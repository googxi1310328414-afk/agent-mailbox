#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-mailbox：多模型协作信箱命令行（主控唯一，助手可增减；带通知、通话、工作队列与主控分配的权限）。

只用 Python 标准库（3.8+），Windows / Linux / macOS / WSL 通用；监视循环不派生子进程。

- 信件是信箱目录下的平铺文件：to-<收件箱>-<线程>-<编号>[-URGENT].md（UTF-8、LF、无 BOM）。
- 控制目录 <信箱>/.agent-mailbox/ 存花名册 config.json、游标、任务、备忘、在线状态、锁和日志。
- 投递：编号锁 + open(..., 'xb') 原子排他创建；编号被占就顺延，绝不覆盖；写后读回核 SHA-256。
- 信头时间由本工具在投递时现取，不靠手写。
- 通知流、通话记录是带序号的 JSONL，持锁取号追加；工作队列整文件持锁读改写；权限写在花名册里，由主控分配。

协议全文见 ../references/protocol.md，命令详解见 ../references/commands.md。
"""
import argparse
import copy
import datetime as dt
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

VERSION = "1.2.1"
SCHEMA = "agent-mailbox/1"
CONTROL = ".agent-mailbox"
SKILL_NAME = "agent-mailbox"
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES = ("controller", "assistant", "former", "observer")
ROLE_ZH = {"controller": "主控", "assistant": "助手", "former": "前任", "observer": "旁听", "user": "用户"}
TASK_STATES = ("open", "waiting", "blocked", "done")

NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
BOX_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
THREAD_RE = re.compile(r"^[0-9a-f]{8}$")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
# 后缀每段须以字母开头（如 -URGENT），否则箱名里的数字段会被误当成编号和后缀
TAG_PART = r"(?P<tag>(?:[-_][A-Za-z][A-Za-z0-9_]*)*)"
LETTER_RE = re.compile(
    r"^to-(?P<box>[a-z0-9]+(?:-[a-z0-9]+)*?)-(?:(?P<thread>[0-9a-f]{8})-)?(?P<num>\d{3,})" + TAG_PART + r"\.md$")
THREADLESS_RE = re.compile(r"^to-(?P<box>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<thread>)(?P<num>\d{3,})" + TAG_PART + r"\.md$")
_THREAD_RES = {}


def parse_letter_name(name, thread=None, threadless=True):
    """解析信件文件名，返回 re.Match 或 None。
    已知线程时箱名取最长、线程按字面匹配，箱名里带日期或数字段（如 agent-main-20260101）也切不错；
    thread=None 时用通用规则（跨线程体检用）；thread 为空串表示本信箱不分线程。"""
    if thread is None:
        return LETTER_RE.match(name)
    if not thread:
        return THREADLESS_RE.match(name)
    rx = _THREAD_RES.get(thread)
    if rx is None:
        rx = _THREAD_RES[thread] = re.compile(r"^to-(?P<box>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<thread>" + re.escape(thread)
                                              + r")-(?P<num>\d{3,})" + TAG_PART + r"\.md$")
    m = rx.match(name)
    if m or not threadless:
        return m
    m = THREADLESS_RE.match(name)
    # 同时列入无线程旧信时，箱名末段是 8 位十六进制的视为别的线程的信
    if m and not re.search(r"-[0-9a-f]{8}$", m.group("box")):
        return m
    return None


def format_letter_name(box, thread, num, tag="", pad=3):
    s = "to-%s-" % box
    if thread:
        s += thread + "-"
    s += str(num).zfill(pad)
    if tag:
        s += "-" + tag
    return s + ".md"
STATUS_RE = re.compile(
    r"^to-(?P<box>[a-z0-9]+(?:-[a-z0-9]+)*?)-(?:(?P<thread>[0-9a-f]{8})-)?"
    r"(?P<key>[A-Z][A-Za-z0-9_-]*?)-STATUS\.json$")
HEADER_LINE_RE = re.compile(r"^(?:发件人|发信方|发件|发信|From)\s*[：:]")
FIELD_RE = re.compile(r"(发件人|发信方|发件|发信|From|收件|抄送|时间|编号|回复|更正)\s*[：:]\s*([^｜|\u3000]*)")
SESSION_TOKEN_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{8}(?![0-9a-f])")
ZHI_RE = re.compile(r"(?:^|[：:｜|]\s*)致\s*(?P<to>[^：:\n]{1,400}?)\s*(?:[：:]|$)")
NAME_SEPARATORS = "、，,；;：:/+＋&｜|"
ARROW_RE = re.compile(r"^(?P<frm>[^→\n]{1,80}?)\s*(?:→|->|⇒|=>)\s*(?P<to>[^：:（(\n]{1,120}?)\s*(?:[：:（(]|\s\d{2,}|$)")
SPLIT_RE = re.compile(r"\s*(?:、|，|,|；|;|＋|\+|&|/|\s和\s|\s与\s|\s及\s)\s*")

ROLE_WORDS = {
    "主控": "@controller", "主控者": "@controller", "controller": "@controller",
    "全体": "@all", "全体成员": "@all", "所有人": "@all", "各位": "@all", "all": "@all", "everyone": "@all",
    "全体助手": "@assistants", "所有助手": "@assistants", "各助手": "@assistants", "assistants": "@assistants",
}
SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}",
    r"Bearer\s+[A-Za-z0-9._~+/=-]{24,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}",
    r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])",
    r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"(?<![A-Za-z0-9])amb_[0-9a-f]{40}(?![0-9a-f])",  # 本工具发的身份令牌
]
RULES = ("处理规则：① 先 read 原信或原件再行动，不凭摘要猜；② 只处理写给本方的事项，点名他人的不碰；"
         "③ 收到登记或返修先发开工回执；④ 写信只用 send（排他编号、现取时间、读回 SHA），通话用 talk say 回复；"
         "⑤ 处理完重挂 watch。")
USER = {"name": "user", "role": "user", "display": "用户", "model": "", "harness": "", "session": "",
        "box": "", "aliases": [], "active": True, "since": "", "until": "", "note": ""}
UTC = dt.timezone.utc


class CliError(Exception):
    def __init__(self, msg, code=1):
        Exception.__init__(self, msg)
        self.code = code


class LockBusy(CliError):
    pass


# ------------------------------------------------------------------ 基础工具
def setup_stdio():
    if os.environ.get("PYTHONIOENCODING"):
        return
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# 输出出口按线程区分：命令行直接写 stdout；MCP 服务在进程内并发调用命令时，各线程写进自己的缓冲区，
# 绝不碰协议用的 stdout，也不读协议用的 stdin。
_TLS = threading.local()


def _out():
    return getattr(_TLS, "out", None) or sys.stdout


def _err():
    return getattr(_TLS, "err", None) or sys.stderr


def emit(text=""):
    o = _out()
    o.write(text + "\n")
    o.flush()


def emit_json(obj):
    emit(json.dumps(obj, ensure_ascii=False, indent=2))


def warn(text):
    e = _err()
    e.write(text + "\n")
    e.flush()


def read_stdin_bytes():
    if getattr(_TLS, "no_stdin", False):
        raise CliError("此处不能从标准输入读正文（MCP 模式下请把内容作为参数传入）", 2)
    return sys.stdin.buffer.read()


def now_utc():
    return dt.datetime.now(UTC)


def iso_utc(t=None):
    return (t or now_utc()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def ts_of(t):
    if isinstance(t, (int, float)):
        return float(t)
    p = parse_iso(t)
    return p.timestamp() if p else None


def parse_offset(s):
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", str(s or ""))
    if not m:
        raise CliError("时区偏移应形如 +08:00：%r" % (s,), 2)
    delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return dt.timezone(delta if m.group(1) == "+" else -delta)


def ago(seconds):
    s = int(max(0, seconds))
    if s < 90:
        return "%d 秒前" % s
    if s < 5400:
        return "%d 分钟前" % round(s / 60.0)
    if s < 172800:
        return "%.1f 小时前" % (s / 3600.0)
    return "%.1f 天前" % (s / 86400.0)


def clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def norm_text(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


ROLE_WORDS_NORM = dict((norm_text(k), v) for k, v in ROLE_WORDS.items())


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha1_text(s):
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def read_head(path, limit=8192):
    with open(path, "rb") as f:
        return f.read(limit)


def slash(p):
    return str(p).replace("\\", "/")


def atomic_write(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for i in range(40):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows 上读者未带共享删除标志打开目标文件时会短暂失败，稍等重试
                if i == 39:
                    raise
                time.sleep(0.025 * (i + 1))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_bytes_retry(path, tries=40):
    """读整个文件；不存在返回 None。Windows 上别的进程正在原子替换它时会短暂拒绝访问，稍等重试。"""
    for i in range(tries):
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except PermissionError:
            if i == tries - 1:
                raise CliError("读不了 %s（一直被占用）" % path)
            time.sleep(0.025 * (i + 1))
    return None


def load_json(path, default=None):
    raw = read_bytes_retry(path)
    if raw is None:
        return default
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CliError("JSON 文件损坏：%s（%s）" % (path, e))


def load_json_quiet(path):
    try:
        return load_json(path, None)
    except (CliError, OSError):
        return None


def read_json_strict(path):
    """读状态 JSON：不存在返回 None；损坏就报错，绝不当成空文件（否则会把已读记录、任务清单整个覆盖掉）。"""
    raw = read_bytes_retry(path)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CliError("状态文件损坏：%s（%s）；请先改名备份再继续，不要直接删" % (path, e))


def unlink_retry(path, tries=40):
    """删除文件；Windows 上有读者开着它时会拒绝删除，稍等重试。删不掉返回 False。"""
    for i in range(tries):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(0.01 * (i + 1))
        except OSError:
            return False
    return False


def dump_json(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def append_line(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def host_key():
    plat = sys.platform
    if plat.startswith("linux"):
        try:
            with open("/proc/sys/kernel/osrelease") as f:
                if "microsoft" in f.read().lower():
                    # 各 WSL 发行版主机名相同但进程号互不相通，要带上发行版名区分
                    plat = "wsl:" + (os.environ.get("WSL_DISTRO_NAME") or "?")
        except OSError:
            pass
    return "%s|%s" % (socket.gethostname(), plat)


HOST = host_key()


def pid_alive(pid):
    """不发信号地判断进程是否存活。Windows 上 os.kill(pid, 0) 会直接终止目标进程，绝不能用。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # 拒绝访问说明进程存在
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class FileLock(object):
    """O_EXCL 锁文件；持有进程已死或超过 stale 秒视为陈旧。
    锁只用来缩小并发窗口；不覆盖的根本保证是信件文件本身的排他创建。"""

    def __init__(self, path, stale=10.0, timeout=20.0):
        self.path, self.stale, self.timeout = path, stale, timeout
        self.token = secrets.token_hex(8)
        self.held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        info = json.dumps({"pid": os.getpid(), "host": HOST, "token": self.token, "at": iso_utc()}).encode("utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
            except FileExistsError:
                if self._clear_if_stale():
                    continue
                if time.monotonic() > deadline:
                    raise LockBusy("锁被占用超时：%s" % self.path)
                time.sleep(0.05)
                continue
            except PermissionError:
                # Windows 上删除中的文件会短暂拒绝创建
                if time.monotonic() > deadline:
                    raise LockBusy("锁文件不可创建：%s" % self.path)
                time.sleep(0.05)
                continue
            try:
                os.write(fd, info)
            finally:
                os.close(fd)
            self.held = True
            return

    def _clear_if_stale(self):
        # 先只看年龄：os.stat 不会挡住持有者删除；正常持有只有毫秒级，2 秒内的锁不去打开它
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        age = time.time() - st.st_mtime
        if age <= 2.0:
            return False
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            holder = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            holder = {}
        dead = bool(holder) and holder.get("host") == HOST and not pid_alive(holder.get("pid"))
        if holder and age <= self.stale and not dead:
            return False
        try:
            st2 = os.stat(self.path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if (st2.st_mtime, st2.st_size) != (st.st_mtime, st.st_size):
            return False  # 期间被别人换过
        return unlink_retry(self.path, tries=5)

    def release(self):
        if not self.held:
            return
        self.held = False
        raw = None
        try:
            raw = read_bytes_retry(self.path, tries=20)
        except CliError:
            pass
        try:
            holder = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            holder = {}
        if holder.get("token") == self.token:
            unlink_retry(self.path)


# ------------------------------------------------------------------ 花名册
def default_config(thread=""):
    return {
        "schema": SCHEMA,
        "revision": 0,
        "updated_at": iso_utc(),
        "thread": thread or "",
        "timezone": "+08:00",
        "timezone_label": "北京",
        "controller_box": "controller",
        "broadcast_box": "all",
        "include_threadless": False,
        "max_assistants": 8,
        "max_bytes": 65536,
        "max_tries": 50,
        "pad": 3,
        "policy": {"assistant_to_assistant": "allow", "broadcast": "controller", "inactive_send": "deny"},
        "secret_patterns": [],
        "participants": [],
    }


def norm_participant(p):
    q = dict(p)
    q["name"] = str(q.get("name") or "")
    q["role"] = str(q.get("role") or "")
    q["display"] = str(q.get("display") or q["name"])
    q["aliases"] = [str(x) for x in (q.get("aliases") or []) if str(x).strip()]
    if q.get("active") is None:
        q["active"] = q["role"] in ("controller", "assistant", "observer")
    q["active"] = bool(q["active"])
    for k in ("model", "harness", "session", "box", "since", "until", "note"):
        q[k] = str(q.get(k) or "")
    for k in ("allow", "deny"):
        v = q.get(k)
        if v is None:
            q[k] = []
        elif isinstance(v, list):
            q[k] = [str(x).strip() for x in v if str(x).strip()]
        # 别的类型原样留着，交给 validate_config 报错
    return q


def validate_config(cfg):
    errors, warns = [], []
    if cfg.get("schema") != SCHEMA:
        errors.append("schema 应为 %s，实际 %r" % (SCHEMA, cfg.get("schema")))
    th = cfg.get("thread") or ""
    if th and not THREAD_RE.match(th):
        errors.append("thread 须为 8 位小写十六进制（或留空）：%r" % th)
    for k in ("controller_box", "broadcast_box"):
        if not BOX_RE.match(str(cfg.get(k) or "")):
            errors.append("%s 不是合法收件箱名（小写字母数字，可用 - 连接）：%r" % (k, cfg.get(k)))
    if cfg.get("controller_box") == cfg.get("broadcast_box"):
        errors.append("controller_box 与 broadcast_box 不能相同")
    try:
        parse_offset(cfg.get("timezone"))
    except CliError as e:
        errors.append(str(e))
    for k, lo in (("max_bytes", 1024), ("max_tries", 1), ("pad", 1)):
        v = cfg.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or v < lo:
            errors.append("%s 须为不小于 %d 的整数：%r" % (k, lo, v))
    mx = cfg.get("max_assistants")
    if not isinstance(mx, int) or isinstance(mx, bool) or mx < 0:
        errors.append("max_assistants 须为非负整数：%r" % (mx,))
    pol = cfg.get("policy") or {}
    if pol.get("assistant_to_assistant") not in ("allow", "cc-controller", "deny"):
        errors.append("policy.assistant_to_assistant 只能是 allow / cc-controller / deny")
    if pol.get("broadcast") not in ("controller", "anyone"):
        errors.append("policy.broadcast 只能是 controller / anyone")
    if pol.get("inactive_send") not in ("deny", "allow"):
        errors.append("policy.inactive_send 只能是 deny / allow")
    for pat in cfg.get("secret_patterns") or []:
        try:
            re.compile(pat)
        except re.error as e:
            errors.append("secret_patterns 里的正则无效：%r（%s）" % (pat, e))
    ps = cfg.get("participants") or []
    names = set()
    alias_owner = {}
    for p in sorted(ps, key=lambda x: not x.get("active")):
        n = p.get("name", "")
        if not NAME_RE.match(n) or n == "user":
            errors.append("参与方名不合法：%r（小写字母、数字、. _ -，且不能叫 user）" % n)
        if n in names:
            errors.append("参与方重名：%s" % n)
        names.add(n)
        role = p.get("role")
        if role not in ROLES:
            errors.append("%s 的角色不合法：%r（只能是 %s）" % (n, role, " / ".join(ROLES)))
        if role == "former" and p.get("active"):
            errors.append("%s 是 former（前任），不能同时在任" % n)
        if role == "controller" and not p.get("active"):
            errors.append("%s 的角色是 controller 却不在任（卸任请改为 former）" % n)
        box = p.get("box") or ""
        if box and not BOX_RE.match(box):
            errors.append("%s 的收件箱名不合法：%r" % (n, box))
        if box and box in (cfg.get("controller_box"), cfg.get("broadcast_box")):
            errors.append("%s 的个人收件箱不能与主控箱或广播箱同名：%s" % (n, box))
        if p.get("active") and role == "assistant" and not box:
            errors.append("在任助手 %s 没有收件箱（box）" % n)
        for a in [n, p.get("display")] + list(p.get("aliases") or []):
            k = norm_text(a)
            if not k:
                continue
            if any(c in k for c in NAME_SEPARATORS) or k.startswith("致"):
                errors.append("%s 的显示名或别名 %r 含分隔符（%s）或以「致」开头，标题会解析不回来" % (
                    n, a, " ".join(NAME_SEPARATORS)))
                continue
            if k in ROLE_WORDS_NORM:
                warns.append("%s 的别名 %r 与角色词同名，路由时按角色词处理" % (n, a))
                continue
            owner = alias_owner.get(k)
            if owner is None:
                alias_owner[k] = (n, bool(p.get("active")))
            elif owner[0] != n:
                if owner[1] and p.get("active"):
                    errors.append("别名 %r 同时属于在任的 %s 和 %s" % (a, owner[0], n))
                else:
                    warns.append("别名 %r 同时属于 %s 和 %s，按在任者优先" % (a, owner[0], n))
    pad = cfg.get("pad") if isinstance(cfg.get("pad"), int) and cfg.get("pad") > 0 else 3
    boxes = set([cfg.get("controller_box"), cfg.get("broadcast_box")] + [p.get("box") for p in ps if p.get("box")])
    for b in sorted(x for x in boxes if x and BOX_RE.match(str(x))):
        for tag in ("", "URGENT"):
            nm = format_letter_name(b, th, 1, tag, pad)
            m = parse_letter_name(nm, th, bool(cfg.get("include_threadless")))
            got = (m.group("box"), m.group("thread") or "", int(m.group("num")), (m.group("tag") or "").lstrip("-_")) if m else None
            if got != (b, th, 1, tag):
                errors.append("收件箱名 %s 与线程号组合后无法无歧义解析（%s），请换个箱名" % (b, nm))
                break
    for p in ps:
        for k in ("allow", "deny"):
            v = p.get(k)
            if v is None:
                continue
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errors.append("%s 的 %s 须是权限字符串的数组" % (p.get("name"), k))
                continue
            bad = [x for x in v if parse_perm(x) is None]
            if bad:
                warns.append("%s 的 %s 里有看不懂的权限 %s，已忽略" % (p.get("name"), k, "、".join(map(repr, bad))))
        tok = p.get("token_sha256")
        if tok and not re.match(r"^[0-9a-f]{64}$", str(tok)):
            errors.append("%s 的 token_sha256 须是 64 位小写十六进制" % p.get("name"))
    utok = cfg.get("user_token_sha256")
    if utok and not re.match(r"^[0-9a-f]{64}$", str(utok)):
        errors.append("user_token_sha256 须是 64 位小写十六进制")
    presets = cfg.get("presets")
    if presets is not None:
        if not isinstance(presets, dict):
            errors.append("presets 须是对象：{\"assistant\": [...], \"observer\": [...]}")
        else:
            for role, v in presets.items():
                if role not in PRESET_ROLES:
                    errors.append("presets 只能给 %s 设：%r" % (" / ".join(PRESET_ROLES), role))
                elif not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    errors.append("presets.%s 须是权限字符串的数组" % role)
                else:
                    bad = [x for x in v if parse_perm(x) is None]
                    if bad:
                        warns.append("presets.%s 里有看不懂的权限 %s，已忽略" % (role, "、".join(map(repr, bad))))
    ctrls = [p.get("name") for p in ps if p.get("role") == "controller" and p.get("active")]
    if len(ctrls) != 1:
        errors.append("必须恰有一名在任主控，当前 %d 名：%s" % (len(ctrls), "、".join(ctrls) or "无"))
    assistants = [p for p in ps if p.get("role") == "assistant" and p.get("active")]
    if isinstance(mx, int) and mx >= 0 and len(assistants) > mx:
        errors.append("在任助手 %d 名，超过上限 max_assistants=%d" % (len(assistants), mx))
    return errors, warns


# ------------------------------------------------------------------ 权限
# 权限写作「键[:范围]」。范围可以是参与方名、角色组（@controller、@assistants、@observers）或队列名；
# 不写范围或写 * 表示不限。主控与用户永远拥有全部权限；其余人 = 角色预设 + 主控的显式授予与收回。
# 判定：显式条目里取最具体的一条（点名 3 > 角色组 2 > 不限 1），同样具体时收回优先；没有显式条目才看角色预设。
PERM_CATALOG = (
    ("send", "who", "写信"),
    ("broadcast", "", "广播：写信或发通知给全体"),
    ("notify", "who", "发通知"),
    ("talk", "who", "通话：发起、邀请、发言（发言要对通话里每个在任成员都有）"),
    ("talk.audit", "", "旁听：读任何通话"),
    ("queue.push", "queue", "向队列投任务"),
    ("queue.take", "queue", "从队列领任务"),
    ("queue.admin", "queue", "建队列、改设置、代他人处置任务"),
    ("status", "", "写状态板"),
    ("roster.admin", "", "代管花名册：增减助手、审批加入申请（不含交接主控、改策略和授权）"),
)
PERM_SCOPE = dict((k, s) for k, s, _ in PERM_CATALOG)
PERM_ZH = dict((k, d) for k, _, d in PERM_CATALOG)
ROLE_SCOPES = {"@controller": "controller", "@assistants": "assistant", "@observers": "observer"}
PRESET_ROLES = ("assistant", "observer")
PERM_RE = re.compile(r"^(?P<key>\*|[a-z]+(?:\.[a-z]+)?)(?::(?P<scope>\*|@[a-z]+|[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?))?$")


def parse_perm(s):
    """把「键[:范围]」拆成（键，范围）；范围 None 表示不限。不合法或不认识返回 None。"""
    m = PERM_RE.match(str(s or "").strip())
    if not m:
        return None
    key, scope = m.group("key"), m.group("scope")
    if scope == "*":
        scope = None
    if key == "*":
        return (key, None) if scope is None else None
    kind = PERM_SCOPE.get(key)
    if kind is None:
        return None
    if scope is None:
        return key, None
    if kind == "":
        return None  # 这类权限不分范围
    if kind == "queue":
        return (key, scope) if BOX_RE.match(scope) else None
    if scope.startswith("@") and scope not in ROLE_SCOPES:
        return None
    return key, scope


def perm_text(key, scope):
    return key if scope is None else "%s:%s" % (key, scope)


def role_preset(cfg, role):
    """角色预设。主控可在花名册 presets 里整体改写；没改写时由旧的通信策略推出，与 1.0 的行为一致。"""
    custom = (cfg.get("presets") or {}).get(role)
    if isinstance(custom, list):
        return [str(x) for x in custom]
    pol = cfg.get("policy") or {}
    if role == "assistant":
        if pol.get("assistant_to_assistant") == "deny":
            out = ["%s:%s" % (k, s) for k in ("send", "notify", "talk") for s in ("@controller", "@observers")]
        else:
            out = ["send", "notify", "talk"]
        out += ["queue.push", "queue.take", "status"]
        if pol.get("broadcast") == "anyone":
            out.append("broadcast")
        return out
    if role == "observer":
        return ["talk.audit"]
    return []


def perm_match(entry, key, target):
    """entry 对（key，target）的具体度：0 不覆盖，1 不限范围，2 角色组，3 点名。
    target 是参与方（dict）、队列名（str）或 None（不针对任何人：带范围的条目一律不覆盖）。"""
    pe = parse_perm(entry)
    if pe is None:
        return 0
    k, scope = pe
    if k != key and k != "*":
        return 0
    if scope is None:
        return 1
    if target is None:
        return 0
    if isinstance(target, dict):
        if scope == target.get("name"):
            return 3
        role = ROLE_SCOPES.get(scope)
        if role and target.get("role") == role and target.get("active"):
            return 2
        return 0
    return 3 if scope == str(target) else 0


class Router(object):
    """把标题、信头里的称呼解析成参与方名：在任者优先，最长匹配优先，会话号兜底。"""

    def __init__(self, cfg):
        table = {}
        ordered = sorted(cfg["participants"], key=lambda p: not p["active"])
        for p in ordered:
            for a in [p["name"], p["display"]] + list(p["aliases"]):
                k = norm_text(a)
                if k and k not in ROLE_WORDS_NORM and k not in table:
                    table[k] = p["name"]
        for k, v in ROLE_WORDS_NORM.items():
            table.setdefault(k, v)
        self.entries = sorted(table.items(), key=lambda kv: -len(kv[0]))
        self.sessions = {}
        for p in ordered:
            s = (p.get("session") or "").lower()
            if len(s) >= 8 and re.match(r"^[0-9a-f]{8}", s):
                self.sessions.setdefault(s[:8], p["name"])

    def resolve(self, text):
        t = norm_text(text)
        if not t:
            return None
        for k, v in self.entries:
            if t.startswith(k) and (len(t) == len(k) or not (t[len(k)].isascii() and t[len(k)].isalnum())):
                return v
        for tok in SESSION_TOKEN_RE.findall(t):
            if tok in self.sessions:
                return self.sessions[tok]
        return None

    def exact(self, text):
        t = norm_text(text)
        for k, v in self.entries:
            if k == t:
                return v
        return None


def title_body(line):
    return (line or "").strip().lstrip("#").strip()


def title_parties(title):
    """从标题取（收件称呼列表，发件称呼）。支持「# 致 X：…」和旧式「# A → B …」。"""
    s = title_body(title)
    m = ZHI_RE.search(s)
    if m:
        frm = s[:m.start()].strip(" ：:｜|") if m.start() > 0 else ""
        return [x for x in SPLIT_RE.split(m.group("to")) if x.strip()], frm
    m = ARROW_RE.match(s)
    if m:
        return [x for x in SPLIT_RE.split(m.group("to")) if x.strip()], m.group("frm").strip()
    # 旧式「# Codex主控：…」「# Grok 助手 511：…」：冒号或编号前的前缀当作发件称呼（解析不出就留空）
    m = re.match(r"^([^：:（(\d]{1,40}?)\s*(?:[：:（(]|\s\d)", s)
    return [], (m.group(1).strip() if m else "")


def parse_head(raw):
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    nonempty = [l.strip() for l in text.split("\n") if l.strip()]
    title = ""
    for l in nonempty[:3]:
        if l.startswith("#"):
            title = l
            break
    if not title and nonempty:
        title = nonempty[0]
    header = ""
    for l in nonempty[:5]:
        if HEADER_LINE_RE.match(l):
            header = l
            break
    fields = {}
    for m in FIELD_RE.finditer(header):
        key = m.group(1)
        if key in ("发件人", "发信方", "发信", "From"):
            key = "发件"
        fields.setdefault(key, m.group(2).strip())
    to_raw, frm_title = title_parties(title)
    frm_raw = fields.get("发件", "") or frm_title
    m = re.match(r"^([^\s（(，,；;｜|。]+)", frm_raw)
    return {"title": clip(title, 300), "header": clip(header, 600), "fields": fields, "to_raw": to_raw,
            "from_raw": clip(frm_raw, 200), "from_token": clip(m.group(1) if m else frm_raw, 120)}


class Letter(object):
    __slots__ = ("name", "path", "box", "thread", "num", "tag", "urgent")

    def __init__(self, root, name, m):
        self.name = name
        self.path = os.path.join(root, name)
        self.box = m.group("box")
        self.thread = m.group("thread") or ""
        self.num = int(m.group("num"))
        self.tag = (m.group("tag") or "").lstrip("-_")
        self.urgent = "URGENT" in self.tag.upper()

    @property
    def ref(self):
        r = "%s-%d" % (self.box, self.num)
        if self.tag:
            r += "-" + self.tag
        return r if self.thread else r + "（无线程旧信）"


# ------------------------------------------------------------------ 信箱
def env_token():
    """身份令牌：环境变量 AGENT_MAILBOX_TOKEN，或 AGENT_MAILBOX_TOKEN_FILE 指向的文件（只取第一行）。"""
    t = (os.environ.get("AGENT_MAILBOX_TOKEN") or "").strip()
    if t:
        return t
    f = os.environ.get("AGENT_MAILBOX_TOKEN_FILE")
    return read_token_file(f) if f else ""


def read_token_file(path):
    raw = read_bytes_retry(os.path.abspath(os.path.expanduser(path)))
    if raw is None:
        raise CliError("找不到令牌文件：%s" % path, 2)
    lines = raw.decode("utf-8-sig", "replace").strip().splitlines()
    return lines[0].strip() if lines else ""


def token_hash(tok):
    return hashlib.sha256(str(tok).strip().encode("utf-8")).hexdigest()


class Mailbox(object):
    def __init__(self, root, config_path=None, state_dir=None, read_only=False, token=None):
        self.root = os.path.abspath(root)
        self.ctl = os.path.abspath(state_dir) if state_dir else os.path.join(self.root, CONTROL)
        self.config_path = os.path.abspath(config_path) if config_path else os.path.join(self.ctl, "config.json")
        self.read_only = bool(read_only)
        self.token = token
        self._cfg = None
        self._router = None
        self._cache = {}
        self.config_warnings = []
        self.config_errors = []

    def p(self, *parts):
        return os.path.join(self.ctl, *parts)

    def require_writable(self):
        if self.read_only:
            raise CliError("当前是只读模式（--read-only），不写任何文件", 2)

    def load_config(self, strict=True):
        raw = load_json(self.config_path)
        if raw is None:
            raise CliError("没有花名册：%s\n先用 init 初始化，或用 --config 指定花名册文件" % self.config_path, 2)
        base = default_config(raw.get("thread"))
        cfg = dict(base)
        cfg.update(raw)
        cfg["policy"] = dict(base["policy"], **(raw.get("policy") or {}))
        cfg["participants"] = [norm_participant(p) for p in raw.get("participants") or []]
        errors, warns = validate_config(cfg)
        self.config_errors, self.config_warnings = errors, warns
        if errors and strict:
            raise CliError("花名册无效（%s）：\n  - %s" % (self.config_path, "\n  - ".join(errors)), 2)
        return cfg

    @property
    def cfg(self):
        if self._cfg is None:
            self._cfg = self.load_config()
        return self._cfg

    @property
    def router(self):
        if self._router is None:
            self._router = Router(self.cfg)
        return self._router

    def reset(self):
        self._cfg = None
        self._router = None
        self._cache = {}

    @property
    def thread(self):
        return self.cfg.get("thread") or ""

    def tz(self):
        return parse_offset(self.cfg.get("timezone"))

    def fmt_time(self, epoch, with_date=True):
        t = dt.datetime.fromtimestamp(epoch, self.tz())
        return t.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")

    # 参与方
    def participants(self, active=None):
        ps = self.cfg["participants"]
        return list(ps) if active is None else [p for p in ps if p["active"] == active]

    def get(self, name):
        for p in self.cfg["participants"]:
            if p["name"] == name:
                return p
        return None

    def controller(self):
        for p in self.cfg["participants"]:
            if p["role"] == "controller" and p["active"]:
                return p
        raise CliError("花名册里没有在任主控", 2)

    def roster_names(self):
        return "、".join(p["name"] for p in self.participants(True)) or "（无）"

    def find(self, text):
        """按名称、显示名、别名（整串）或角色词找参与方；返回参与方或 @all / @assistants。"""
        v = self.router.exact(text)
        if v is None:
            raise CliError("找不到参与方 %r。在任参与方：%s" % (text, self.roster_names()), 2)
        if v == "@controller":
            return self.controller()
        if v.startswith("@"):
            return v
        return self.get(v)

    def lookup(self, text):
        """按名称、显示名、别名或角色词「主控」找一个具体的参与方（不收「全体」「全体助手」）。"""
        v = self.find(text)
        if isinstance(v, str):
            raise CliError("这里要写一个具体的参与方，不能是 %s" % text, 2)
        return v

    def identity(self, name, need_active=True):
        if not name:
            raise CliError("没有本方身份：加 --as <参与方名>，或设环境变量 AGENT_MAILBOX_ME", 2)
        if name == "user":
            self.check_token(USER)
            return USER
        p = self.get(name)
        if p is None:
            v = self.router.exact(name)
            if v == "@controller":
                p = self.controller()
            elif v and not v.startswith("@"):
                p = self.get(v)
        if p is None:
            raise CliError("花名册里没有 %r。在任参与方：%s" % (name, self.roster_names()), 2)
        if need_active and not p["active"]:
            raise CliError("%s 已不在任（%s）" % (p["name"], ROLE_ZH.get(p["role"], p["role"])), 2)
        self.check_token(p)
        return p

    def check_token(self, p):
        """花名册给这个身份设了令牌时，要求调用方出示同一令牌（防止用 --as 冒充主控来绕开权限）。"""
        want = self.cfg.get("user_token_sha256") if p is USER else p.get("token_sha256")
        if not want:
            return
        tok = self.token if self.token is not None else env_token()
        if not tok or not hmac.compare_digest(token_hash(tok), str(want)):
            raise CliError("身份 %s 设有令牌：请用环境变量 AGENT_MAILBOX_TOKEN（或 AGENT_MAILBOX_TOKEN_FILE、--token-file）"
                           "提供本人的令牌；没有令牌就不能以 %s 的身份操作" % (p["name"], p["name"]), 2)

    def require_controller(self, me):
        if me is USER:
            return
        c = self.controller()
        if me["name"] != c["name"]:
            raise CliError("只有主控（%s）或用户（--as user）能做这项操作；你是 %s" % (c["name"], me["name"]), 2)

    # 权限
    def perm_decide(self, p, key, target=None):
        """返回（是否允许，依据）。"""
        if p is USER:
            return True, "用户"
        if p["role"] == "controller" and p["active"]:
            return True, "主控"
        if isinstance(target, dict) and target.get("name") == "user":
            return True, "对用户（人）总是可以"
        pol = self.cfg["policy"]
        if not p["active"]:
            ok = key == "send" and pol.get("inactive_send") == "allow"
            return ok, "不在任（policy.inactive_send=%s）" % pol.get("inactive_send")
        best = None
        for entries, deny in ((p.get("deny") or [], True), (p.get("allow") or [], False)):
            for e in entries:
                s = perm_match(e, key, target)
                if s and (best is None or s > best[0] or (s == best[0] and deny and not best[1])):
                    best = (s, deny, e)
        if best:
            return (not best[1]), "主控%s %s" % ("收回" if best[1] else "授予", best[2])
        for e in role_preset(self.cfg, p["role"]):
            if perm_match(e, key, target):
                return True, "%s预设 %s" % (ROLE_ZH.get(p["role"], p["role"]), e)
        return False, "%s预设里没有" % ROLE_ZH.get(p["role"], p["role"])

    def can(self, p, key, target=None):
        return self.perm_decide(p, key, target)[0]

    def perm_error(self, p, key, target=None, why=None):
        why = why or self.perm_decide(p, key, target)[1]
        if isinstance(target, dict):
            tname, want = target["name"], "%s:%s" % (key, target["name"])
        elif target:
            tname, want = "队列 %s" % target, "%s:%s" % (key, target)
        else:
            tname, want = "", key
        msg = "权限不足：%s 不能%s用 %s（%s，%s）" % (
            p["name"], ("对 %s " % tname) if tname else "", key, PERM_ZH.get(key, key), why)
        pol = self.cfg["policy"]
        if (key in ("send", "notify", "talk") and isinstance(target, dict) and target.get("role") == "assistant"
                and p.get("role") == "assistant" and "预设" in why and pol.get("assistant_to_assistant") == "deny"):
            msg += "；按策略助手之间不直接通信（policy.assistant_to_assistant=deny），请经主控转达"
        if key == "broadcast" and "预设" in why and pol.get("broadcast") == "controller":
            msg += "；按策略只有主控能广播（policy.broadcast=controller）"
        return CliError(msg + "；确有需要请主控执行 perm grant %s %s" % (p["name"], want), 2)

    def require_perm(self, p, key, target=None):
        ok, why = self.perm_decide(p, key, target)
        if not ok:
            raise self.perm_error(p, key, target, why)

    def require_roster_admin(self, me):
        if me is USER or (me["role"] == "controller" and me["active"]):
            return
        if not self.can(me, "roster.admin"):
            c = self.controller()
            raise CliError("只有主控（%s）、用户（--as user）或有 roster.admin 权限的人能改花名册；你是 %s" % (
                c["name"], me["name"]), 2)

    def perm_summary(self, p):
        """（预设，显式授予，显式收回）三个列表，供展示。"""
        if p is USER or (p["role"] == "controller" and p["active"]):
            return ["*"], [], []
        if not p["active"]:
            return (["send"] if self.cfg["policy"].get("inactive_send") == "allow" else []), [], []
        return role_preset(self.cfg, p["role"]), list(p.get("allow") or []), list(p.get("deny") or [])

    def boxes_of(self, p):
        if p is USER:
            return []
        out = []
        if p["role"] == "controller" and p["active"]:
            out.append(self.cfg["controller_box"])
        if p.get("box") and p["box"] not in out:
            out.append(p["box"])
        if p["active"]:
            out.append(self.cfg["broadcast_box"])
        return out

    def box_of_target(self, p):
        if p["role"] == "controller" and p["active"]:
            return self.cfg["controller_box"]
        return p.get("box") or ""

    def members(self, box):
        return [p["name"] for p in self.participants(True) if box in self.boxes_of(p)]

    # 信件
    def scan(self, thread="__cfg__"):
        if thread == "__cfg__":
            thread = self.thread
            threadless = bool(self.cfg.get("include_threadless"))
        else:
            threadless = True
        try:
            names = os.listdir(self.root)
        except OSError as e:
            raise CliError("读不了信箱目录：%s（%s）" % (self.root, e))
        letters, boards, others = [], [], []
        for n in names:
            m = parse_letter_name(n, thread, threadless)
            if m:
                t = m.group("thread") or ""
                if thread is None or t == thread or (threadless and not t):
                    letters.append(Letter(self.root, n, m))
                continue
            if thread and LETTER_RE.match(n):
                continue  # 别的线程的信
            m = STATUS_RE.match(n)
            if m:
                if thread is None or (m.group("thread") or "") == thread:
                    boards.append((n, m))
                continue
            if n != CONTROL:
                others.append(n)
        letters.sort(key=lambda l: (l.box, l.thread, l.num, l.name))
        return letters, boards, others

    def info(self, letter):
        try:
            st = os.stat(letter.path)
        except OSError:
            return None
        key = (st.st_size, st.st_mtime_ns)
        hit = self._cache.get(letter.name)
        if hit and hit[0] == key:
            return hit[1]
        try:
            raw = read_head(letter.path, 8192)
        except OSError:
            return None
        h = parse_head(raw)
        h["size"] = st.st_size
        h["mtime"] = st.st_mtime
        frm = self.router.resolve(h["from_token"]) if h["from_token"] else None
        if frm is None and h["from_raw"]:
            frm = self.router.resolve(h["from_raw"])
        h["from"] = frm or ""
        targets = []
        # 本工具投的信在信头「收件：」里写的是参与方名，按它路由最可靠；旧信才回退到解析标题
        listed = [x for x in re.split(r"[、,，]", h["fields"].get("收件", "")) if x.strip()]
        if listed:
            exact = [self.router.exact(x) for x in listed]
            if all(exact):
                targets = list(dict.fromkeys(exact))
        if not targets:
            for x in h["to_raw"]:
                v = self.router.resolve(x)
                if v and v not in targets:
                    targets.append(v)
        h["to"] = targets
        self._cache[letter.name] = (key, h)
        return h

    def classify(self, me, letter, h):
        """对收件人 me：mine（写给我）、all（广播）、shared（共享箱里没点名）、other（点名给别人）。
        主控箱按角色归属：在任主控收主控箱的全部来信（含写给前任主控的）。"""
        to = set((h or {}).get("to") or [])
        hit = (me["name"] in to or "@all" in to or ("@assistants" in to and me["role"] == "assistant")
               or ("@controller" in to and me["role"] == "controller"))
        if letter.box == self.cfg["controller_box"] and me["role"] == "controller" and me["active"]:
            return "mine"
        if letter.box == self.cfg["broadcast_box"]:
            if not to or "@all" in to:
                return "all"
            return "mine" if hit else "other"
        if hit:
            return "mine"
        if to:
            return "other"
        return "shared" if len(self.members(letter.box)) > 1 else "mine"

    def describe(self, letter, h, cls=None, path=True):
        tag = {"shared": "[共享]", "all": "[广播]", "other": "[他人]", "self": "[自己]"}.get(cls, "")
        frm = h.get("from") or h.get("from_token") or "?"
        return "%s%s%s  %s  %dB  发件 %s  「%s」%s" % (
            "[急]" if letter.urgent else "", tag, letter.ref, self.fmt_time(h["mtime"]), h["size"], frm,
            clip(h["title"], 140), ("  " + slash(letter.path)) if path else "")

    def resolve_ref(self, ref, letters, me=None):
        ref = ref.strip()
        base = os.path.basename(ref)
        for l in letters:
            if l.name == base or l.name == base + ".md":
                return l
        m = re.match(r"^(?:(?P<box>[a-z0-9]+(?:-[a-z0-9]+)*)-)?(?P<num>\d+)(?:-(?P<tag>[A-Za-z0-9_]+))?$", ref)
        if not m:
            raise CliError("看不懂的信件引用：%r（可写编号 941、箱-编号 claude-941 或完整文件名）" % ref, 2)
        num, box, tag = int(m.group("num")), m.group("box"), m.group("tag")
        cands = [l for l in letters if l.num == num and (box is None or l.box == box)]
        if tag:
            cands = [l for l in cands if l.tag.upper() == tag.upper()]
        if box is None and me is not None and me is not USER and len(cands) > 1:
            mine = [l for l in cands if l.box in self.boxes_of(me)]
            if mine:
                cands = mine
        if len(cands) > 1:
            threaded = [l for l in cands if l.thread]
            if len(threaded) == 1:
                cands = threaded
        if not cands:
            raise CliError("本线程里找不到信件 %r" % ref, 2)
        if len(cands) > 1:
            raise CliError("信件引用 %r 有歧义：%s；请写成 箱-编号[-后缀] 或完整文件名" % (
                ref, "、".join(l.name for l in cands)), 2)
        return cands[0]

    # 编号与投递
    def fmt_name(self, box, thread, num, tag=""):
        return format_letter_name(box, thread, num, tag, int(self.cfg.get("pad", 3)))

    def same_number(self, box, thread, num=None):
        out = {}
        for n in os.listdir(self.root):
            m = parse_letter_name(n, thread or "", threadless=False)
            if m and m.group("box") == box:
                k = int(m.group("num"))
                if num is None or k == num:
                    out.setdefault(k, []).append(n)
        return out

    def _rank(self, name):
        m = parse_letter_name(name, self.thread, threadless=False)
        return (1 if (m and m.group("tag")) else 0, name)

    def deliver(self, box, render, tag="", start=None):
        """在 box 里排他投递一封信。render(num, filename) 返回最终字节。"""
        self.require_writable()
        thread = self.thread
        tries = int(self.cfg.get("max_tries", 50))
        notes = []
        lock = FileLock(self.p("locks", "send-%s-%s.lock" % (box, thread or "none")))
        try:
            lock.acquire()
        except LockBusy:
            notes.append("编号锁超时，本次只靠排他创建防覆盖")
            lock = None
        except OSError as e:
            notes.append("编号锁不可用（%s），本次只靠排他创建防覆盖" % e)
            lock = None
        try:
            taken = set(self.same_number(box, thread).keys())
            n = (max(taken) + 1) if taken else 1
            if start:
                n = max(int(start), 1)
            attempts = 0
            while attempts < tries:
                while n in taken:
                    n += 1
                name = self.fmt_name(box, thread, n, tag)
                path = os.path.join(self.root, name)
                data = render(n, name)
                try:
                    with open(path, "xb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                except FileExistsError:
                    attempts += 1
                    taken.add(n)
                    taken |= set(self.same_number(box, thread).keys())
                    continue
                rivals = [x for x in self.same_number(box, thread, n).get(n, []) if x != name]
                if rivals and not all(self._rank(name) < self._rank(r) for r in rivals):
                    with open(path, "rb") as f:
                        back = f.read()
                    if back == data and unlink_retry(path):
                        notes.append("编号 %d 与 %s 同号（不同后缀），本信让号顺延" % (n, "、".join(rivals)))
                        attempts += 1
                        taken.add(n)
                        continue
                    notes.append("编号 %d 与 %s 同号；本信%s，保留原样待查" % (
                        n, "、".join(rivals), "读回不一致" if back != data else "被占用删不掉"))
                elif rivals:
                    notes.append("编号 %d 另有 %s；按规则本信保留，对方应让号" % (n, "、".join(rivals)))
                with open(path, "rb") as f:
                    back = f.read()
                return {"file": name, "path": slash(path), "box": box, "number": n, "bytes": len(back),
                        "sha256": sha256_bytes(back), "readbackEqual": back == data, "notes": notes}
            raise CliError("连试 %d 个编号都被占用，没有投递" % tries)
        finally:
            if lock:
                lock.release()

    # 游标（每个参与方的已提醒/已读）
    def cursor_path(self, name):
        return self.p("cursors", name + ".json")

    def update_cursor(self, name, fn):
        self.require_writable()
        with FileLock(self.p("locks", "cursor-%s.lock" % name)):
            d = read_json_strict(self.cursor_path(name)) or {}
            fn(d)
            d["name"] = name
            d["updated_at"] = iso_utc()
            atomic_write(self.cursor_path(name), dump_json(d))
            return d

    def state(self, me, letters_in_scope):
        """读游标；第一次使用时建立基线：参与方加入（since）之前的信视为已读，之后的保留为未读。
        判断时留 120 秒余量：宁可多报几封加入前后的信，也不因各机器时钟偏差漏掉登记信。"""
        d = read_json_strict(self.cursor_path(me["name"]))
        if d is not None and d.get("baseline_at"):
            return d
        base = self.baseline_names(me, letters_in_scope)
        fresh = {"name": me["name"], "notified": sorted(base), "read": sorted(base), "baseline_at": iso_utc()}
        if self.read_only:
            return fresh

        def fn(x):
            if not x.get("baseline_at"):
                x["notified"] = sorted(set(x.get("notified") or []) | set(base))
                x["read"] = sorted(set(x.get("read") or []) | set(base))
                x["baseline_at"] = fresh["baseline_at"]
        return self.update_cursor(me["name"], fn)

    @staticmethod
    def baseline_names(me, letters):
        since = ts_of(me.get("since"))
        base = []
        for l in letters:
            if since is None:
                base.append(l.name)
                continue
            try:
                if os.stat(l.path).st_mtime < since - 120:
                    base.append(l.name)
            except OSError:
                pass
        return base

    def adopt_box(self, name, box):
        """参与方新拿到一个已有历史的收件箱时，把箱里现有的信记为已读，免得被历史信淹没。"""
        if self.read_only:
            return 0
        names = [l.name for l in self.scan()[0] if l.box == box]
        if names:
            self.mark(name, names, read=True)
        return len(names)

    def mark(self, name, names, read=True, unread=False):
        names = set(names)

        def fn(d):
            if unread:
                # 重新标为未读：已读、已提醒、已送达（MCP）三个集合都去掉，下次 watch 或 mailbox_wait 会再报
                for k in ("notified", "read", "delivered"):
                    if k in d:
                        d[k] = sorted(set(d.get(k) or []) - names)
                return
            d["notified"] = sorted(set(d.get("notified") or []) | names)
            if read:
                d["read"] = sorted(set(d.get("read") or []) | names)
        return self.update_cursor(name, fn)

    def log(self, kind, obj):
        if self.read_only:
            return
        # Windows 上 O_APPEND 不是原子的（先定位再写），多进程同时追加会丢行，所以加锁
        lock = FileLock(self.p("locks", "log-%s.lock" % kind), timeout=5.0)
        try:
            lock.acquire()
        except (LockBusy, OSError):
            lock = None
        try:
            append_line(self.p("log", kind + ".jsonl"), obj)
        except OSError:
            pass
        finally:
            if lock:
                lock.release()

    # 花名册写入
    def save_config(self, cfg, actor, action, detail=None):
        self.require_writable()
        cfg = copy.deepcopy(cfg)
        cfg["revision"] = int(cfg.get("revision") or 0) + 1
        cfg["updated_at"] = iso_utc()
        errors, _ = validate_config(cfg)
        if errors:
            raise CliError("改动后的花名册无效，未写入：\n  - " + "\n  - ".join(errors), 2)
        atomic_write(self.config_path, dump_json(cfg))
        self.log("roster", {"at": cfg["updated_at"], "revision": cfg["revision"], "actor": actor,
                            "action": action, "detail": detail or {}})
        self.reset()
        return cfg

    def edit_config(self, actor, action, fn, detail=None):
        self.require_writable()
        with FileLock(self.p("locks", "config.lock")):
            self.reset()
            cfg = copy.deepcopy(self.cfg)
            fn(cfg)
            return self.save_config(cfg, actor, action, detail)


def my_letters(mb, me, letters, boxes=None):
    boxes = boxes or mb.boxes_of(me)
    return [l for l in letters if l.box in boxes]


# ------------------------------------------------------------------ 任务、备忘、在线
def tasks_path(mb, name):
    return mb.p("tasks", name + ".json")


def load_tasks(mb, name, strict=False):
    path = tasks_path(mb, name)
    d = (read_json_strict(path) if strict else load_json_quiet(path)) or {}
    d.setdefault("tasks", [])
    d.setdefault("waits", [])
    d.setdefault("waiting_on", None)
    return d


def update_tasks(mb, name, fn):
    mb.require_writable()
    with FileLock(mb.p("locks", "tasks-%s.lock" % name)):
        d = load_tasks(mb, name, strict=True)
        fn(d)
        d["name"] = name
        d["updated_at"] = iso_utc()
        atomic_write(tasks_path(mb, name), dump_json(d))
        return d


def notes_path(mb, name):
    return mb.p("notes", name + ".md")


class Presence(object):
    """监视进程的在线登记；新监视默认接管旧监视，旧监视发现令牌被换就自行退出。"""
    BEAT = 30.0

    def __init__(self, mb, me, mode, takeover=True):
        self.mb, self.name, self.mode, self.takeover = mb, me["name"], mode, takeover
        self.path = mb.p("presence", self.name + ".json")
        self.token = secrets.token_hex(8)
        self.started = iso_utc()
        self.last = 0.0
        self.replaced = None

    def doc(self, **extra):
        d = {"name": self.name, "pid": os.getpid(), "host": HOST, "token": self.token, "mode": self.mode,
             "started": self.started, "beat": iso_utc()}
        d.update(extra)
        return d

    def claim(self):
        cur = load_json_quiet(self.path)
        if cur and not cur.get("stopped") and cur.get("token"):
            fresh = (time.time() - (ts_of(cur.get("beat")) or 0)) < 3 * self.BEAT
            alive = fresh and (cur.get("host") != HOST or pid_alive(cur.get("pid")))
            if alive:
                if not self.takeover:
                    raise CliError("%s 已有监视在跑（pid %s，%s，心跳 %s）；要接管请去掉 --no-takeover" % (
                        self.name, cur.get("pid"), cur.get("host"), cur.get("beat")), 5)
                self.replaced = cur
        atomic_write(self.path, dump_json(self.doc()))
        self.last = time.time()

    def still_mine(self):
        cur = load_json_quiet(self.path)
        return cur is None or cur.get("token") == self.token

    def beat(self):
        if time.time() - self.last >= self.BEAT:
            if self.still_mine():
                atomic_write(self.path, dump_json(self.doc()))
            self.last = time.time()

    def stop(self, reason):
        try:
            if self.still_mine():
                atomic_write(self.path, dump_json(self.doc(stopped=iso_utc(), reason=reason)))
        except OSError:
            pass


def presence_online(mb, name):
    pres = load_json_quiet(mb.p("presence", name + ".json")) or {}
    beat = ts_of(pres.get("beat"))
    return bool(beat and not pres.get("stopped") and time.time() - beat < 3 * Presence.BEAT)


# ------------------------------------------------------------------ 带序号的追加日志（通知流、通话记录共用）
def _last_seq(chunk):
    for raw in reversed(chunk.split(b"\n")):
        if not raw.strip():
            continue
        try:
            e = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue  # 崩溃留下的残行
        if isinstance(e, dict) and isinstance(e.get("seq"), int) and not isinstance(e.get("seq"), bool):
            return e["seq"]
    return None


def jsonl_tail_seq(path):
    """最后一条记录的序号，以及文件是否以换行结尾。只读末尾 64 KB，找不到再读全文。"""
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return 0, True
    with f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return 0, True
        back = min(size, 65536)
        f.seek(size - back)
        chunk = f.read(back)
        seq = _last_seq(chunk)
        if seq is None and back < size:
            f.seek(0)
            seq = _last_seq(f.read())
    return (seq or 0), chunk.endswith(b"\n")


def append_seq(path, make):
    """（调用方已持锁）取下一个序号并追加一行；make(seq) 返回记录。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    last, ends = jsonl_tail_seq(path)
    rec = make(last + 1)
    data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    if not ends:
        data = b"\n" + data  # 上次写到一半崩溃留下的残行，不和本行粘在一起
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o644)
    try:
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)
    return rec


def jsonl_append(mb, lockname, path, make, after=None):
    """持锁取下一个序号并追加一行；after() 在放锁前执行（例如归档旧条目）。拿不到锁就报错，绝不无锁取号。"""
    mb.require_writable()
    with FileLock(mb.p("locks", lockname)):
        rec = append_seq(path, make)
        if after is not None:
            after()
        return rec


def append_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o644)
    try:
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


_FILE_CACHE = {}
_FILE_CACHE_LOCK = threading.Lock()


def cached_load(path, loader):
    """按（大小，修改时间）缓存文件解析结果；监视循环、MCP 每秒都会查，文件没变就不重读。"""
    try:
        st = os.stat(path)
    except OSError:
        with _FILE_CACHE_LOCK:
            _FILE_CACHE.pop(path, None)
        return None
    key = (st.st_size, st.st_mtime_ns)
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    val = loader(path)
    with _FILE_CACHE_LOCK:
        if len(_FILE_CACHE) > 2000:
            _FILE_CACHE.clear()
        _FILE_CACHE[path] = (key, val)
    return val


def _parse_records(raw, seqs, out, after=0):
    """把一段 JSONL 里序号大于 after、还没见过的记录追加到 out；看不懂的行、残行跳过，同号只取第一条。"""
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            e = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(e, dict) or not isinstance(e.get("seq"), int) or isinstance(e.get("seq"), bool):
            continue
        if e["seq"] > after and e["seq"] not in seqs:
            seqs.add(e["seq"])
            out.append(e)


def jsonl_read(path, after=0):
    """整读：序号大于 after 的记录（按序号）。"""
    raw = read_bytes_retry(path)
    if raw is None:
        return []
    out = []
    _parse_records(raw, set(), out, after)
    out.sort(key=lambda e: e["seq"])
    return out


_TAIL = {}
_TAIL_LOCK = threading.Lock()


def jsonl_records(path):
    """只追加 JSONL 的增量读取（监视与送达每秒都查）：文件只是变长了，就只读新增的完整行；
    被替换（归档后）、变短或开头变了才整读。返回按序号排好的列表；每次有新内容都返回新列表，调用方不要改它。"""
    with _TAIL_LOCK:
        try:
            st = os.stat(path)
        except OSError:
            _TAIL.pop(path, None)
            return []
        ident = (st.st_dev, st.st_ino)
        c = _TAIL.get(path)
        if c is not None and c["ident"] == ident and c["offset"] == st.st_size:
            return c["recs"]
        try:
            with open(path, "rb") as f:
                head = f.read(256)
                if (c is None or c["ident"] != ident or st.st_size < c["offset"]
                        or head[:len(c["head"])] != c["head"]):
                    c = {"ident": ident, "offset": 0, "recs": [], "seqs": set(), "head": b""}
                f.seek(c["offset"])
                chunk = f.read()
        except OSError:
            return c["recs"] if c is not None else []  # 正被替换（Windows 上会短暂拒绝访问）：先用上次的，下一轮再读
        cut = chunk.rfind(b"\n")  # 最后一行可能还没写完，留到下一轮
        if cut >= 0:
            new = []
            _parse_records(chunk[:cut], c["seqs"], new)
            recs = c["recs"] + new
            if any(recs[i]["seq"] > recs[i + 1]["seq"] for i in range(max(0, len(c["recs"]) - 1), len(recs) - 1)):
                recs.sort(key=lambda e: e["seq"])
            c["recs"], c["offset"] = recs, c["offset"] + cut + 1
        if len(head) > len(c["head"]):
            c["head"] = head
        if len(_TAIL) > 2000:
            _TAIL.clear()
        _TAIL[path] = c
        return c["recs"]


def check_text(text, what, limit, allow_secret=False, extra=None):
    """通知、通话、队列里的文字：UTF-8、换行统一为 LF、无损坏字、不超长、不含疑似凭据。"""
    text = hygiene(text.encode("utf-8") if isinstance(text, str) else text, True).strip("\n")
    if not text.strip():
        raise CliError("%s不能为空" % what, 2)
    n = len(text.encode("utf-8"))
    if n > limit:
        raise CliError("%s %d 字节，超过上限 %d；长内容请写信（send）或附文件路径" % (what, n, limit), 2)
    if not allow_secret:
        hits = scan_secrets(text, extra)
        if hits:
            raise CliError("%s里疑似有凭据（第 %s 行），拒收；确认不是凭据可加 --allow-secret" % (
                what, "、".join(str(h[0]) for h in hits[:10])), 2)
    return text


# ------------------------------------------------------------------ 通知
NOTIFY_MAX = 4096
NOTIFY_ROTATE_BYTES = 512 * 1024  # 通知流超过这么大，就把旧条目移到归档
NOTIFY_KEEP = 500                 # 归档后至少留下最近这么多条
NOTIFY_KEEP_UNSEEN = 10000        # 本人一直不看时，最多为他留这么多条没看过的


def feed_path(mb, name):
    return mb.p("notify", name + ".jsonl")


def feed_archive_path(mb, name):
    return mb.p("notify", name + ".archive.jsonl")


def rotate_feed(mb, name):
    """（持这条通知流的锁调用）超过 NOTIFY_ROTATE_BYTES 时，把旧条目移到 <名>.archive.jsonl；
    本人还没看过的、以及最近 NOTIFY_KEEP 条留在原处。序号不变，已看指针照旧有效。返回移走的条数。"""
    path = feed_path(mb, name)
    try:
        if os.path.getsize(path) <= NOTIFY_ROTATE_BYTES:
            return 0
        recs = jsonl_read(path)
        if len(recs) <= NOTIFY_KEEP:
            return 0
        # 按条数算（不按序号差）：从第一条没看过的、和倒数第 NOTIFY_KEEP 条里取靠前的那个留起；最多留 NOTIFY_KEEP_UNSEEN 条
        seen = seen_pointers(mb, name)[0]
        first_unseen = next((i for i, r in enumerate(recs) if r["seq"] > seen), len(recs))
        start = max(min(first_unseen, len(recs) - NOTIFY_KEEP), len(recs) - NOTIFY_KEEP_UNSEEN, 0)
        old, keep = recs[:start], recs[start:]
        if not old:
            return 0

        def enc(rs):
            return b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8") for r in rs)
        append_bytes(feed_archive_path(mb, name), enc(old))  # 先写归档：中途崩溃只会多出一份，不会丢
        atomic_write(path, enc(keep))
        return len(old)
    except (OSError, CliError) as e:
        warn("（%s 的通知流归档没做成，下次再试：%s）" % (name, str(e).split("\n")[0]))
        return 0


def notify_push(mb, name, kind, text, frm="", title="", urgent=False, ref=None):
    def make(seq):
        rec = {"seq": seq, "at": iso_utc(), "from": frm, "kind": kind, "title": clip(title, 200), "text": text,
               "urgent": bool(urgent)}
        if ref:
            rec["ref"] = ref
        return rec
    return jsonl_append(mb, "notify-%s.lock" % name, feed_path(mb, name), make, after=lambda: rotate_feed(mb, name))


def notify_system(mb, names, kind, text, frm="", title="", urgent=False, ref=None):
    """系统通知尽力而为：写不进去只在标准错误提示，不让主操作失败。"""
    done = []
    for n in dict.fromkeys(x for x in names if x and x != "user"):
        try:
            notify_push(mb, n, kind, clip(text, NOTIFY_MAX), frm, title, urgent, ref)
            done.append(n)
        except (CliError, OSError) as e:
            warn("（给 %s 的通知没写进去：%s）" % (n, str(e).split("\n")[0]))
    return done


def advance_seen(mb, name, notify=None, talk=None):
    """把通知流、通话的「已看」指针往前推（只增不减）。"""
    if mb.read_only or (notify is None and not talk):
        return

    def fn(d):
        if notify is not None:
            d["notify_seen"] = max(int(d.get("notify_seen") or 0), int(notify))
        if talk:
            ts = dict(d.get("talk_seen") or {})
            for k, v in talk.items():
                ts[k] = max(int(ts.get(k) or 0), int(v))
            d["talk_seen"] = ts
    mb.update_cursor(name, fn)


def seen_pointers(mb, name):
    """（通知已看序号，{通话: 已看序号}）。"""
    cur = load_json_quiet(mb.cursor_path(name)) or {}
    return int(cur.get("notify_seen") or 0), dict((k, int(v or 0)) for k, v in (cur.get("talk_seen") or {}).items())


def notify_unseen(mb, name, seen=None):
    if seen is None:
        seen = seen_pointers(mb, name)[0]
    return [e for e in jsonl_records(feed_path(mb, name)) if e["seq"] > seen]


def iso_at(epoch):
    return iso_utc(dt.datetime.fromtimestamp(epoch, UTC))


def poll_until(seconds, fn, interval=0.5):
    """每隔 interval 秒调一次 fn，直到它返回真值或超时（返回 None）。"""
    deadline = time.time() + max(0.0, float(seconds or 0))
    while True:
        r = fn()
        if r:
            return r
        left = deadline - time.time()
        if left <= 0:
            return None
        time.sleep(min(interval, max(0.05, left)))


def resolve_people(mb, me, spec, what):
    """把「a,b、controller、assistants、all」展开成在任参与方（不含自己）；返回（名单，是否含全体）。"""
    tos = [x.strip() for x in re.split(r"[,，、]", spec or "") if x.strip()]
    if not tos:
        raise CliError("%s需要 --to 收件人" % what, 2)
    out, everyone = [], False
    mine = "" if me is USER else me["name"]
    for t in tos:
        v = mb.find(t)
        if v == "@all":
            everyone = True
            out += [p for p in mb.participants(True) if p["name"] != mine]
        elif v == "@assistants":
            out += [p for p in mb.participants(True) if p["role"] == "assistant" and p["name"] != mine]
        else:
            if v["name"] == mine:
                raise CliError("不能给自己发%s" % what, 2)
            if not v["active"]:
                raise CliError("%s 已不在任" % v["name"], 2)
            out.append(v)
    uniq = list(dict((p["name"], p) for p in out).values())
    if not uniq:
        raise CliError("没有可发的对象（展开后为空）", 2)
    return uniq, everyone


def cc_controller(mb, me, people):
    """policy.assistant_to_assistant=cc-controller 时，助手之间的往来自动带上主控。"""
    if me is USER or me["role"] != "assistant" or mb.cfg["policy"].get("assistant_to_assistant") != "cc-controller":
        return None
    if not any(p["role"] == "assistant" for p in people):
        return None
    c = mb.controller()
    return None if any(p["name"] == c["name"] for p in people) else c


# ------------------------------------------------------------------ 通话
TALK_SCHEMA = "agent-mailbox/talk/1"
TALK_MAX = 16384
TALK_ID_RE = re.compile(r"^c([1-9][0-9]{0,8})$")


def talk_meta_path(mb, cid):
    return mb.p("talk", cid + ".json")


def talk_log_path(mb, cid):
    return mb.p("talk", cid + ".jsonl")


def norm_conv(ref):
    s = str(ref or "").strip().lower()
    if re.match(r"^[0-9]+$", s):
        s = "c%d" % int(s)
    if not TALK_ID_RE.match(s):
        raise CliError("看不懂的通话编号 %r（形如 c12，也可只写 12）" % (ref,), 2)
    return s


def talk_ids(mb):
    try:
        names = os.listdir(mb.p("talk"))
    except OSError:
        return []
    out = []
    for n in names:
        m = TALK_ID_RE.match(n[:-5]) if n.endswith(".json") else None
        if m:
            out.append((int(m.group(1)), n[:-5]))
    return [c for _, c in sorted(out)]


def talk_meta(mb, cid, fresh=False):
    path = talk_meta_path(mb, cid)
    return read_json_strict(path) if fresh else cached_load(path, read_json_strict)


def talk_log(mb, cid, fresh=False):
    path = talk_log_path(mb, cid)
    return jsonl_read(path) if fresh else jsonl_records(path)


def talk_member(meta, name):
    for m in meta.get("members") or []:
        if m.get("name") == name:
            return m
    return None


def talk_in(meta, name):
    m = talk_member(meta, name)
    return bool(m and not m.get("left"))


def talk_people(mb, meta, but=None):
    """通话里在任、没退出的成员（参与方 dict；用户以 USER 表示）。"""
    out = []
    for m in meta.get("members") or []:
        if m.get("left") or m.get("name") == but:
            continue
        p = USER if m.get("name") == "user" else mb.get(m.get("name"))
        if p and p["active"]:
            out.append(p)
    return out


def talk_brief(meta):
    return {"id": meta["id"], "topic": meta.get("topic") or "", "state": meta.get("state") or "open",
            "created_by": meta.get("created_by") or "", "created_at": meta.get("created_at") or "",
            "members": [m["name"] for m in meta.get("members") or [] if not m.get("left")],
            "left": [m["name"] for m in meta.get("members") or [] if m.get("left")]}


def talk_mine(mb, name, closed=True):
    out = []
    for cid in talk_ids(mb):
        try:
            meta = talk_meta(mb, cid)
        except CliError:
            continue
        if meta and talk_in(meta, name) and (closed or meta.get("state") == "open"):
            out.append(meta)
    return out


def talk_unread(mb, name, meta, seen):
    """name 在这个通话里还没看过的、别人发的记录（含邀请、退出、结束等系统记录）。"""
    m = talk_member(meta, name)
    if not m or m.get("left"):
        return []
    base = max(int(m.get("since") or 0), int(seen.get(meta["id"]) or 0))
    return [x for x in talk_log(mb, meta["id"]) if x["seq"] > base and x.get("from") != name]


def talk_queue(mb, name, seen=None):
    """name 的来电队列：有未读消息的进行中通话；紧急的在前，其余按最早一条未读消息先来后到。"""
    if seen is None:
        seen = seen_pointers(mb, name)[1]
    rows = []
    for meta in talk_mine(mb, name, closed=False):
        un = [x for x in talk_unread(mb, name, meta, seen) if x.get("kind", "msg") == "msg"]
        if un:
            rows.append({"conv": meta["id"], "topic": meta.get("topic") or "", "from": un[0].get("from") or "",
                         "since": un[0].get("at") or "", "count": len(un), "urgent": any(x.get("urgent") for x in un),
                         "first_seq": un[0]["seq"]})
    rows.sort(key=lambda r: (not r["urgent"], ts_of(r["since"]) or 0, int(r["conv"][1:])))
    return rows


def talk_create(mb, me, people, topic):
    mb.require_writable()
    os.makedirs(mb.p("talk"), exist_ok=True)
    now = iso_utc()
    members = [{"name": x, "since": 0, "at": now} for x in dict.fromkeys([me["name"]] + [p["name"] for p in people])]
    with FileLock(mb.p("locks", "talk.lock")):
        ids = talk_ids(mb)
        n = int(ids[-1][1:]) + 1 if ids else 1
        for _ in range(50):
            cid = "c%d" % n
            meta = {"schema": TALK_SCHEMA, "id": cid, "topic": clip(topic, 200), "created_by": me["name"],
                    "created_at": now, "state": "open", "members": members}
            try:
                with open(talk_meta_path(mb, cid), "xb") as f:
                    f.write(dump_json(meta))
            except FileExistsError:
                n += 1
                continue
            return meta
    raise CliError("连试 50 个通话编号都被占用，没有建成")


def _talk_record(me, kind, text, **extra):
    def make(seq):
        rec = {"seq": seq, "at": iso_utc(), "from": me["name"], "kind": kind, "text": text}
        rec.update((k, v) for k, v in extra.items() if v)
        return rec
    return make


def talk_post(mb, me, cid, text, reply_to=0, urgent=False):
    """在通话里发言：须是成员（主控、用户会自动加入）；对其中每个在任成员都要有 talk 权限。"""
    mb.require_writable()
    with FileLock(mb.p("locks", "talk-%s.lock" % cid)):
        meta = talk_meta(mb, cid, fresh=True)
        if meta is None:
            raise CliError("没有通话 %s（talk list 查看）" % cid, 2)
        if meta.get("state") != "open":
            raise CliError("通话 %s 已结束（%s 于 %s）；要继续请另起一个" % (cid, meta.get("closed_by"), meta.get("closed_at")), 2)
        log = talk_log_path(mb, cid)
        if not talk_in(meta, me["name"]):
            if not (me is USER or (me["role"] == "controller" and me["active"])):
                raise CliError("你不在通话 %s 里；请成员用 talk invite 邀请你" % cid, 2)
            _talk_join(meta, [me["name"]], jsonl_tail_seq(log)[0])
            atomic_write(talk_meta_path(mb, cid), dump_json(meta))
            append_seq(log, _talk_record(me, "join", "%s 加入通话" % me["name"], who=[me["name"]]))
        for t in talk_people(mb, meta, but=me["name"]):
            mb.require_perm(me, "talk", t)
        if reply_to:
            if not any(x["seq"] == reply_to for x in jsonl_read(log)):
                raise CliError("通话 %s 里没有第 %d 条" % (cid, reply_to), 2)
        rec = append_seq(log, _talk_record(me, "msg", text, reply_to=reply_to, urgent=bool(urgent)))
    return rec, meta


def _talk_join(meta, names, last_seq):
    now = iso_utc()
    for n in names:
        m = talk_member(meta, n)
        if m is None:
            meta.setdefault("members", []).append({"name": n, "since": last_seq, "at": now})
        elif m.get("left"):
            m.update(left=False, since=last_seq, at=now)
            m.pop("left_at", None)


def talk_change(mb, me, cid, op, people=None, cc=None):
    """邀请（invite）、退出（leave）、结束（close）。"""
    mb.require_writable()
    with FileLock(mb.p("locks", "talk-%s.lock" % cid)):
        meta = talk_meta(mb, cid, fresh=True)
        if meta is None:
            raise CliError("没有通话 %s" % cid, 2)
        boss = me is USER or (me["role"] == "controller" and me["active"])
        if op != "close" and meta.get("state") != "open":
            raise CliError("通话 %s 已结束" % cid, 2)
        log = talk_log_path(mb, cid)
        last = jsonl_tail_seq(log)[0]
        if op == "invite":
            if not talk_in(meta, me["name"]) and not boss:
                raise CliError("你不在通话 %s 里，不能邀请别人" % cid, 2)
            new = [p for p in people if not talk_in(meta, p["name"])]
            for p in new:
                mb.require_perm(me, "talk", p)
            if cc is not None and not talk_in(meta, cc["name"]):
                new.append(cc)
            if not new:
                raise CliError("他们都已在通话 %s 里" % cid, 2)
            names = [p["name"] for p in new]
            _talk_join(meta, names, last)
            text = "%s 邀请 %s 加入通话" % (me["name"], "、".join(names))
            rec = dict(kind="join", text=text, who=names)
        elif op == "leave":
            m = talk_member(meta, me["name"])
            if not m or m.get("left"):
                raise CliError("你不在通话 %s 里" % cid, 2)
            m.update(left=True, left_at=iso_utc())
            rec = dict(kind="leave", text="%s 退出通话" % me["name"])
        else:
            if meta.get("state") != "open":
                raise CliError("通话 %s 已经结束" % cid, 2)
            if not boss and meta.get("created_by") != me["name"]:
                raise CliError("只有发起人（%s）或主控能结束通话 %s" % (meta.get("created_by"), cid), 2)
            meta.update(state="closed", closed_by=me["name"], closed_at=iso_utc())
            rec = dict(kind="close", text="%s 结束了通话" % me["name"])
        atomic_write(talk_meta_path(mb, cid), dump_json(meta))
        kind, text = rec.pop("kind"), rec.pop("text")
        out = append_seq(log, _talk_record(me, kind, text, **rec))
    return out, meta


def talk_find_pair(mb, me, other, cc=None):
    """最近一个只有本方与 other 两人（按策略可再带上抄送的主控）、仍在进行的通话。"""
    want = set([me["name"], other["name"]] + ([cc["name"]] if cc else []))
    best = None
    for meta in talk_mine(mb, me["name"], closed=False):
        names = set(m["name"] for m in meta.get("members") or [] if not m.get("left"))
        if names == want:
            best = meta
    return best


def talk_replies(mb, me, cid, after):
    return [x for x in talk_log(mb, cid, fresh=True) if x["seq"] > after and x.get("from") != me["name"]]


# ------------------------------------------------------------------ 队列
QUEUE_SCHEMA = "agent-mailbox/queue/1"
QUEUE_MAX_PENDING = 500
QUEUE_KEEP_DONE = 100
JOB_MAX = 16384
JOB_FINAL = ("done", "failed", "cancelled")
JOB_ZH = {"pending": "排队", "claimed": "已领", "done": "完成", "failed": "失败", "cancelled": "已取消"}


def queue_path(mb, name):
    return mb.p("queues", name + ".json")


def check_queue_name(name):
    if not BOX_RE.match(name or "") or len(name) > 64:
        raise CliError("队列名须是小写字母数字，可用 - 连接（如 review、build-2）：%r" % (name,), 2)
    return name


def queue_names(mb):
    try:
        names = os.listdir(mb.p("queues"))
    except OSError:
        return []
    return sorted(n[:-5] for n in names if n.endswith(".json") and BOX_RE.match(n[:-5]))


def job_order(j):
    return (-int(j.get("priority") or 0), int(j["id"]))


def job_log(j, event, who, detail=""):
    j.setdefault("log", []).append([iso_utc(), event, who, clip(detail, 200)])
    del j["log"][:-20]


def queue_pending(q):
    return sorted([j for j in q["jobs"] if j["status"] == "pending"], key=job_order)


def queue_position(q, job_id):
    for i, j in enumerate(queue_pending(q)):
        if j["id"] == job_id:
            return i + 1
    return 0


def queue_expire(q, now, notes):
    """租约到期的任务：还有尝试次数就放回排队，否则记为失败并通知投放者。"""
    for j in q["jobs"]:
        if j["status"] != "claimed" or (ts_of(j.get("lease_until")) or 0) >= now:
            continue
        who = j.get("claimed_by") or "?"
        if int(j.get("attempts") or 0) >= int(q.get("max_attempts") or 1):
            j.update(status="failed", finished_at=iso_at(now), finished_by="",
                     error="租约到期：%s 没在期限内完成，已用完 %d 次尝试" % (who, int(q.get("max_attempts") or 1)))
            job_log(j, "expired-failed", who)
            notes.append(([j.get("from")], "job-failed", "队列 %s 的任务 #%d「%s」失败：%s" % (
                q["name"], j["id"], j.get("title"), j["error"]), {"queue": q["name"], "job": j["id"]}))
        else:
            j.update(status="pending", claimed_by="", claimed_at="", lease_until="")
            job_log(j, "expired", who, "租约到期，放回排队")


def queue_prune(mb, q):
    done = sorted([j for j in q["jobs"] if j["status"] in JOB_FINAL], key=lambda j: (j.get("finished_at") or "", j["id"]))
    extra = done[:-QUEUE_KEEP_DONE] if len(done) > QUEUE_KEEP_DONE else []
    if not extra:
        return
    ids = set(j["id"] for j in extra)
    for j in extra:
        try:
            append_line(mb.p("queues", q["name"] + ".archive.jsonl"), j)
        except OSError:
            pass
    q["jobs"] = [j for j in q["jobs"] if j["id"] not in ids]


def queue_view(mb, name):
    """只读查看：实时算出租约到期后的样子，但不写回（下次有人操作这个队列时才真正收回）。"""
    q = read_json_strict(queue_path(mb, name))
    if q is None:
        raise CliError("没有队列 %s（queue list 查看）" % name, 2)
    q = copy.deepcopy(q)
    queue_expire(q, time.time(), [])
    return q


def queue_edit(mb, name, fn, create=None, actor=""):
    """持队列锁读改写。fn(q, notes) 的返回值原样返回；notes 里攒的通知在放锁之后才发。"""
    mb.require_writable()
    path = queue_path(mb, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    notes = []
    with FileLock(mb.p("locks", "queue-%s.lock" % name)):
        q = read_json_strict(path)
        if q is None:
            if create is None:
                raise CliError("没有队列 %s（queue list 查看；主控或有 queue.admin 权限的人可 queue create）" % name, 2)
            q = create()
        queue_expire(q, time.time(), notes)
        res = fn(q, notes)
        queue_prune(mb, q)
        q["updated_at"] = iso_utc()
        atomic_write(path, dump_json(q))
    for names, kind, text, ref in notes:
        notify_system(mb, [n for n in names if n and n != actor], kind, text, actor or "", "队列 " + name, ref=ref)
    return res, q


def queue_new(name, me, desc="", lease=900, max_attempts=3, consumers=None, notify=True):
    return {"schema": QUEUE_SCHEMA, "name": name, "desc": clip(desc, 300), "created_by": me["name"],
            "created_at": iso_utc(), "lease_seconds": int(lease), "max_attempts": int(max_attempts),
            "consumers": list(consumers or []), "notify": bool(notify), "seq": 0, "jobs": []}


def queue_job(q, job_id):
    for j in q["jobs"]:
        if j["id"] == job_id:
            return j
    raise CliError("队列 %s 里没有任务 #%d（完成太久的会移到归档）" % (q["name"], job_id), 2)


def queue_consumers(mb, q, me):
    """新任务通知谁：队列登记的领取名单；没登记就是有领取权限的在任助手与旁听者（不含主控、投放者本人）。"""
    if q.get("consumers"):
        return [n for n in q["consumers"] if mb.get(n) and mb.get(n)["active"]]
    return [p["name"] for p in mb.participants(True)
            if p["role"] != "controller" and p["name"] != me["name"] and mb.can(p, "queue.take", q["name"])]


# ------------------------------------------------------------------ 加入申请
REQ_MAX_PENDING = 20


def request_path(mb, name):
    return mb.p("requests", name + ".json")


def requests_all(mb):
    try:
        names = sorted(os.listdir(mb.p("requests")))
    except OSError:
        return []
    out = []
    for n in names:
        if n.endswith(".json") and NAME_RE.match(n[:-5]):
            d = load_json_quiet(mb.p("requests", n))
            if isinstance(d, dict):
                out.append(d)
    out.sort(key=lambda d: (d.get("at") or "", d.get("name") or ""))
    return out


# ------------------------------------------------------------------ 命令公共
def resolve_mailbox_dir(a):
    d = a.mailbox or os.environ.get("AGENT_MAILBOX_DIR")
    if not d:
        dflt = load_json_quiet(os.path.join(os.path.expanduser("~"), ".agent-mailbox", "default.json")) or {}
        d = dflt.get("mailbox")
    if not d:
        raise CliError("没有指定信箱目录：用 --mailbox，或设环境变量 AGENT_MAILBOX_DIR，或先用 use 设默认", 2)
    return os.path.abspath(os.path.expanduser(d))


def open_mb(a, need_config=True):
    root = resolve_mailbox_dir(a)
    if not os.path.isdir(root):
        raise CliError("信箱目录不存在：%s" % root, 2)
    mb = Mailbox(root, a.config, a.state_dir, a.read_only,
                 token=read_token_file(a.token_file) if getattr(a, "token_file", None) else None)
    if need_config:
        mb.cfg  # 尽早校验花名册
    return mb


def sender_desc(p):
    if p is USER:
        return "user（用户）"
    bits = [ROLE_ZH.get(p["role"], p["role"])]
    for k in ("model", "harness"):
        if p.get(k):
            bits.append(p[k])
    if p.get("session"):
        bits.append("会话 " + p["session"])
    return "%s（%s）" % (p["name"], "，".join(bits))


def lines_with(text, ch):
    return [i + 1 for i, line in enumerate(text.split("\n")) if ch in line][:20]


def hygiene(raw, normalize):
    """投递前卫生检查：UTF-8、无 BOM、无 CR、无 U+FFFD 损坏字。"""
    problems = []
    if raw.startswith(b"\xef\xbb\xbf"):
        if normalize:
            raw = raw[3:]
        else:
            problems.append("开头有 UTF-8 BOM")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CliError("正文不是合法 UTF-8：%s" % e, 2)
    if "\r" in text:
        if normalize:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        else:
            problems.append("含 CR（第 %s 行）" % "、".join(str(x) for x in lines_with(text, "\r")))
    bad = lines_with(text, chr(0xFFFD))
    if bad:
        raise CliError("正文含 U+FFFD 损坏字（第 %s 行），请把这些字修好再投递" % "、".join(str(x) for x in bad), 2)
    if problems:
        raise CliError("正文不合规：%s；可加 --normalize 自动去 BOM、把 CRLF 换成 LF" % "；".join(problems), 2)
    return text


def split_title(text):
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("#"):
        return lines[i].strip(), "\n".join(lines[i + 1:]).strip("\n")
    return None, text.strip("\n")


def scan_secrets(text, extra):
    hits = []
    for pat in SECRET_PATTERNS + list(extra or []):
        for m in re.finditer(pat, text):
            hits.append((text.count("\n", 0, m.start()) + 1, pat))
    return hits


def attach_section(paths):
    rows = []
    for p in paths:
        ap = os.path.abspath(os.path.expanduser(p))
        if not os.path.isfile(ap):
            raise CliError("附件须是存在的文件：%s（目录请先生成 SHA256SUMS 再附该文件）" % p, 2)
        h, n = sha256_file(ap)
        rows.append("| `%s` | %d | `%s` |" % (slash(ap), n, h))
    if not rows:
        return ""
    return "\n".join(["", "## 附件（投递时实算）", "", "| 文件 | 字节 | SHA-256 |", "|---|---|---|"] + rows) + "\n"


def read_file_arg(path):
    if not os.path.isfile(path):
        raise CliError("找不到文件：%s" % path, 2)
    with open(path, "rb") as f:
        return f.read()


def read_body(a):
    if a.draft:
        return read_file_arg(a.draft)
    if a.body_file:
        return read_file_arg(a.body_file)
    if a.stdin:
        return read_stdin_bytes()
    if a.body is not None:
        return a.body.encode("utf-8")
    raise CliError("没有正文：用 --draft、--body-file、--stdin 或 --body", 2)


# ------------------------------------------------------------------ init / use / roster
def cmd_init(a):
    root = os.path.abspath(os.path.expanduser(a.mailbox or os.environ.get("AGENT_MAILBOX_DIR") or ""))
    if not a.mailbox and not os.environ.get("AGENT_MAILBOX_DIR"):
        raise CliError("init 需要 --mailbox 指明信箱目录", 2)
    os.makedirs(root, exist_ok=True)
    mb = Mailbox(root, a.config, a.state_dir, a.read_only)
    mb.require_writable()
    if os.path.exists(mb.config_path):
        raise CliError("花名册已存在：%s（改动请用 roster 子命令）" % mb.config_path, 2)
    if a.from_config:
        raw = load_json(a.from_config)
        if not isinstance(raw, dict):
            raise CliError("--from-config 不是 JSON 对象", 2)
        cfg = default_config(raw.get("thread"))
        cfg.update(raw)
        cfg["policy"] = dict(default_config()["policy"], **(raw.get("policy") or {}))
        stamp = iso_utc()
        cfg["participants"] = [norm_participant(p) for p in raw.get("participants") or []]
        for p in cfg["participants"]:
            if p["active"] and not p["since"]:
                p["since"] = stamp
    else:
        if not a.controller:
            raise CliError("init 需要 --controller <主控名>（或 --from-config 花名册文件）", 2)
        thread = a.thread or secrets.token_hex(4)
        cfg = default_config(thread)
        cfg.update({"controller_box": a.controller_box, "broadcast_box": a.broadcast_box,
                    "timezone": a.tz, "timezone_label": a.tz_label, "max_assistants": a.max_assistants})
        cfg["participants"] = [norm_participant({
            "name": a.controller, "role": "controller", "display": a.display or a.controller,
            "model": a.model, "harness": a.harness, "session": a.session, "box": a.box,
            "aliases": a.alias or [], "active": True, "since": iso_utc()})]
    cfg = mb.save_config(cfg, a.me or "user", "init", {"from_config": a.from_config or ""})
    for sub in ("cursors", "tasks", "notes", "presence", "locks", "log"):
        os.makedirs(mb.p(sub), exist_ok=True)
    if a.json:
        emit_json({"mailbox": slash(root), "config": slash(mb.config_path), "thread": cfg["thread"],
                   "controller": [p["name"] for p in cfg["participants"] if p["role"] == "controller"][0]})
    else:
        emit("已初始化信箱 %s｜线程 %s｜主控 %s｜花名册 %s" % (
            slash(root), cfg["thread"] or "（无）",
            [p["name"] for p in cfg["participants"] if p["role"] == "controller"][0], slash(mb.config_path)))
    return 0


def cmd_use(a):
    path = os.path.join(os.path.expanduser("~"), ".agent-mailbox", "default.json")
    if a.clear:
        if os.path.exists(path):
            os.unlink(path)
        emit("已清除默认信箱")
        return 0
    if not a.mailbox:
        cur = load_json_quiet(path) or {}
        emit("默认信箱：%s" % (cur.get("mailbox") or "（未设）"))
        return 0
    root = os.path.abspath(os.path.expanduser(a.mailbox))
    if not os.path.isdir(root):
        raise CliError("信箱目录不存在：%s" % root, 2)
    atomic_write(path, dump_json({"mailbox": root, "set_at": iso_utc()}))
    emit("默认信箱已设为 %s（写在 %s）" % (slash(root), slash(path)))
    return 0


def roster_rows(mb):
    rows = []
    for p in mb.participants():
        rows.append({"name": p["name"], "role": p["role"], "active": p["active"], "display": p["display"],
                     "model": p["model"], "harness": p["harness"], "session": p["session"],
                     "inbox": mb.boxes_of(p) if p["active"] else ([p["box"]] if p["box"] else []),
                     "aliases": p["aliases"], "since": p["since"], "until": p["until"], "note": p["note"],
                     "allow": list(p.get("allow") or []), "deny": list(p.get("deny") or []),
                     "token": bool(p.get("token_sha256"))})
    return rows


def cmd_roster_show(a):
    mb = open_mb(a)
    cfg = mb.cfg
    rows = roster_rows(mb)
    if a.json:
        emit_json({"thread": cfg["thread"], "revision": cfg.get("revision"), "controller_box": cfg["controller_box"],
                   "broadcast_box": cfg["broadcast_box"], "max_assistants": cfg["max_assistants"],
                   "policy": cfg["policy"], "presets": dict((r, role_preset(cfg, r)) for r in PRESET_ROLES),
                   "user_token": bool(cfg.get("user_token_sha256")), "participants": rows,
                   "warnings": mb.config_warnings})
        return 0
    act = [r for r in rows if r["role"] == "assistant" and r["active"]]
    emit("信箱 %s｜线程 %s｜花名册第 %s 版｜主控箱 %s｜广播箱 %s｜在任助手 %d/%d" % (
        slash(mb.root), cfg["thread"] or "（无）", cfg.get("revision"), cfg["controller_box"],
        cfg["broadcast_box"], len(act), cfg["max_assistants"]))
    for r in sorted(rows, key=lambda r: (not r["active"], ROLES.index(r["role"]) if r["role"] in ROLES else 9)):
        perms = " ".join(["+" + x for x in r["allow"]] + ["-" + x for x in r["deny"]])
        emit("  %s %-18s %-4s  %s / %s%s  收件箱 %s%s%s%s" % (
            "●" if r["active"] else "○", r["name"], ROLE_ZH.get(r["role"], r["role"]), r["model"] or "-",
            r["harness"] or "-", ("  会话 " + r["session"]) if r["session"] else "",
            ",".join(r["inbox"]) or "-", ("  别名 " + "、".join(r["aliases"])) if r["aliases"] else "",
            ("  权限 " + perms) if perms else "", "  [令牌]" if r["token"] else ""))
    for w in mb.config_warnings:
        emit("  注意：" + w)
    return 0


def default_box(name):
    return re.sub(r"[._]+", "-", name).strip("-")


def cmd_roster_add(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_roster_admin(me)
    if a.role == "controller":
        raise CliError("新参与方不能直接设为主控：先 add 为助手，再 roster handover", 2)

    def fn(cfg):
        if any(p["name"] == a.name for p in cfg["participants"]):
            raise CliError("已有同名参与方 %s（恢复请用 roster update %s --activate）" % (a.name, a.name), 2)
        box = a.box or (default_box(a.name) if a.role == "assistant" else "")
        cfg["participants"].append(norm_participant({
            "name": a.name, "role": a.role, "display": a.display or a.name, "model": a.model, "harness": a.harness,
            "session": a.session, "box": box, "aliases": a.alias or [], "active": True, "since": iso_utc(),
            "note": a.note}))
    cfg = mb.edit_config(me["name"], "add", fn, {"name": a.name, "role": a.role})
    p = [x for x in cfg["participants"] if x["name"] == a.name][0]
    emit("已加入 %s（%s，%s / %s，收件箱 %s）；在任助手 %d/%d" % (
        a.name, ROLE_ZH[a.role], p["model"] or "-", p["harness"] or "-", p["box"] or "-",
        len([x for x in cfg["participants"] if x["role"] == "assistant" and x["active"]]), cfg["max_assistants"]))
    return 0


def cmd_roster_update(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "", need_active=False)
    target = mb.get(a.name)
    if target is None:
        raise CliError("花名册里没有 %s" % a.name, 2)
    self_fields = {"model", "harness", "session", "note"}
    asked = set(k for k in ("display", "model", "harness", "session", "box", "note") if getattr(a, k) is not None)
    if a.alias_add or a.alias_remove or a.activate:
        asked.add("roster")
    if not asked:
        raise CliError("没有要改的字段", 2)
    if not (me is not USER and me["name"] == a.name and asked <= self_fields):
        if target["role"] == "controller" and target["active"]:
            mb.require_controller(me)  # 代管花名册的人不能改主控
        else:
            mb.require_roster_admin(me)

    def fn(cfg):
        p = [x for x in cfg["participants"] if x["name"] == a.name][0]
        for k in ("display", "model", "harness", "session", "box", "note"):
            v = getattr(a, k)
            if v is not None:
                p[k] = v
        for x in a.alias_add or []:
            if x not in p["aliases"]:
                p["aliases"].append(x)
        if a.alias_remove:
            p["aliases"] = [x for x in p["aliases"] if x not in a.alias_remove]
        if a.activate:
            if p["role"] == "controller":
                raise CliError("%s 已是主控" % p["name"], 2)
            p["role"] = a.activate
            p["active"] = True
            p["until"] = ""
            p["since"] = iso_utc()
            if p["role"] == "assistant" and not p["box"]:
                p["box"] = default_box(p["name"])
    # 换箱或重新在任时，先把新箱里的历史信记为已读，再改花名册（顺序反了，监视会把历史信当新信报）
    box_after = a.box if a.box is not None else (
        target["box"] or (default_box(target["name"]) if a.activate == "assistant" else ""))
    if box_after and (box_after != target["box"] or (a.activate and not target["active"])) and not mb.read_only:
        if BOX_RE.match(box_after):
            mb.adopt_box(target["name"], box_after)
    mb.edit_config(me["name"], "update", fn, {"name": a.name, "fields": sorted(asked)})
    emit("已更新 %s：%s" % (a.name, "、".join(sorted(asked))))
    return 0


def cmd_roster_remove(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_roster_admin(me)
    t = mb.get(a.name)
    if t is None:
        raise CliError("花名册里没有 %s" % a.name, 2)
    if t["role"] == "controller":
        raise CliError("不能移除在任主控；先 roster handover 交接", 2)

    def fn(cfg):
        p = [x for x in cfg["participants"] if x["name"] == a.name][0]
        p["role"] = "former"
        p["active"] = False
        p["until"] = iso_utc()
        if a.note:
            p["note"] = a.note
    cfg = mb.edit_config(me["name"], "remove", fn, {"name": a.name})
    emit("已移除 %s（改为前任，历史信件保留）；在任助手 %d/%d" % (
        a.name, len([x for x in cfg["participants"] if x["role"] == "assistant" and x["active"]]),
        cfg["max_assistants"]))
    return 0


def cmd_roster_limit(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_roster_admin(me)

    def fn(cfg):
        cfg["max_assistants"] = a.n
    mb.edit_config(me["name"], "limit", fn, {"max_assistants": a.n})
    emit("在任助手上限已设为 %d" % a.n)
    return 0


def cmd_roster_policy(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_controller(me)
    changes = dict((k, getattr(a, k)) for k in ("assistant_to_assistant", "broadcast", "inactive_send")
                   if getattr(a, k) is not None)
    if not changes:
        emit_json(mb.cfg["policy"])
        return 0

    def fn(cfg):
        cfg["policy"].update(changes)
    mb.edit_config(me["name"], "policy", fn, changes)
    emit("已改策略：%s" % json.dumps(changes, ensure_ascii=False))
    return 0


def cmd_roster_handover(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_controller(me)
    old = mb.controller()
    new = mb.get(a.name)
    if new is None:
        raise CliError("花名册里没有 %s；先 roster add 再交接" % a.name, 2)
    if new["name"] == old["name"]:
        raise CliError("%s 已是主控" % a.name, 2)
    stamp = iso_utc()
    old_box = (a.old_box or old["box"] or default_box(old["name"])) if a.old_role == "assistant" else old["box"]
    inherited = 0
    if not mb.read_only:
        # 先把两人的游标备好，最后才改花名册：新主控的监视一看到角色变化，游标里已经有继承来的已读，
        # 不会把主控箱的历史信当新信报
        letters = mb.scan()[0]
        mb.state(new, my_letters(mb, new, letters))  # 新主控按 since 规则建好自己原有各箱的基线
        cbox = mb.cfg["controller_box"]
        in_box = set(l.name for l in letters if l.box == cbox)
        got_n = got_r = set()
        if not a.no_inherit:
            oc = read_json_strict(mb.cursor_path(old["name"])) or {}
            if oc.get("baseline_at"):
                got_n = set(x for x in oc.get("notified") or [] if x in in_box)
                got_r = set(x for x in oc.get("read") or [] if x in in_box)
            else:
                # 旧主控没用过本工具、没有已读记录：主控箱现有信按已处理计，避免新主控被历史信淹没
                got_n = got_r = in_box
        inherited = len(got_r)

        def cfn(d):
            d["notified"] = sorted(set(d.get("notified") or []) | got_n)
            d["read"] = sorted(set(d.get("read") or []) | got_r)
        mb.update_cursor(new["name"], cfn)
        if a.old_role == "assistant" and old_box and old_box != old["box"] and BOX_RE.match(old_box):
            mb.adopt_box(old["name"], old_box)  # 旧主控转助手后拿到的箱，历史信记为已读

    def fn(cfg):
        for p in cfg["participants"]:
            if p["name"] == old["name"]:
                if a.old_role == "assistant":
                    p["role"] = "assistant"
                    p["active"] = True
                    p["box"] = old_box
                else:
                    p["role"] = a.old_role
                    p["active"] = a.old_role == "observer"
                    if a.old_role == "former":
                        p["until"] = stamp
            elif p["name"] == new["name"]:
                p["role"] = "controller"
                p["active"] = True
                p["until"] = ""
                if not p["since"]:
                    p["since"] = stamp
    mb.edit_config(me["name"], "handover", fn, {"from": old["name"], "to": new["name"], "old_role": a.old_role})
    emit("主控已由 %s 交接给 %s（%s 现为%s）；主控箱 %s 不变，继承已读 %d 封" % (
        old["name"], new["name"], old["name"], ROLE_ZH.get(a.old_role, a.old_role), mb.cfg["controller_box"],
        inherited))
    return 0


# ------------------------------------------------------------------ 权限命令
SCOPE_ZH = {"who": "参与方或角色组", "queue": "队列", "": "不分范围"}


def perm_row(mb, p):
    preset, allow, deny = mb.perm_summary(p)
    return {"name": p["name"], "role": p["role"], "active": p["active"], "preset": preset, "allow": allow,
            "deny": deny, "token": bool(mb.cfg.get("user_token_sha256") if p is USER else p.get("token_sha256"))}


def emit_perm_row(r):
    emit("%s（%s%s）%s" % (r["name"], ROLE_ZH.get(r["role"], r["role"]), "" if r["active"] else "，不在任",
                         "  [令牌]" if r["token"] else ""))
    emit("  预设：%s" % ("全部权限" if r["preset"] == ["*"] else ("、".join(r["preset"]) or "（无）")))
    if r["allow"]:
        emit("  授予：%s" % "、".join(r["allow"]))
    if r["deny"]:
        emit("  收回：%s" % "、".join(r["deny"]))


def parse_perm_args(mb, items):
    """命令行给的权限（可逗号分隔）逐个校验并规范化；点名的参与方必须在花名册里。"""
    out = []
    for raw in items or []:
        for x in [s.strip() for s in re.split(r"[,，、\s]+", raw) if s.strip()]:
            pe = parse_perm(x)
            if pe is None:
                raise CliError("看不懂的权限 %r：写作 键[:范围]，键见 perm list，范围可写参与方名、@controller、"
                               "@assistants、@observers 或队列名" % x, 2)
            key, scope = pe
            if scope and not scope.startswith("@") and PERM_SCOPE.get(key) == "who" and mb.get(scope) is None:
                raise CliError("权限 %s 点名的 %s 不在花名册里" % (x, scope), 2)
            t = perm_text(key, scope)
            if t not in out:
                out.append(t)
    return out


def cmd_perm_list(a):
    mb = open_mb(a)
    cfg = mb.cfg
    cat = [{"perm": k, "scope": s, "scope_zh": SCOPE_ZH[s], "desc": d} for k, s, d in PERM_CATALOG]
    presets = dict((r, role_preset(cfg, r)) for r in PRESET_ROLES)
    custom = sorted((cfg.get("presets") or {}).keys())
    if a.json:
        emit_json({"catalog": cat, "presets": presets, "custom_presets": custom})
        return 0
    emit("权限目录（写作 键[:范围]；范围可写参与方名、@controller、@assistants、@observers 或队列名，不写表示不限）：")
    for c in cat:
        emit("  %-13s %-14s %s" % (c["perm"], c["scope_zh"], c["desc"]))
    emit("角色预设（主控与用户永远拥有全部权限）：")
    for r in PRESET_ROLES:
        emit("  %s%s：%s" % (ROLE_ZH[r], "（主控已自定义）" if r in custom else "（由通信策略推出）",
                             "、".join(presets[r]) or "（无）"))
    emit("判定：显式条目取最具体的一条（点名 > 角色组 > 不限），同样具体时收回优先；没有显式条目才看角色预设。")
    return 0


def cmd_perm_show(a):
    mb = open_mb(a)
    if a.name:
        ps = [USER if a.name == "user" else mb.lookup(a.name)]
    elif a.me:
        ps = [mb.identity(a.me, need_active=False)]
    else:
        ps = mb.participants(True)
    rows = [perm_row(mb, p) for p in ps]
    if a.json:
        emit_json({"participants": rows})
        return 0
    for r in rows:
        emit_perm_row(r)
    return 0


def cmd_perm_check(a):
    mb = open_mb(a)
    p = USER if a.name == "user" else mb.lookup(a.name)
    pe = parse_perm(a.perm)
    if pe is None:
        raise CliError("看不懂的权限 %r（见 perm list）" % a.perm, 2)
    key, scope = pe
    if key == "*":
        raise CliError("check 要写具体的权限键", 2)
    tgt = scope or a.target
    target = None
    if tgt:
        target = tgt if PERM_SCOPE.get(key) == "queue" else (
            dict(role=ROLE_SCOPES[tgt], active=True, name=tgt) if tgt in ROLE_SCOPES else mb.lookup(tgt))
    ok, why = mb.perm_decide(p, key, target)
    if a.json:
        emit_json({"name": p["name"], "perm": key, "target": tgt or "", "allowed": ok, "why": why})
    else:
        emit("%s %s %s%s（%s）" % (p["name"], "可以" if ok else "不能", key, (" → " + tgt) if tgt else "", why))
    return 0


def _norm_entries(xs):
    out = []
    for x in xs or []:
        pe = parse_perm(x)
        t = perm_text(*pe) if pe else x  # 看不懂的条目（可能来自新版本）原样保留
        if t not in out:
            out.append(t)
    return out


def cmd_perm_edit(a):
    op = a.op
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_controller(me)
    t = mb.lookup(a.name)
    if t["role"] == "controller" and t["active"]:
        raise CliError("主控永远拥有全部权限，不用授予或收回", 2)
    perms = parse_perm_args(mb, a.perms)
    if op != "reset" and not perms:
        raise CliError("perm %s 需要至少一个权限" % op, 2)

    def fn(cfg):
        p = [x for x in cfg["participants"] if x["name"] == t["name"]][0]
        allow, deny = _norm_entries(p.get("allow")), _norm_entries(p.get("deny"))
        if op == "grant":
            deny = [x for x in deny if x not in perms]
            allow += [x for x in perms if x not in allow]
        elif op == "revoke":
            allow = [x for x in allow if x not in perms]
            deny += [x for x in perms if x not in deny]
        elif perms:
            allow = [x for x in allow if x not in perms]
            deny = [x for x in deny if x not in perms]
        else:
            allow, deny = [], []
        p["allow"], p["deny"] = allow, deny
    mb.edit_config(me["name"], "perm-" + op, fn, {"name": t["name"], "perms": perms})
    word = {"grant": "授予你", "revoke": "收回你的", "reset": "把你的权限重置为角色预设"}[op]
    text = "%s %s%s" % (me["name"], word, ("：" + "、".join(perms)) if perms else "")
    if op == "reset" and perms:
        text = "%s 去掉了你的显式权限条目（回到角色预设）：%s" % (me["name"], "、".join(perms))
    notify_system(mb, [t["name"]], "perm", text + "。权限变动即时生效；用 perm show（MCP：mailbox_perm show）查看。",
                  me["name"], "权限变更")
    row = perm_row(mb, mb.get(t["name"]))
    if a.json:
        emit_json(row)
    else:
        emit("已%s %s 的权限%s" % ({"grant": "授予", "revoke": "收回", "reset": "重置"}[op], t["name"],
                                ("：" + "、".join(perms)) if perms else "（回到角色预设）"))
        emit_perm_row(row)
    return 0


def cmd_perm_preset(a):
    mb = open_mb(a)
    role = a.role
    cur = role_preset(mb.cfg, role)
    if a.set is None and not a.add and not a.remove and not a.reset:
        if a.json:
            emit_json({"role": role, "preset": cur, "custom": role in (mb.cfg.get("presets") or {})})
        else:
            emit("%s预设：%s" % (ROLE_ZH[role], "、".join(cur) or "（无）"))
        return 0
    me = mb.identity(a.me or "")
    mb.require_controller(me)
    if a.reset:
        new = None
    elif a.set is not None:
        new = parse_perm_args(mb, [a.set]) if a.set.strip() else []
    else:
        add, rm = parse_perm_args(mb, a.add), parse_perm_args(mb, a.remove)
        new = [x for x in _norm_entries(cur) if x not in rm] + [x for x in add if x not in cur]

    def fn(cfg):
        ps = dict(cfg.get("presets") or {})
        if new is None:
            ps.pop(role, None)
        else:
            ps[role] = new
        if ps:
            cfg["presets"] = ps
        else:
            cfg.pop("presets", None)
    cfg = mb.edit_config(me["name"], "perm-preset", fn, {"role": role, "preset": new})
    after = role_preset(cfg, role)
    notify_system(mb, [p["name"] for p in mb.participants(True) if p["role"] == role], "perm",
                  "%s 改了%s的角色预设：%s" % (me["name"], ROLE_ZH[role], "、".join(after) or "（无）"), me["name"], "权限变更")
    if a.json:
        emit_json({"role": role, "preset": after, "custom": new is not None})
    else:
        emit("%s预设已%s：%s" % (ROLE_ZH[role], "恢复为由通信策略推出" if new is None else "改为", "、".join(after) or "（无）"))
    return 0


def cmd_perm_token(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_controller(me)
    name = "user" if a.name == "user" else mb.lookup(a.name)["name"]
    tok = None if a.clear else "amb_" + secrets.token_hex(20)

    def fn(cfg):
        if name == "user":
            if tok:
                cfg["user_token_sha256"] = token_hash(tok)
            else:
                cfg.pop("user_token_sha256", None)
            return
        p = [x for x in cfg["participants"] if x["name"] == name][0]
        if tok:
            p["token_sha256"] = token_hash(tok)
        else:
            p.pop("token_sha256", None)
    mb.edit_config(me["name"], "perm-token", fn, {"name": name, "clear": bool(a.clear)})
    if not tok:
        emit("已清除 %s 的身份令牌：以后以 %s 的身份操作不再需要令牌" % (name, name))
        return 0
    if a.out:
        atomic_write(os.path.abspath(os.path.expanduser(a.out)), (tok + "\n").encode("utf-8"))
        where = "已写入 %s（只有本人可读的权限位；这是唯一一次拿到明文）" % slash(os.path.abspath(a.out))
    else:
        where = "令牌（只显示这一次，花名册里只存它的 SHA-256）：\n%s" % tok
    emit("已给 %s 设身份令牌。%s\n以后以 %s 的身份操作都要出示它：MCP 配置里设环境变量 AGENT_MAILBOX_TOKEN，"
         "或命令行加 --token-file <文件>。令牌不要写进信件、通话或通知（会被当成凭据拒收）。" % (name, where, name))
    return 0


# ------------------------------------------------------------------ 发信
def cmd_send(a):
    mb = open_mb(a)
    mb.require_writable()
    cfg = mb.cfg
    pol = cfg["policy"]
    me = mb.identity(a.me or "", need_active=False)
    if me is not USER:
        if not me["active"] and pol["inactive_send"] == "deny":
            raise CliError("%s 已不在任，不能发信；如需恢复请主控 roster update %s --activate assistant" % (
                me["name"], me["name"]), 2)
    text = hygiene(read_body(a), a.normalize)
    draft_title, body = split_title(text) if a.draft else (None, text.strip("\n"))
    tos = [x.strip() for x in re.split(r"[,，、]", a.to or "") if x.strip()]
    title_named = []
    if draft_title:
        unknown = []
        for x in title_parties(draft_title)[0]:
            v = mb.router.resolve(x)
            if v:
                title_named.append(v)
            else:
                unknown.append(x)
        if unknown and not a.force_title:
            raise CliError("草稿标题点名的 %s 不在花名册里（拼错了，或此人还没加入）；请核对标题，确有需要可加 --force-title" % (
                "、".join(unknown)), 2)
    if not tos and title_named:
        # 没写 --to 时按草稿标题点名的收件人投递
        role_to = {"@controller": "controller", "@all": "all", "@assistants": "assistants"}
        tos = [role_to.get(v, v) for v in title_named]
    if not tos:
        if me is USER or me["role"] == "controller":
            raise CliError("主控和用户发信须用 --to 指明收件人（或在草稿标题里点名）", 2)
        tos = ["controller"]
    assistants_but_me = [p for p in mb.participants(True)
                         if p["role"] == "assistant" and (me is USER or p["name"] != me["name"])]
    targets = []
    for t in tos:
        v = mb.find(t)
        if v == "@assistants":
            targets.extend(assistants_but_me)
        else:
            targets.append(v)
    uniq, seen = [], set()
    for t in targets:
        k = t if isinstance(t, str) else t["name"]
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    targets = uniq
    if not targets:
        raise CliError("没有可投递的收件人（全体助手展开后为空）", 2)
    broadcast = "@all" in targets
    people = [t for t in targets if not isinstance(t, str)]
    for t in people:
        if me is not USER and t["name"] == me["name"]:
            raise CliError("不能给自己发信", 2)
        if not t["active"] and not a.allow_inactive:
            raise CliError("%s 已不在任；确需投递旧箱请加 --allow-inactive" % t["name"], 2)
        if not mb.box_of_target(t):
            raise CliError("%s 没有收件箱，无法投递" % t["name"], 2)
    # 权限：旁听者默认不发信、助手互通与广播的旧策略都已折进角色预设，主控可按人授予或收回
    if broadcast:
        mb.require_perm(me, "broadcast")
    for t in people:
        mb.require_perm(me, "send", t)
    cc = []
    if me is not USER and me["role"] == "assistant":
        peers = [t for t in people if t["role"] == "assistant"]
        if peers and pol["assistant_to_assistant"] == "cc-controller":
            c = mb.controller()
            if all(t["name"] != c["name"] for t in people):
                cc.append(c)

    if draft_title and a.subject:
        raise CliError("草稿已有标题行，不要再给 --subject", 2)
    if not draft_title and not a.subject:
        raise CliError("没有标题：给 --subject，或在草稿第一行写「# 致 <收件人>：<主题>」", 2)
    keep_header = False
    first = next((l.strip() for l in body.split("\n") if l.strip()), "")
    if HEADER_LINE_RE.match(first):
        if not a.keep_header:
            raise CliError("草稿里已有「发件：…」信头行；请删掉由本工具现取时间生成，或加 --keep-header 保留原信头", 2)
        keep_header = True
    labels = ["全体" if t == "@all" else t["display"] for t in targets]
    title = draft_title or "# 致 %s：%s" % ("、".join(labels), a.subject.strip())
    if draft_title and not a.force_title:
        named = set(title_named)
        expanded = set()
        for v in named:
            if v == "@controller":
                expanded.add(mb.controller()["name"])
            elif v == "@assistants":
                expanded |= set(p["name"] for p in assistants_but_me)
            else:
                expanded.add(v)
        sent_to = set(t["name"] for t in targets if not isinstance(t, str)) | ({"@all"} if broadcast else set())
        missing = expanded - sent_to
        if missing:
            raise CliError("标题点名了 %s，但收件人里没有，会漏投；请补进 --to 或改标题（确有需要可加 --force-title）" % (
                "、".join(sorted(missing))), 2)
        for t in targets:
            if t == "@all":
                if "@all" not in named:
                    raise CliError("广播信的标题须以「# 致 全体：」开头（或加 --force-title）", 2)
                continue
            box = mb.box_of_target(t)
            if len(mb.members(box)) > 1 and t["name"] not in named and not (
                    "@assistants" in named and t["role"] == "assistant"):
                raise CliError("%s 的收件箱 %s 由多方共用，标题须点名收件人：「# 致 %s：…」（或加 --force-title）" % (
                    t["name"], box, t["display"]), 2)
    reply_ref = corr_ref = ""
    if a.reply_to or a.corrects:
        letters = mb.scan()[0]
        if a.reply_to:
            reply_ref = mb.resolve_ref(a.reply_to, letters, me).name[:-3]
        if a.corrects:
            corr_ref = mb.resolve_ref(a.corrects, letters, me).name[:-3]
    attach = attach_section(a.attach or [])
    tz = mb.tz()
    names_all = ["全体" if t == "@all" else t["name"] for t in targets]

    def render(num, fname):
        t = now_utc()
        fields = ["发件：" + sender_desc(me)]
        fields.append("收件：" + "、".join(names_all))  # 写参与方名，收信方按它路由，不怕显示名歧义
        if cc:
            fields.append("抄送：" + "、".join(c["name"] for c in cc))
        fields.append("时间：%s（%s）/ %s" % (t.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
                                          cfg["timezone_label"], iso_utc(t)))
        fields.append("编号：" + fname[:-3])
        if reply_ref:
            fields.append("回复：" + reply_ref)
        if corr_ref:
            fields.append("更正：" + corr_ref)
        if a.urgent:
            fields.append("紧急")
        sub = lambda s: s.replace("{{NUM}}", str(num)).replace("{{FILE}}", fname)
        parts = [sub(title), ""]
        if not keep_header:
            parts += ["｜".join(fields), ""]
        parts.append(sub(body))
        out = ("\n".join(parts).rstrip("\n") + "\n" + attach).encode("utf-8")
        if len(out) > cfg["max_bytes"]:
            raise CliError("信件 %d 字节，超过上限 %d；请精简，只写新增事实和精确路径" % (len(out), cfg["max_bytes"]), 2)
        if b"\r" in out or b"\xef\xbf\xbd" in out:
            raise CliError("渲染后的信件含 CR 或 U+FFFD，拒绝投递", 2)
        if not a.allow_secret:
            hits = scan_secrets(out.decode("utf-8"), cfg.get("secret_patterns"))
            if hits:
                raise CliError("疑似凭据（第 %s 行），拒绝投递；确认不是凭据可加 --allow-secret" % "、".join(
                    str(h[0]) for h in hits[:10]), 2)
        return out

    tag = "URGENT" if a.urgent else ""
    groups = []
    for t in targets + cc:
        box = cfg["broadcast_box"] if t == "@all" else mb.box_of_target(t)
        if box not in [g[0] for g in groups]:
            groups.append((box, t))
    # 先按各箱最长可能的文件名整体校验一遍（大小、凭据），避免多箱投递投了一半才报错
    big = 10 ** (int(cfg["pad"]) + 4) - 1
    for box, _ in groups:
        render(big, mb.fmt_name(box, mb.thread, big, tag))
    preview = render(0, "（投递时分配）.md")
    if a.dry_run:
        emit("（演练，未投递）将投到：%s" % "、".join(g[0] for g in groups))
        emit(preview.decode("utf-8").rstrip("\n"))
        return 0
    results = []
    for box, t in groups:
        r = mb.deliver(box, render, tag=tag, start=a.start)
        r["to"] = "全体" if t == "@all" else t["name"]
        results.append(r)
        mb.log("sent", {"at": iso_utc(), "from": me["name"], "to": names_all, "cc": [c["name"] for c in cc],
                        "file": r["file"], "sha256": r["sha256"], "bytes": r["bytes"], "urgent": bool(a.urgent),
                        "reply_to": reply_ref, "corrects": corr_ref})
    if me is not USER:
        own = [r["file"] for r in results if r["box"] in mb.boxes_of(me)]
        last = {"at": iso_utc(), "file": results[-1]["file"]}

        def fn(d):
            d["last_sent"] = last
            if own:
                d["notified"] = sorted(set(d.get("notified") or []) | set(own))
                d["read"] = sorted(set(d.get("read") or []) | set(own))
        try:
            mb.update_cursor(me["name"], fn)
        except (CliError, OSError) as e:
            warn("（信已投递；更新本方游标失败：%s）" % e)
    if a.json:
        emit_json({"sent": results})
    else:
        for r in results:
            emit("已投递 %s → %s｜%d 字节｜sha256 %s｜%s%s" % (
                r["file"], r["to"], r["bytes"], r["sha256"], "读回一致" if r["readbackEqual"] else "读回不一致！",
                ("｜" + "；".join(r["notes"])) if r["notes"] else ""))
    return 0 if all(r["readbackEqual"] for r in results) else 1


# ------------------------------------------------------------------ 收信
def collect_inbox(mb, me, letters, a):
    boxes = [a.box] if getattr(a, "box", None) else None
    scope = my_letters(mb, me, letters, boxes)
    st = mb.state(me, my_letters(mb, me, letters))
    read = set(st.get("read") or [])
    rows = []
    for l in scope:
        h = mb.info(l)
        if h is None:
            continue
        cls = mb.classify(me, l, h)
        if cls == "other" and not a.others:
            continue
        unread = l.name not in read
        if h.get("from") == me["name"]:
            cls, unread = "self", False  # 自己发的（如广播、交接前写进主控箱的）不算未读
        if not a.all and not unread:
            continue
        if a.since and l.num <= a.since:
            continue
        rows.append((l, h, cls, unread))
    rows.sort(key=lambda r: (r[1]["mtime"], r[0].name))
    return rows


def cmd_inbox(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    letters = mb.scan()[0]
    rows = collect_inbox(mb, me, letters, a)
    unread_total = len([r for r in rows if r[3]])
    if a.limit:
        rows = rows[-a.limit:]
    if a.json:
        emit_json({"me": me["name"], "boxes": mb.boxes_of(me), "unread": unread_total, "letters": [{
            "file": l.name, "path": slash(l.path), "box": l.box, "number": l.num, "urgent": l.urgent,
            "class": cls, "unread": unread, "title": h["title"], "from": h.get("from") or h.get("from_token"),
            "to": h.get("to"), "bytes": h["size"], "mtime": mb.fmt_time(h["mtime"])} for l, h, cls, unread in rows]})
        return 0
    emit("%s（%s）的收件｜箱 %s｜%s %d 封" % (me["name"], ROLE_ZH.get(me["role"]), ",".join(mb.boxes_of(me)),
                                       "列出" if a.all else "未读", len(rows) if a.all else unread_total))
    for l, h, cls, unread in rows:
        emit("  %s%s" % ("[未读]" if unread else "", mb.describe(l, h, cls, path=a.paths)))
    if not rows:
        emit("  （没有%s）" % ("信件" if a.all else "未读信件"))
    return 0


def cmd_read(a):
    mb = open_mb(a)
    me = mb.identity(a.me) if a.me else None
    letters = mb.scan()[0]
    picked = [mb.resolve_ref(r, letters, me) for r in a.refs]
    for l in picked:
        with open(l.path, "rb") as f:
            data = f.read()
        st = os.stat(l.path)
        emit("===== %s｜%d 字节｜%s｜sha256 %s" % (l.name, len(data), mb.fmt_time(st.st_mtime), sha256_bytes(data)))
        emit(data.decode("utf-8", "replace").replace("\r\n", "\n").rstrip("\n"))
    if me is not None and me is not USER and not a.peek and not mb.read_only:
        mb.state(me, my_letters(mb, me, letters))
        mb.mark(me["name"], [l.name for l in picked], read=True)
    return 0


def cmd_mark(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    letters = mb.scan()[0]
    if a.all:
        names = [l.name for l in my_letters(mb, me, letters)]
    elif a.from_num is not None:
        names = [l.name for l in my_letters(mb, me, letters) if l.num >= a.from_num and (not a.box or l.box == a.box)]
    else:
        if not a.refs:
            raise CliError("给出信件引用，或用 --all / --from-num", 2)
        names = [mb.resolve_ref(r, letters, me).name for r in a.refs]
    mb.state(me, my_letters(mb, me, letters))
    mb.mark(me["name"], names, read=True, unread=a.unread)
    emit("已标为%s %d 封" % ("未读" if a.unread else "已读", len(names)))
    return 0


def cmd_search(a):
    mb = open_mb(a)
    letters = mb.scan()[0]
    flags = re.I if a.ignore_case else 0
    try:
        rx = re.compile(a.pattern, flags)
    except re.error as e:
        raise CliError("正则无效：%s" % e, 2)
    hits = 0
    for l in letters:
        if a.box and l.box != a.box:
            continue
        if a.since and l.num <= a.since:
            continue
        h = mb.info(l)
        if h is None:
            continue
        if a.sender and h.get("from") != a.sender:
            continue
        if a.title_only:
            if rx.search(h["title"]):
                emit(mb.describe(l, h))
                hits += 1
        else:
            with open(l.path, "rb") as f:
                text = f.read().decode("utf-8", "replace")
            lines = [(i + 1, s) for i, s in enumerate(text.splitlines()) if rx.search(s)]
            if lines:
                hits += 1
                emit(mb.describe(l, h))
                for i, s in lines[:5]:
                    emit("    %d: %s" % (i, clip(s.strip(), 200)))
        if a.limit and hits >= a.limit:
            break
    if not hits:
        emit("（没有匹配）")
    return 0


# ------------------------------------------------------------------ 监视
class Watcher(object):
    def __init__(self, mb, me, a):
        self.mb, self.me, self.a = mb, me, a
        self.boxes = mb.boxes_of(me)
        self.ro = mb.read_only
        self.sizes = {}
        self.events = []
        self.digest = []
        self.new_notified = set()
        self.unstable = False
        self.real = False
        self.waits = []
        self.presence = None
        self.started = time.time()
        self.rev = mb.cfg.get("revision")
        self.cfg_error = False
        self.notify_mark, self.talk_mark = 0, {}
        # 嵌入式用法（MCP 服务）：事件交给回调而不打印；stop_event 置位即退出循环
        self.sink = getattr(a, "sink", None)
        self.stop_event = getattr(a, "stop_event", None)

    def refresh_roster(self):
        """每轮重读花名册：交接、增减助手即时生效；本方角色或收件箱变了就报 ROSTER。返回 False 表示本方已不在任。"""
        old_cfg, old_router = self.mb._cfg, self.mb._router
        self.mb._cfg = None
        self.mb._router = None
        try:
            cfg = self.mb.cfg
        except (CliError, OSError) as e:
            self.mb._cfg, self.mb._router = old_cfg, old_router
            if not self.cfg_error:
                self.cfg_error = True
                self.add("ERROR", "花名册暂时读不了，沿用上一版：%s" % str(e).split("\n")[0], real=False)
            return True
        self.cfg_error = False
        rev = cfg.get("revision")
        if rev == self.rev:
            return True
        self.mb._cache = {}
        me = self.mb.get(self.me["name"])
        if me is None or not me["active"]:
            self.add("ROSTER", "%s 已不在在任名单里（现为%s）；监视结束，后续听用户或主控安排" % (
                self.me["name"], ROLE_ZH.get(me["role"], me["role"]) if me else "不在花名册"))
            return False
        boxes = self.mb.boxes_of(me)
        if me["role"] != self.me["role"] or boxes != self.boxes:
            self.add("ROSTER", "本方角色或收件箱已变：%s（%s）→ %s（%s）" % (
                ROLE_ZH.get(self.me["role"], self.me["role"]), ",".join(self.boxes),
                ROLE_ZH.get(me["role"], me["role"]), ",".join(boxes)))
            # 交接时继承的已读记录写在游标文件里，这里并进内存，避免把主控箱历史信当新信报
            saved = read_json_strict(self.mb.cursor_path(me["name"])) or {}
            self.notified |= set(saved.get("notified") or [])
        self.me, self.boxes, self.rev = me, boxes, rev
        return True

    def add(self, kind, text, sub=None, real=True, **extra):
        e = {"kind": kind, "text": text, "sub": sub or []}
        e.update(extra)
        self.events.append(e)
        if real:
            self.real = True

    def sync_cursor(self):
        """游标文件变了就以它为准重算「已提醒」：别处 mark --unread 去掉的信会重新报，别处已读的信不再报。"""
        if self.ro:
            return
        try:
            mt = os.stat(self.mb.cursor_path(self.me["name"])).st_mtime_ns
        except OSError:
            return
        if mt == getattr(self, "cursor_mtime", None):
            return
        d = load_json_quiet(self.mb.cursor_path(self.me["name"]))
        if d is None:
            return
        self.cursor_mtime = mt
        self.read_set = set(d.get("read") or [])
        self.notified = set(d.get("notified") or []) | self.new_notified
        self.notify_mark = max(self.notify_mark, int(d.get("notify_seen") or 0))
        for k, v in (d.get("talk_seen") or {}).items():
            self.talk_mark[k] = max(self.talk_mark.get(k, 0), int(v or 0))

    def setup(self):
        letters = self.mb.scan()[0]
        self.st = copy.deepcopy(self.mb.state(self.me, my_letters(self.mb, self.me, letters)))
        self.notified = set(self.st.get("notified") or [])
        self.notify_mark, self.talk_mark = seen_pointers(self.mb, self.me["name"])
        for k in ("ledger", "journals", "mention_marks", "reminders", "waits_fired"):
            self.st.setdefault(k, {})
        self.st.setdefault("journal_globs", [])
        if not self.st.get("last_real_at"):
            self.st["last_real_at"] = iso_utc()
        self.markers = list(self.a.marker or [])
        if self.a.ledger and not self.markers:
            self.markers = [self.me["name"]] + [x for x in self.me["aliases"] if len(x) >= 3]
        # 基线只在某项监视第一次启用时建；之后新出现的日志文件从头读、新出现的箱从 0 号查，
        # 两次监视之间到的内容照样会报
        for path in self.a.ledger or []:
            ap = os.path.abspath(path)
            if ap not in self.st["ledger"]:
                self.st["ledger"][ap] = self.ledger_snapshot(ap)
        new_globs = [g for g in (self.a.journal or []) if g not in self.st["journal_globs"]]
        if new_globs:
            for path in self.journal_files(new_globs):
                if path not in self.st["journals"]:
                    try:
                        self.st["journals"][path] = os.path.getsize(path)
                    except OSError:
                        pass
            self.st["journal_globs"] = sorted(set(self.st["journal_globs"]) | set(new_globs))
        if self.a.mentions and not self.st.get("mention_init"):
            marks = self.st["mention_marks"]
            for l in letters:
                marks[l.box] = max(marks.get(l.box, 0), l.num)
            self.st["mention_init"] = True
        for p in self.a.wait_file or []:
            self.waits.append((os.path.abspath(p), "", "arg"))
        if self.a.wait_from:
            for line in read_file_arg(self.a.wait_from).decode("utf-8-sig").splitlines():
                if line.strip():
                    self.waits.append((os.path.abspath(line.strip()), "", "file"))
        self.persist()  # 无论这一轮以事件还是超时结束，基线都先落盘
        if not self.ro:
            self.presence = Presence(self.mb, self.me, "once" if self.a.once else "loop", not self.a.no_takeover)
            self.presence.claim()

    # 信件
    def poll_mail(self, letters):
        now = time.time()
        self.sync_cursor()
        read = getattr(self, "read_set", set())
        for l in letters:
            if l.box not in self.boxes or l.name in self.notified:
                continue
            if l.name in read:  # 监视还没扫到就已经读过了：只记为提醒过，不再报
                self.notified.add(l.name)
                self.new_notified.add(l.name)
                continue
            try:
                st = os.stat(l.path)
            except OSError:
                continue
            if now - st.st_mtime < 2.0 and self.sizes.get(l.name) != st.st_size:
                self.sizes[l.name] = st.st_size
                self.unstable = True
                continue
            self.sizes.pop(l.name, None)
            h = self.mb.info(l)
            if h is None:
                continue
            self.notified.add(l.name)
            self.new_notified.add(l.name)
            if h.get("from") == self.me["name"]:
                continue
            cls = self.mb.classify(self.me, l, h)
            line = self.mb.describe(l, h, cls)
            if cls == "other":
                if self.a.others == "trigger":
                    self.add("MAIL-OTHER", line)
                elif self.a.others == "digest":
                    self.digest.append(line)
                continue
            self.add({"mine": "MAIL", "shared": "MAIL-SHARED", "all": "MAIL-ALL"}[cls], line, file=l.name,
                     urgent=l.urgent)

    # 通知流与通话（按已看指针增量读，文件没变不重读）
    def poll_notify(self):
        name = self.me["name"]
        try:
            items = jsonl_records(feed_path(self.mb, name))
        except CliError as e:
            self.add("ERROR", "通知流读不了：%s" % str(e).split("\n")[0], real=False)
            return
        for e in items:
            if e["seq"] <= self.notify_mark:
                continue
            self.notify_mark = e["seq"]
            lines = (e.get("text") or "").split("\n")
            self.add("NOTIFY", "%s#%d [%s] %s%s：%s" % (
                "[急]" if e.get("urgent") else "", e["seq"], e.get("kind") or "note", e.get("from") or "系统",
                ("「%s」" % e["title"]) if e.get("title") else "", clip(lines[0], 300)),
                [clip(l, 300) for l in lines[1:40]], seq=e["seq"], urgent=bool(e.get("urgent")),
                notify_kind=e.get("kind") or "note")

    def poll_talk(self):
        name = self.me["name"]
        for meta in talk_mine(self.mb, name):
            cid = meta["id"]
            m = talk_member(meta, name)
            base = max(int(m.get("since") or 0), self.talk_mark.get(cid, 0))
            try:
                new = [x for x in talk_log(self.mb, cid) if x["seq"] > base]
            except CliError:
                continue
            if not new:
                continue
            self.talk_mark[cid] = new[-1]["seq"]
            topic = ("「%s」" % meta["topic"]) if meta.get("topic") else ""
            for x in new:
                if x.get("from") == name:
                    continue
                if x.get("kind", "msg") != "msg":
                    self.add("TALK", "%s%s #%d · %s" % (cid, topic, x["seq"], x.get("text") or ""), conv=cid,
                             seq=x["seq"], sender=x.get("from") or "")
                    continue
                lines = (x.get("text") or "").split("\n")
                sub = [clip(l, 300) for l in lines[1:40]]
                if len(lines) > 40:
                    sub.append("…（全文用 talk read %s 查看）" % cid)
                self.add("TALK", "%s%s%s #%d %s%s：%s" % (
                    "[急]" if x.get("urgent") else "", cid, topic, x["seq"], x.get("from") or "?",
                    ("（回复 #%d）" % x["reply_to"]) if x.get("reply_to") else "", clip(lines[0], 300)), sub,
                    conv=cid, seq=x["seq"], sender=x.get("from") or "", urgent=bool(x.get("urgent")))

    def mention_keys(self):
        keys = [self.me["name"], self.me["display"]] + list(self.me["aliases"])
        if self.me.get("box"):
            keys.append("to-%s-" % self.me["box"])
        return sorted(set(norm_text(k) for k in keys if len(norm_text(k)) >= 3 and norm_text(k) not in ROLE_WORDS_NORM))

    def poll_mentions(self, letters):
        marks = self.st["mention_marks"]
        keys = self.mention_keys()
        now = time.time()
        for l in letters:
            if l.box in self.boxes:
                # 本方箱里的信走 MAIL，不重复报；记号照样往前推，日后此箱不再属于本方时不会翻出旧信
                if l.num > marks.get(l.box, 0):
                    marks[l.box] = l.num
                continue
            if l.box not in marks:
                marks[l.box] = 0  # 监视启用后才出现的新箱，从头查
            if l.num <= marks[l.box]:
                continue
            try:
                st = os.stat(l.path)
                if now - st.st_mtime < 2.0 and self.sizes.get(l.name) != st.st_size:
                    self.sizes[l.name] = st.st_size
                    self.unstable = True
                    continue
                with open(l.path, "rb") as f:
                    text = f.read(65536).decode("utf-8", "replace").lower()
            except OSError:
                continue
            self.sizes.pop(l.name, None)
            marks[l.box] = max(marks[l.box], l.num)
            h = self.mb.info(l)
            if h is None or h.get("from") == self.me["name"]:
                continue
            if any(k in text for k in keys):
                self.add("MENTION", self.mb.describe(l, h))

    # 总账
    def marker_lines(self, ap):
        with open(ap, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        return [l for l in text.splitlines() if any(m in l for m in self.markers)]

    def ledger_snapshot(self, ap):
        try:
            mt = os.stat(ap).st_mtime
            lines = self.marker_lines(ap)
        except OSError:
            return {"mtime": 0, "hashes": [], "count": 0}
        return {"mtime": mt, "hashes": sorted(set(sha1_text(l) for l in lines)), "count": len(lines)}

    def poll_ledgers(self):
        for ap, s in list(self.st["ledger"].items()):
            try:
                st = os.stat(ap)
            except OSError:
                continue
            if st.st_mtime == s.get("mtime"):
                continue
            try:
                lines = self.marker_lines(ap)
            except OSError:
                continue
            old = set(s.get("hashes") or [])
            fresh = [l for l in lines if sha1_text(l) not in old]
            self.st["ledger"][ap] = {"mtime": st.st_mtime, "hashes": sorted(set(sha1_text(l) for l in lines)),
                                     "count": len(lines)}
            if fresh:
                self.add("LEDGER", "%s：%d 条含 %s 的新行或改动行（文件时间 %s，现共 %d 行）" % (
                    slash(ap), len(fresh), "/".join(self.markers), self.mb.fmt_time(st.st_mtime), len(lines)),
                    [clip(l, 300) for l in fresh[-5:]])

    # 工作流日志（JSONL）
    def journal_files(self, globs=None):
        out = []
        for pat in (self.a.journal or []) if globs is None else globs:
            out.extend(glob.glob(pat, recursive=True))
        return sorted(set(os.path.abspath(p) for p in out if os.path.isfile(p)))

    def poll_journals(self):
        if not self.a.journal:
            return
        types = set(x.strip() for x in (self.a.journal_types or "").split(",") if x.strip())
        js = self.st["journals"]
        for path in self.journal_files():
            off = js.get(path, 0)
            try:
                size = os.path.getsize(path)
                if size < off:
                    off = 0
                if size == off:
                    continue
                with open(path, "rb") as f:
                    f.seek(off)
                    chunk = f.read(size - off)
            except OSError:
                continue
            cut = chunk.rfind(b"\n")
            if cut < 0:
                continue
            js[path] = off + cut + 1
            for raw in chunk[:cut].split(b"\n"):
                try:
                    e = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(e, dict) or (types and str(e.get("type")) not in types):
                    continue
                who, info, r = str(e.get("label") or ""), "", e.get("result")
                if isinstance(r, dict):
                    who = who or str(r.get("role") or "")
                    info = " ".join(str(x) for x in (r.get("status"), r.get("verdict")) if x)
                elif e.get("type") == "result" and r is None:
                    info = "NULL"
                self.add("JOURNAL", "%s %s %s %s" % (os.path.basename(os.path.dirname(path)), e.get("type"), who,
                                                    info))

    # 等待文件
    def poll_waits(self):
        """命令行给的等待文件只在启动时读；tasks 里登记的每轮重读，登记后不必重挂监视。"""
        keep, fired = [], self.st["waits_fired"]
        listed = [(w.get("path"), w.get("note") or "", "tasks")
                  for w in load_tasks(self.mb, self.me["name"]).get("waits") or [] if w.get("path")]
        for path, note, src in self.waits + listed:
            if not path or not os.path.exists(path):
                if src != "tasks":
                    keep.append((path, note, src))
                continue
            try:
                st = os.stat(path)
            except OSError:
                if src != "tasks":
                    keep.append((path, note, src))
                continue
            mt = st.st_mtime
            if fired.get(path) == mt:
                continue
            # 刚出现、可能还在写的文件先等它稳定（大小一轮不变，或已 2 秒没动），免得报出写了一半的结果
            if os.path.isfile(path) and time.time() - mt < 2.0 and self.sizes.get(path) != st.st_size:
                self.sizes[path] = st.st_size
                self.unstable = True
                if src != "tasks":
                    keep.append((path, note, src))
                continue
            self.sizes.pop(path, None)
            fired[path] = mt
            sub = []
            if os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        raw = f.read(4097)
                    body = raw[:4096].decode("utf-8", "replace")
                    sub = body.splitlines()[:60] + (["…（超过 4 KB 已截断）"] if len(raw) > 4096 else [])
                except OSError:
                    pass
            self.add("WAITFILE", "%s 已出现%s" % (slash(path), "（%s）" % note if note else ""), sub)
            if src == "tasks" and not self.ro:
                update_tasks(self.mb, self.me["name"],
                             lambda d: d.__setitem__("waits", [w for w in d["waits"] if w.get("path") != path]))
        self.waits = keep

    # 提醒
    def poll_reminders(self):
        now = time.time()
        rem = self.st["reminders"]
        tasks = load_tasks(self.mb, self.me["name"])
        todo = [t for t in tasks.get("tasks") or [] if t.get("status") in ("open", "waiting", "blocked")]
        w = tasks.get("waiting_on")
        if self.a.remind_status and (todo or w):
            # 每次都从游标重读上次发信时间：循环模式下本方期间发的信也要算上
            cur = load_json_quiet(self.mb.cursor_path(self.me["name"])) or {}
            last_sent = (cur.get("last_sent") or self.st.get("last_sent") or {}).get("at")
            last = ts_of(last_sent) or ts_of(self.st.get("baseline_at")) or self.started
            gap = self.a.remind_status * 60
            if now - last >= gap and now - float(rem.get("status_at") or 0) >= gap:
                rem["status_at"] = now
                self.add("STATUS-DUE", "距本方上一封去信已 %d 分钟，仍有 %d 项未完成%s：请给主控发一封简短状态信"
                                       "（只报事实：在哪一步、预计时刻、有无中断）" % (
                                           (now - last) // 60, len(todo), "、在等「%s」" % w["what"] if w else ""),
                         real=False)
        if self.a.remind_wait and w and not self.ro:
            since = ts_of(w.get("since")) or now
            n = int(w.get("reminded") or 0)
            if n < 3 and now - since >= self.a.remind_wait * 60 * (2 ** n):
                def bump(d):
                    # 只给同一个等待对象计数；期间被换成新的等待对象就不动它
                    if d.get("waiting_on") and d["waiting_on"].get("since") == w.get("since"):
                        d["waiting_on"]["reminded"] = n + 1
                update_tasks(self.mb, self.me["name"], bump)
                self.add("WAIT-STALE", "等待「%s」已 %d 分钟（第 %d/3 次提醒）：先查信箱是否已有回复；确实没有，"
                                       "再发一封简短的状态询问（只问状态，不重复正文），或调整 waiting-on" % (
                                           w["what"], (now - since) // 60, n + 1), real=False)
        if self.a.nudge and todo and not self.events:
            k = int(rem.get("nudges") or 0)
            if k < 4:
                gap = min(self.a.nudge * 60 * (2 ** k), 7200)
                base = max(ts_of(self.st.get("last_real_at")) or self.started, float(rem.get("nudge_at") or 0))
                if now - base >= gap:
                    rem["nudges"] = k + 1
                    rem["nudge_at"] = now
                    self.add("HEARTBEAT", "已 %d 分钟没有新事件，仍有 %d 项未完成（第 %d/4 次心跳）：读备忘和任务清单，"
                                          "看能否在授权范围内推进；只能等外部就用 tasks waiting-on 登记后结束本轮" % (
                                              (now - base) // 60, len(todo), k + 1), real=False)

    def emit_digest(self, dig, stamp):
        if self.a.json:
            for d in dig:
                emit(json.dumps({"kind": "DIGEST", "text": d, "at": stamp, "me": self.me["name"]}, ensure_ascii=False))
        elif dig:
            emit("同期写给他人的信（仅供知悉，不处理）：")
            for d in dig:
                emit("  · " + d)

    def flush(self, only_notified=False):
        evs, self.events = self.events, []
        dig, self.digest = self.digest, []
        stamp = iso_utc()
        if self.real:
            self.st["last_real_at"] = stamp
            self.st["reminders"]["nudges"] = 0
            self.real = False
        if self.sink is not None:
            tasks = load_tasks(self.mb, self.me["name"])
            self.sink({"at": stamp, "me": self.me["name"], "events": evs, "digest": dig,
                       "tasks": [t for t in tasks.get("tasks") or [] if t.get("status") != "done"],
                       "waiting_on": tasks.get("waiting_on")})
        elif self.a.json:
            for e in evs:
                emit(json.dumps(dict(e, at=stamp, me=self.me["name"]), ensure_ascii=False))
            self.emit_digest(dig, stamp)
        else:
            emit("[agent-mailbox %s] %s 的新事件 %d 条" % (self.mb.fmt_time(time.time()), self.me["name"], len(evs)))
            for e in evs:
                emit("%s %s" % (e["kind"], e["text"]))
                for s in e["sub"]:
                    emit("    " + s)
            self.emit_digest(dig, stamp)
            tasks = load_tasks(self.mb, self.me["name"])
            todo = [t for t in tasks.get("tasks") or [] if t.get("status") != "done"]
            if todo:
                emit("当前任务：" + "；".join("%s[%s]%s" % (t["id"], t["status"], ("→" + t["next"]) if t.get("next") else "")
                                         for t in todo))
            if tasks.get("waiting_on"):
                emit("等待中：%s（自 %s）" % (tasks["waiting_on"]["what"], tasks["waiting_on"].get("since")))
            if not self.a.no_rules:
                emit(RULES)
        self.persist(only_notified)

    def persist(self, only_notified=False):
        """把已提醒集合与监视进度并进游标。各项按「谁的进度更新取谁」合并：
        被接管的旧监视、并行的两个监视都不会把别人更新过的进度写回旧值。"""
        if self.ro:
            return
        new, st = set(self.new_notified), self.st

        def newer(a, b):
            return max(float(a or 0), float(b or 0))

        seen_n, seen_t = (self.notify_mark, dict(self.talk_mark)) if self.sink is None else (None, None)

        def fn(d):
            d["notified"] = sorted(set(d.get("notified") or []) | new)
            d.setdefault("baseline_at", st.get("baseline_at") or iso_utc())
            if seen_n is not None:
                d["notify_seen"] = max(int(d.get("notify_seen") or 0), seen_n)
                ts = dict(d.get("talk_seen") or {})
                for k, v in seen_t.items():
                    ts[k] = max(int(ts.get(k) or 0), int(v))
                d["talk_seen"] = ts
            if only_notified:
                return
            j = dict(d.get("journals") or {})
            for k, v in st["journals"].items():
                j[k] = max(int(j.get(k) or 0), int(v or 0))
            d["journals"] = j
            mm = dict(d.get("mention_marks") or {})
            for k, v in st["mention_marks"].items():
                mm[k] = max(int(mm.get(k) or 0), int(v or 0))
            d["mention_marks"] = mm
            lg = dict(d.get("ledger") or {})
            for k, v in st["ledger"].items():
                if k not in lg or float(v.get("mtime") or 0) >= float(lg[k].get("mtime") or 0):
                    lg[k] = v
            d["ledger"] = lg
            wf = dict(d.get("waits_fired") or {})
            for k, v in st["waits_fired"].items():
                wf[k] = newer(wf.get(k), v)
            d["waits_fired"] = wf
            rem = dict(d.get("reminders") or {})
            for k in ("status_at", "nudge_at"):
                if k in st["reminders"] or k in rem:
                    rem[k] = newer(rem.get(k), st["reminders"].get(k))
            if "nudges" in st["reminders"]:
                rem["nudges"] = st["reminders"]["nudges"]
            d["reminders"] = rem
            d["journal_globs"] = sorted(set(d.get("journal_globs") or []) | set(st.get("journal_globs") or []))
            if st.get("mention_init"):
                d["mention_init"] = True
            lr = newer(ts_of(d.get("last_real_at")), ts_of(st.get("last_real_at")))
            d["last_real_at"] = iso_utc(dt.datetime.fromtimestamp(lr, UTC)) if lr else iso_utc()
        self.mb.update_cursor(self.me["name"], fn)
        self.new_notified = set()

    def run(self):
        self.setup()
        once = self.a.once
        seconds = self.a.seconds if self.a.seconds is not None else (1790 if once else 0)
        deadline = time.time() + seconds if seconds > 0 else None
        first_at = None
        reason = "exit"
        try:
            while True:
                if self.stop_event is not None and self.stop_event.is_set():
                    if self.new_notified:
                        self.persist()
                    reason = "stopped"
                    return 0
                if self.presence and not self.presence.still_mine():
                    cur = load_json_quiet(self.presence.path) or {}
                    self.add("SUPERSEDED", "%s 的监视已被另一进程接管（pid %s，%s）；若不是你自己重挂的，说明有两个会话在用同一身份" % (
                        self.me["name"], cur.get("pid"), cur.get("host")), real=False)
                    self.flush(only_notified=True)  # 只并入已提醒集合，不碰接管者的监视进度
                    reason = "superseded"
                    return 4
                self.unstable = False
                if not self.refresh_roster():
                    self.flush()
                    reason = "removed"
                    return 0
                try:
                    letters = self.mb.scan()[0]
                except CliError as e:
                    self.add("ERROR", str(e), real=False)
                    letters = []
                self.poll_mail(letters)
                self.poll_notify()
                self.poll_talk()
                if self.a.mentions:
                    self.poll_mentions(letters)
                self.poll_ledgers()
                self.poll_journals()
                self.poll_waits()
                if not self.events:
                    self.poll_reminders()
                if self.presence:
                    self.presence.beat()
                now = time.time()
                if self.events:
                    if first_at is None:
                        first_at = now
                    settled = not self.unstable and now - first_at >= self.a.coalesce
                    if not once or settled or now - first_at > 15:
                        self.flush()
                        first_at = None
                        if once:
                            reason = "event"
                            return 0
                elif self.new_notified and not once:
                    self.persist()
                if deadline and now >= deadline:
                    if self.events:
                        self.flush()
                        reason = "event"
                        return 0
                    dig, self.digest = self.digest, []
                    self.emit_digest(dig, iso_utc())  # 写给他人的信已记为提醒过，超时退出前也要交代
                    self.persist()
                    if self.a.json:
                        emit(json.dumps({"kind": "TIMEOUT", "at": iso_utc(), "me": self.me["name"]}, ensure_ascii=False))
                    else:
                        emit("TIMEOUT 监视 %d 秒无新事件（%s）；需要时重挂 watch" % (seconds, self.me["name"]))
                    reason = "timeout"
                    return 3 if once else 0
                pause = 1.0 if (self.events or self.unstable) else self.a.interval
                if self.stop_event is not None:
                    self.stop_event.wait(pause)
                else:
                    time.sleep(pause)
        except KeyboardInterrupt:
            reason = "interrupted"
            return 130
        finally:
            if self.presence:
                self.presence.stop(reason)


def cmd_watch(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    return Watcher(mb, me, a).run()


# ------------------------------------------------------------------ 任务、备忘、状态板
def cmd_tasks(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    act = a.action or "show"
    if act == "show":
        d = load_tasks(mb, me["name"])
        if a.json:
            emit_json(d)
            return 0
        emit("%s 的任务（%d 项）" % (me["name"], len(d["tasks"])))
        for t in d["tasks"]:
            emit("  %-30s [%s] %s" % (t["id"], t["status"], t.get("next") or ""))
        if d.get("waiting_on"):
            emit("  等待：%s（自 %s，已提醒 %s 次）" % (d["waiting_on"]["what"], d["waiting_on"].get("since"),
                                              d["waiting_on"].get("reminded", 0)))
        for w in d["waits"]:
            emit("  等文件：%s %s" % (w["path"], w.get("note") or ""))
        return 0
    if act in ("set", "remove") and not a.id:
        raise CliError("tasks %s 需要任务编号" % act, 2)
    if act in ("wait-add", "wait-remove") and not a.path:
        raise CliError("tasks %s 需要 --path" % act, 2)
    if act == "set":
        if a.status not in TASK_STATES:
            raise CliError("状态只能是 %s" % " / ".join(TASK_STATES), 2)

        def fn(d):
            item = {"id": clip(a.id, 120), "status": a.status, "updated": iso_utc()}
            if a.next:
                item["next"] = clip(a.next, 300)
            ts = [t for t in d["tasks"] if t["id"] != item["id"]]
            if len(ts) >= 30:
                raise CliError("任务清单最多 30 项，请先把已完成的 remove 掉", 2)
            d["tasks"] = ts + [item]
    elif act == "remove":
        def fn(d):
            d["tasks"] = [t for t in d["tasks"] if t["id"] != a.id]
    elif act == "replace":
        raw = read_file_arg(a.file) if a.file else read_stdin_bytes()
        try:
            items = json.loads(hygiene(raw, True))
        except ValueError as e:
            raise CliError("任务清单不是合法 JSON：%s" % e, 2)
        if not isinstance(items, list) or len(items) > 30:
            raise CliError("任务清单须是不超过 30 项的 JSON 数组", 2)
        clean = []
        for t in items:
            if not isinstance(t, dict) or not t.get("id") or t.get("status") not in TASK_STATES:
                raise CliError("每项须有 id 和 status（%s）：%r" % (" / ".join(TASK_STATES), t), 2)
            item = {"id": clip(t["id"], 120), "status": t["status"], "updated": iso_utc()}
            if t.get("next"):
                item["next"] = clip(t["next"], 300)
            clean.append(item)

        def fn(d):
            d["tasks"] = clean
    elif act == "waiting-on":
        if not a.clear and not a.what:
            raise CliError("tasks waiting-on 需要 --what <在等谁、等什么>，清除用 --clear", 2)

        def fn(d):
            d["waiting_on"] = None if a.clear else {"what": clip(a.what, 300), "since": iso_utc(), "reminded": 0}
    elif act == "wait-add":
        ap = os.path.abspath(a.path)

        def fn(d):
            if not any(w.get("path") == ap for w in d["waits"]):
                d["waits"].append({"path": ap, "note": a.note or "", "added": iso_utc()})
    elif act == "wait-remove":
        ap = os.path.abspath(a.path)

        def fn(d):
            d["waits"] = [w for w in d["waits"] if w.get("path") != ap]
    else:
        raise CliError("未知的 tasks 动作：%s" % act, 2)
    update_tasks(mb, me["name"], fn)
    emit("已更新 %s 的任务清单（%s）" % (me["name"], act))
    return 0


def cmd_notes(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    path = notes_path(mb, me["name"])
    act = a.action or "show"
    if act == "path":
        emit(slash(path))
        return 0
    if act == "show":
        try:
            with open(path, "rb") as f:
                emit(f.read().decode("utf-8", "replace").rstrip("\n"))
        except FileNotFoundError:
            emit("（%s 还没有续接备忘：%s）" % (me["name"], slash(path)))
        return 0
    mb.require_writable()
    if a.file:
        with open(a.file, "rb") as f:
            text = hygiene(f.read(), True)
    elif a.text is not None:
        text = a.text
    else:
        text = hygiene(read_stdin_bytes(), True)
    with FileLock(mb.p("locks", "notes-%s.lock" % me["name"])):
        if act == "replace":
            atomic_write(path, (text.rstrip("\n") + "\n").encode("utf-8"))
        elif act == "append":
            try:
                with open(path, "rb") as f:
                    old = f.read()
            except FileNotFoundError:
                old = ("# %s 续接备忘\n\n（每轮结束前更新：当前任务、已交付件、等待对象、下一步。）\n" % me["name"]).encode("utf-8")
            stamp = mb.fmt_time(time.time())
            atomic_write(path, old.rstrip(b"\n") + ("\n\n## %s\n\n%s\n" % (stamp, text.strip("\n"))).encode("utf-8"))
        else:
            raise CliError("未知的 notes 动作：%s" % act, 2)
    emit("已%s %s" % ("覆盖" if act == "replace" else "追加", slash(path)))
    return 0


def board_name(mb, box, key):
    th = mb.thread
    return "to-%s-%s%s-STATUS.json" % (box, (th + "-") if th else "", key)


def cmd_status(a):
    mb = open_mb(a)
    act = a.action or "list"
    _, boards, _ = mb.scan()
    if act == "list":
        rows = []
        for n, m in sorted(boards):
            p = os.path.join(mb.root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            d = load_json_quiet(p) or {}
            w = (d.get("_mailbox") or {}).get("writer") or d.get("writer") or "?"
            rows.append({"file": n, "box": m.group("box"), "key": m.group("key"), "writer": w, "bytes": st.st_size,
                         "age_s": int(time.time() - st.st_mtime)})
        if a.json:
            emit_json(rows)
        else:
            for r in rows:
                emit("  %s｜%s｜%dB｜%s｜写入方 %s" % (r["key"], r["box"], r["bytes"], ago(r["age_s"]), clip(r["writer"], 60)))
            if not rows:
                emit("（没有状态板）")
        return 0
    key = (a.key or "").upper()
    if not KEY_RE.match(key):
        raise CliError("状态板键须是大写字母数字，可用 - 连接，例如 PINWIN-W1", 2)
    me = mb.identity(a.me) if a.me else None
    if act == "get":
        cands = [n for n, m in boards if m.group("key") == key and (not a.box or m.group("box") == a.box)]
        if not cands:
            raise CliError("没有状态板 %s" % key, 2)
        for n in cands:
            p = os.path.join(mb.root, n)
            with open(p, "rb") as f:
                data = f.read()
            emit("===== %s｜%s｜sha256 %s" % (n, ago(time.time() - os.stat(p).st_mtime), sha256_bytes(data)))
            emit(data.decode("utf-8", "replace").rstrip("\n"))
        return 0
    if act == "put":
        mb.require_writable()
        if me is None:
            raise CliError("写状态板要 --as 本方身份", 2)
        mb.require_perm(me, "status")
        if a.to:
            t = mb.find(a.to)
            if isinstance(t, str):
                raise CliError("状态板只能写给一个参与方", 2)
            box = mb.box_of_target(t)
        else:
            box = mb.cfg["controller_box"]
        raw = read_file_arg(a.file) if a.file else read_stdin_bytes()
        try:
            obj = json.loads(hygiene(raw, True))
        except ValueError as e:
            raise CliError("状态板内容不是合法 JSON：%s" % e, 2)
        if not isinstance(obj, dict):
            raise CliError("状态板内容须是 JSON 对象", 2)
        obj["_mailbox"] = {"writer": me["name"], "writtenAtUtc": iso_utc()}
        data = dump_json(obj)
        path = os.path.join(mb.root, board_name(mb, box, key))
        atomic_write(path, data)
        emit("已更新状态板 %s｜%d 字节｜sha256 %s" % (os.path.basename(path), len(data), sha256_bytes(data)))
        return 0
    raise CliError("未知的 status 动作：%s" % act, 2)


# ------------------------------------------------------------------ 通知、通话、队列、加入：命令
def text_arg(a, what, required=True):
    f = getattr(a, "file", None)
    if f:
        return read_file_arg(f)
    t = getattr(a, "text", None)
    if t is not None:
        return t
    if getattr(a, "stdin", False):
        return read_stdin_bytes()
    if required:
        raise CliError("没有%s：用 --text 或 --file" % what, 2)
    return None


def fmt_when(mb, iso):
    t = ts_of(iso)
    return mb.fmt_time(t) if t else "?"


def seen_upto(seen, unseen_sorted, shown):
    """只把「连续展示过」的未看条目记为已看：被 --limit 截掉的较早条目留到下次。"""
    s = seen
    for e in unseen_sorted:
        if e["seq"] <= seen:
            continue
        if e["seq"] not in shown:
            break
        s = e["seq"]
    return s


def emit_lines(prefix, text):
    lines = (text or "").split("\n")
    emit(prefix + lines[0])
    for l in lines[1:]:
        emit("    " + l)


def talk_lines(mb, x):
    head = "#%d %s %s" % (x["seq"], fmt_when(mb, x.get("at")), x.get("from") or "?")
    if x.get("kind", "msg") != "msg":
        return ["%s · %s" % (head, x.get("text") or "")]
    flags = ("[急]" if x.get("urgent") else "") + (("（回复 #%d）" % x["reply_to"]) if x.get("reply_to") else "")
    lines = (x.get("text") or "").split("\n")
    return ["%s%s：%s" % (head, flags, lines[0])] + ["    " + l for l in lines[1:]]


def notify_lines(mb, e, seen=None):
    new = "[新]" if seen is not None and e["seq"] > seen else ""
    lines = (e.get("text") or "").split("\n")
    head = "%s%s#%d %s [%s] %s%s：%s" % (new, "[急]" if e.get("urgent") else "", e["seq"], fmt_when(mb, e.get("at")),
                                       e.get("kind") or "note", e.get("from") or "系统",
                                       ("「%s」" % e["title"]) if e.get("title") else "", lines[0])
    return [head] + ["    " + l for l in lines[1:]]


def cmd_notify_send(a):
    mb = open_mb(a)
    mb.require_writable()
    me = mb.identity(a.me or "")
    text = check_text(text_arg(a, "通知内容"), "通知", NOTIFY_MAX, a.allow_secret, mb.cfg.get("secret_patterns"))
    people, everyone = resolve_people(mb, me, a.to, "通知")
    if everyone:
        mb.require_perm(me, "broadcast")
    for p in people:
        mb.require_perm(me, "notify", p)
    cc = cc_controller(mb, me, people)
    to = [p["name"] for p in people]
    recs = []
    for n in to + ([cc["name"]] if cc else []):
        ref = {"to": to}
        if cc and n == cc["name"]:
            ref["cc"] = True
        recs.append((n, notify_push(mb, n, "note", text, me["name"], a.title or "", a.urgent, ref)))
    if a.json:
        emit_json({"sent": [{"to": n, "seq": r["seq"], "cc": bool(r.get("ref", {}).get("cc"))} for n, r in recs]})
    else:
        emit("已通知 %s" % "、".join("%s（#%d%s）" % (n, r["seq"], "，抄送" if r.get("ref", {}).get("cc") else "")
                                   for n, r in recs))
    return 0


def cmd_notify_list(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    seen = seen_pointers(mb, me["name"])[0]
    every = jsonl_read(feed_path(mb, me["name"]))
    unseen = [e for e in every if e["seq"] > seen]
    if a.all:
        items = every[-a.limit:] if a.limit else every
    else:
        items = unseen[:a.limit] if a.limit else unseen
    if items and not a.peek and not mb.read_only:
        s = seen_upto(seen, unseen, set(e["seq"] for e in items))
        if s > seen:
            advance_seen(mb, me["name"], notify=s)
    archive = feed_archive_path(mb, me["name"])
    archive = slash(archive) if os.path.exists(archive) else ""
    if a.json:
        emit_json({"me": me["name"], "seen": seen, "unseen": len(unseen), "items": items, "archive": archive})
        return 0
    emit("%s 的通知｜未看 %d 条%s" % (me["name"], len(unseen), "｜列出最近 %d 条" % len(items) if a.all else ""))
    for e in items:
        for l in notify_lines(mb, e, seen):
            emit("  " + l)
    if not items:
        emit("  （没有%s）" % ("通知" if a.all else "未看的通知"))
    if a.all and archive:
        emit("  （更早、已看过的通知归档在 %s）" % archive)
    return 0


def talk_after_post(mb, me, a, meta, rec, created=False):
    cid = meta["id"]
    others = [p["name"] for p in talk_people(mb, meta, but=me["name"])]
    online, pos = {}, {}
    for n in others:
        if n == "user":
            continue
        online[n] = presence_online(mb, n)
        for i, r in enumerate(talk_queue(mb, n)):
            if r["conv"] == cid:
                pos[n] = i + 1
    wait = float(getattr(a, "wait", 0) or 0)
    replies = []
    if wait > 0:
        replies = poll_until(wait, lambda: talk_replies(mb, me, cid, rec["seq"])) or []
        if replies and not mb.read_only:
            advance_seen(mb, me["name"], talk={cid: max(x["seq"] for x in replies)})
    data = {"conv": talk_brief(meta), "message": rec, "created": created, "online": online, "queue_position": pos,
            "replies": replies, "timeout": bool(wait > 0 and not replies)}
    if a.json:
        emit_json(data)
    else:
        emit("已%s通话 %s%s｜第 %d 条｜成员 %s" % ("发起" if created else "发到", cid,
                                          ("「%s」" % meta["topic"]) if meta.get("topic") else "", rec["seq"],
                                          "、".join(talk_brief(meta)["members"])))
        for n in others:
            if n in online:
                emit("  %s：%s%s" % (n, "监视在线" if online[n] else "监视不在线（可能很久才回）",
                                    "，本通话排在其来电队列第 %d 位" % pos[n] if pos.get(n) else ""))
        if wait > 0:
            if replies:
                emit("回复：")
                for x in replies:
                    for l in talk_lines(mb, x):
                        emit("  " + l)
            else:
                emit("TIMEOUT %d 秒内没有回复；稍后用 talk wait %s 或 talk read %s 查看" % (wait, cid, cid))
    return 3 if data["timeout"] else 0


def talk_text(mb, a):
    return check_text(text_arg(a, "通话内容"), "通话内容", TALK_MAX, a.allow_secret, mb.cfg.get("secret_patterns"))


def cmd_talk_start(a):
    mb = open_mb(a)
    mb.require_writable()
    me = mb.identity(a.me or "")
    text = talk_text(mb, a)
    people, everyone = resolve_people(mb, me, a.to, "通话")
    if everyone:
        mb.require_perm(me, "broadcast")
    for p in people:
        mb.require_perm(me, "talk", p)
    cc = cc_controller(mb, me, people)
    meta = talk_create(mb, me, people + ([cc] if cc else []), a.topic or "")
    if cc:
        with FileLock(mb.p("locks", "talk-%s.lock" % meta["id"])):
            append_seq(talk_log_path(mb, meta["id"]), _talk_record(
                me, "join", "按策略（assistant_to_assistant=cc-controller）主控 %s 一同在场" % cc["name"], who=[cc["name"]]))
    rec, meta = talk_post(mb, me, meta["id"], text, urgent=a.urgent)
    return talk_after_post(mb, me, a, meta, rec, created=True)


def cmd_talk_say(a):
    mb = open_mb(a)
    mb.require_writable()
    me = mb.identity(a.me or "")
    rec, meta = talk_post(mb, me, norm_conv(a.conv), talk_text(mb, a), a.reply_to or 0, a.urgent)
    return talk_after_post(mb, me, a, meta, rec)


def cmd_talk_call(a):
    mb = open_mb(a)
    mb.require_writable()
    me = mb.identity(a.me or "")
    who = mb.lookup(a.who)
    if who["name"] == me["name"]:
        raise CliError("不能呼叫自己", 2)
    if not who["active"]:
        raise CliError("%s 已不在任" % who["name"], 2)
    text = talk_text(mb, a)
    mb.require_perm(me, "talk", who)
    cc = cc_controller(mb, me, [who])
    meta, created = None, False
    if not a.new:
        meta = talk_find_pair(mb, me, who, cc)
    if meta is None:
        meta = talk_create(mb, me, [who] + ([cc] if cc else []), a.topic or "%s 呼叫 %s" % (me["name"], who["name"]))
        created = True
    rec, meta = talk_post(mb, me, meta["id"], text, urgent=a.urgent)
    return talk_after_post(mb, me, a, meta, rec, created)


def talk_access(mb, me, meta):
    """读通话：成员（含已退出的）、主控、用户或有 talk.audit 的人。"""
    if me is USER or talk_member(meta, me["name"]) or mb.can(me, "talk.audit"):
        return
    raise CliError("你不在通话 %s 里，也没有旁听权限 talk.audit" % meta["id"], 2)


def cmd_talk_read(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    cid = norm_conv(a.conv)
    meta = talk_meta(mb, cid, fresh=True)
    if meta is None:
        raise CliError("没有通话 %s（talk list 查看）" % cid, 2)
    talk_access(mb, me, meta)
    msgs = talk_log(mb, cid, fresh=True)
    seen = seen_pointers(mb, me["name"])[1].get(cid, 0)
    m = talk_member(meta, me["name"])
    base = max(seen, int((m or {}).get("since") or 0))
    unread = [x for x in msgs if x["seq"] > base and x.get("from") != me["name"]] if m else []
    if a.since is not None:
        shown = [x for x in msgs if x["seq"] > a.since]
    elif a.unread:
        shown = unread
    else:
        shown = msgs
    if a.limit:
        shown = shown[:a.limit] if a.unread else shown[-a.limit:]
    if m and not a.peek and not mb.read_only and unread:
        s = seen_upto(base, unread, set(x["seq"] for x in shown))
        if s > base:
            advance_seen(mb, me["name"], talk={cid: s})
    if a.json:
        emit_json({"conv": talk_brief(meta), "messages": shown, "unread": len(unread), "total": len(msgs)})
        return 0
    b = talk_brief(meta)
    emit("通话 %s%s｜%s｜成员 %s%s｜共 %d 条，未读 %d" % (
        cid, ("「%s」" % b["topic"]) if b["topic"] else "", "进行中" if b["state"] == "open" else "已结束",
        "、".join(b["members"]), ("｜已退出 " + "、".join(b["left"])) if b["left"] else "", len(msgs), len(unread)))
    for x in shown:
        for l in talk_lines(mb, x):
            emit("  " + l)
    return 0


def cmd_talk_list(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    seen = seen_pointers(mb, me["name"])[1]
    if a.everyone:
        if not (me is USER or mb.can(me, "talk.audit")):
            raise CliError("列出全部通话要旁听权限 talk.audit（主控、用户自带）", 2)
        metas = [m for m in (talk_meta(mb, c) for c in talk_ids(mb)) if m and (a.all or m.get("state") == "open")]
    else:
        metas = talk_mine(mb, me["name"], closed=a.all)
    rows = []
    for meta in metas:
        log = talk_log(mb, meta["id"])
        un = [x for x in talk_unread(mb, me["name"], meta, seen) if x.get("kind", "msg") == "msg"]
        last = log[-1] if log else None
        rows.append(dict(talk_brief(meta), unread=len(un), messages=len(log),
                         last=({"seq": last["seq"], "at": last.get("at"), "from": last.get("from"),
                                "text": clip((last.get("text") or "").split("\n")[0], 120)} if last else None)))
    if a.json:
        emit_json({"me": me["name"], "conversations": rows})
        return 0
    emit("%s 的通话 %d 个%s" % (me["name"], len(rows), "（含已结束）" if a.all else ""))
    for r in rows:
        last = r["last"]
        emit("  %s%s｜%s｜成员 %s｜未读 %d%s" % (
            r["id"], ("「%s」" % r["topic"]) if r["topic"] else "", "进行中" if r["state"] == "open" else "已结束",
            "、".join(r["members"]), r["unread"],
            ("｜最后 #%d %s %s：%s" % (last["seq"], fmt_when(mb, last["at"]), last["from"], last["text"])) if last else ""))
    if not rows:
        emit("  （没有通话；用 talk call <对方> 或 talk start --to <对方> 发起）")
    return 0


def cmd_talk_queue(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    who = me if not a.who else (USER if a.who == "user" else mb.lookup(a.who))
    if who["name"] != me["name"] and not (me is USER or mb.can(me, "talk.audit")):
        raise CliError("看别人的来电队列要旁听权限 talk.audit（主控、用户自带）", 2)
    rows = talk_queue(mb, who["name"])
    if a.json:
        emit_json({"for": who["name"], "queue": rows})
        return 0
    emit("%s 的来电队列：%d 个通话等着回复（紧急的在前，其余先来后到）" % (who["name"], len(rows)))
    for i, r in enumerate(rows):
        emit("  %d. %s%s%s｜%s 起 %d 条未读｜最早 %s" % (
            i + 1, "[急]" if r["urgent"] else "", r["conv"], ("「%s」" % r["topic"]) if r["topic"] else "", r["from"],
            r["count"], fmt_when(mb, r["since"])))
    return 0


def cmd_talk_next(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")

    def head():
        q = talk_queue(mb, me["name"])
        return q if q else None
    q = poll_until(a.wait, head) if (a.wait or 0) > 0 else head()
    if not q:
        if a.json:
            emit_json({"empty": True})
        else:
            emit("TIMEOUT 来电队列是空的" if (a.wait or 0) > 0 else "来电队列是空的")
        return 3
    cid = q[0]["conv"]
    meta = talk_meta(mb, cid, fresh=True)
    seen = seen_pointers(mb, me["name"])[1]
    unread = talk_unread(mb, me["name"], meta, seen)
    if unread and not a.peek and not mb.read_only:
        advance_seen(mb, me["name"], talk={cid: unread[-1]["seq"]})
    if a.json:
        emit_json({"conv": talk_brief(meta), "messages": unread, "queue_left": len(q) - 1})
        return 0
    b = talk_brief(meta)
    emit("接通 %s%s｜成员 %s｜队列里还有 %d 个" % (cid, ("「%s」" % b["topic"]) if b["topic"] else "", "、".join(b["members"]),
                                         len(q) - 1))
    for x in unread:
        for l in talk_lines(mb, x):
            emit("  " + l)
    emit("用 talk say %s --text … 回复" % cid)
    return 0


def cmd_talk_change(a):
    mb = open_mb(a)
    mb.require_writable()
    me = mb.identity(a.me or "")
    cid = norm_conv(a.conv)
    people, cc = None, None
    if a.op == "invite":
        people, everyone = resolve_people(mb, me, a.to, "邀请")
        if everyone:
            mb.require_perm(me, "broadcast")
        meta = talk_meta(mb, cid, fresh=True) or {}
        cc = cc_controller(mb, me, talk_people(mb, meta, but=me["name"]) + people) if meta else None
    rec, meta = talk_change(mb, me, cid, a.op, people, cc)
    if a.json:
        emit_json({"conv": talk_brief(meta), "record": rec})
    else:
        emit("通话 %s：%s" % (cid, rec["text"]))
    return 0


def cmd_talk_wait(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    only = norm_conv(a.conv) if a.conv else None

    def check():
        seen = seen_pointers(mb, me["name"])[1]
        metas = [talk_meta(mb, only, fresh=True)] if only else talk_mine(mb, me["name"])
        out = []
        for meta in metas:
            if meta:
                un = talk_unread(mb, me["name"], meta, seen)
                if un:
                    out.append((meta, un))
        return out
    got = poll_until(a.timeout, check)
    if not got:
        if a.json:
            emit_json({"timeout": True})
        else:
            emit("TIMEOUT %d 秒内通话里没有新消息" % a.timeout)
        return 3
    if not mb.read_only:
        advance_seen(mb, me["name"], talk=dict((m["id"], un[-1]["seq"]) for m, un in got))
    if a.json:
        emit_json({"conversations": [dict(talk_brief(m), messages=un) for m, un in got]})
        return 0
    for meta, un in got:
        emit("通话 %s%s：" % (meta["id"], ("「%s」" % meta["topic"]) if meta.get("topic") else ""))
        for x in un:
            for l in talk_lines(mb, x):
                emit("  " + l)
    return 0


def job_brief(q, j):
    d = dict((k, j.get(k)) for k in ("id", "title", "from", "at", "priority", "to", "status", "attempts", "claimed_by",
                                    "claimed_at", "lease_until", "finished_at", "finished_by", "error"))
    d["queue"] = q["name"]
    if j["status"] == "pending":
        d["position"] = queue_position(q, j["id"])
    return d


def job_lines(mb, q, j, full=False):
    st = JOB_ZH.get(j["status"], j["status"])
    if j["status"] == "pending":
        st += "，第 %d 位" % queue_position(q, j["id"])
    elif j["status"] == "claimed":
        st += "：%s，租约到 %s" % (j.get("claimed_by"), fmt_when(mb, j.get("lease_until")))
    elif j.get("finished_by"):
        st += "：%s" % j["finished_by"]
    head = "#%d [%s] %s「%s」｜%s 投于 %s%s%s%s" % (
        j["id"], st, ("优先级 %d " % j["priority"]) if j.get("priority") else "", j.get("title"), j.get("from"),
        fmt_when(mb, j.get("at")), ("｜指派 " + j["to"]) if j.get("to") else "",
        ("｜第 %d 次尝试" % j["attempts"]) if j.get("attempts") else "", ("｜" + clip(j["error"], 200)) if j.get("error") else "")
    out = [head]
    if full:
        if j.get("body"):
            out += ["  内容："] + ["    " + l for l in j["body"].split("\n")]
        if j.get("result"):
            out += ["  结果："] + ["    " + l for l in j["result"].split("\n")]
        for at, ev, who, detail in j.get("log") or []:
            out.append("  · %s %s %s%s" % (fmt_when(mb, at), ev, who, ("：" + detail) if detail else ""))
    return out


def queue_wait_final(mb, name, jid, seconds):
    def check():
        try:
            q = queue_view(mb, name)
            j = queue_job(q, jid)
        except CliError:
            return None
        return (q, j) if j["status"] in JOB_FINAL else None
    return poll_until(seconds, check)


def emit_job_result(mb, a, q, j, extra=None):
    if a.json:
        d = dict(job_brief(q, j), body=j.get("body") or "", result=j.get("result") or "", log=j.get("log") or [])
        d.update(extra or {})
        emit_json(d)
    else:
        for l in job_lines(mb, q, j, full=True):
            emit(l)


def cmd_queue_list(a):
    mb = open_mb(a)
    rows = []
    for n in queue_names(mb):
        try:
            q = queue_view(mb, n)
        except CliError as e:
            rows.append({"name": n, "error": str(e).split("\n")[0]})
            continue
        cnt = dict((s, 0) for s in JOB_ZH)
        for j in q["jobs"]:
            cnt[j["status"]] = cnt.get(j["status"], 0) + 1
        rows.append(dict({"name": n, "desc": q.get("desc") or "", "created_by": q.get("created_by"),
                          "lease_seconds": q.get("lease_seconds"), "max_attempts": q.get("max_attempts"),
                          "consumers": q.get("consumers") or [], "notify": q.get("notify", True)}, **cnt))
    if a.json:
        emit_json({"queues": rows})
        return 0
    emit("队列 %d 个" % len(rows))
    for r in rows:
        if "error" in r:
            emit("  %s｜读不了：%s" % (r["name"], r["error"]))
            continue
        emit("  %s%s｜排队 %d，已领 %d，完成 %d，失败 %d｜租约 %d 秒，最多尝试 %d 次%s" % (
            r["name"], ("（%s）" % r["desc"]) if r["desc"] else "", r["pending"], r["claimed"], r["done"], r["failed"],
            r["lease_seconds"], r["max_attempts"], ("｜领取名单 " + "、".join(r["consumers"])) if r["consumers"] else ""))
    if not rows:
        emit("  （还没有队列；主控用 queue create <名字> 建，或直接 queue push）")
    return 0


def cmd_queue_create(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    name = check_queue_name(a.name)
    mb.require_perm(me, "queue.admin", name)
    consumers = None
    if a.consumers is not None:
        consumers = [mb.lookup(x)["name"] for x in re.split(r"[,，、\s]+", a.consumers) if x.strip()]
    if a.lease is not None and not 10 <= a.lease <= 86400 * 7:
        raise CliError("--lease 须在 10 秒到 7 天之间", 2)
    if a.max_attempts is not None and not 1 <= a.max_attempts <= 100:
        raise CliError("--max-attempts 须在 1 到 100 之间", 2)
    made = []

    def create():
        made.append(True)
        return queue_new(name, me)

    def fn(q, notes):
        for k, v in (("desc", a.desc), ("lease_seconds", a.lease), ("max_attempts", a.max_attempts),
                     ("consumers", consumers), ("notify", a.notify)):
            if v is not None:
                q[k] = clip(v, 300) if k == "desc" else v
    _, q = queue_edit(mb, name, fn, create=create, actor=me["name"])
    if a.json:
        emit_json({"created": bool(made), "queue": dict((k, v) for k, v in q.items() if k != "jobs")})
    else:
        emit("已%s队列 %s｜租约 %d 秒｜最多尝试 %d 次｜%s｜新任务%s通知" % (
            "建" if made else "更新", name, q["lease_seconds"], q["max_attempts"],
            ("领取名单 " + "、".join(q["consumers"])) if q.get("consumers") else "有 queue.take 权限的人都能领",
            "" if q.get("notify", True) else "不"))
    return 0


def cmd_queue_push(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    name = check_queue_name(a.name)
    mb.require_perm(me, "queue.push", name)
    title = re.sub(r"\s+", " ", a.title or "").strip()
    if not title:
        raise CliError("任务要有标题（--title）", 2)
    check_text(title, "任务标题", 600, a.allow_secret, mb.cfg.get("secret_patterns"))
    raw = text_arg(a, "任务内容", required=False)
    body = check_text(raw, "任务内容", JOB_MAX, a.allow_secret, mb.cfg.get("secret_patterns")) if raw is not None else ""
    to = None
    if a.to:
        to = mb.lookup(a.to)
        if not to["active"]:
            raise CliError("%s 已不在任" % to["name"], 2)
        if not mb.can(to, "queue.take", name):
            raise CliError("%s 没有从队列 %s 领任务的权限（queue.take），指派了也领不了" % (to["name"], name), 2)
    create = None
    if not os.path.exists(queue_path(mb, name)):
        if not mb.can(me, "queue.admin", name):
            raise CliError("没有队列 %s；请主控先 queue create %s" % (name, name), 2)
        create = lambda: queue_new(name, me)

    def fn(q, notes):
        if len(queue_pending(q)) >= QUEUE_MAX_PENDING:
            raise CliError("队列 %s 已有 %d 个任务在排队，到上限了" % (name, QUEUE_MAX_PENDING), 2)
        q["seq"] = int(q.get("seq") or 0) + 1
        j = {"id": q["seq"], "title": clip(title, 200), "body": body, "from": me["name"], "at": iso_utc(),
             "priority": int(a.priority or 0), "to": to["name"] if to else "", "status": "pending", "attempts": 0,
             "claimed_by": "", "claimed_at": "", "lease_until": "", "result": "", "error": "", "finished_at": "",
             "finished_by": ""}
        job_log(j, "pushed", me["name"])
        q["jobs"].append(j)
        pos = queue_position(q, j["id"])
        recips = [j["to"]] if j["to"] else (queue_consumers(mb, q, me) if q.get("notify", True) else [])
        if recips:
            notes.append((recips, "job", "队列 %s 有新任务 #%d「%s」%s（优先级 %d，前面还有 %d 个）：用 queue take %s 领取"
                                         "（MCP：mailbox_queue take）" % (name, j["id"], j["title"], "，指派给你" if j["to"] else "",
                                                                        j["priority"], pos - 1, name),
                          {"queue": name, "job": j["id"]}))
        return dict(j)
    j, q = queue_edit(mb, name, fn, create=create, actor=me["name"])
    if (a.wait or 0) > 0:
        got = queue_wait_final(mb, name, j["id"], a.wait)
        if got:
            emit_job_result(mb, a, got[0], got[1])
            return 0
        if a.json:
            emit_json(dict(job_brief(q, queue_job(q, j["id"])), timeout=True))
        else:
            emit("已投到队列 %s：任务 #%d；TIMEOUT %d 秒内没有完成（queue wait %s %d 继续等）" % (
                name, j["id"], a.wait, name, j["id"]))
        return 3
    if a.json:
        emit_json(job_brief(q, queue_job(q, j["id"])))
    else:
        emit("已投到队列 %s：任务 #%d「%s」｜排第 %d 位%s" % (name, j["id"], j["title"], queue_position(q, j["id"]),
                                                    ("｜指派 " + j["to"]) if j["to"] else ""))
    return 0


def queue_try_take(mb, me, name):
    boss = me is USER or (me["role"] == "controller" and me["active"])

    def fn(q, notes):
        if q.get("consumers") and me["name"] not in q["consumers"] and not boss:
            raise CliError("你不在队列 %s 的领取名单里（%s）" % (name, "、".join(q["consumers"])), 2)
        cands = [j for j in q["jobs"] if j["status"] == "pending" and (not j.get("to") or j.get("to") == me["name"])]
        if not cands:
            return None
        cands.sort(key=lambda j: (-int(j.get("priority") or 0), 0 if j.get("to") == me["name"] else 1, j["id"]))
        j = cands[0]
        j.update(status="claimed", claimed_by=me["name"], claimed_at=iso_utc(),
                 lease_until=iso_at(time.time() + int(q.get("lease_seconds") or 900)))
        j["attempts"] = int(j.get("attempts") or 0) + 1
        job_log(j, "claimed", me["name"])
        if j.get("from") != me["name"]:
            notes.append(([j.get("from")], "job", "%s 领取了你投在队列 %s 的任务 #%d「%s」" % (
                me["name"], name, j["id"], j.get("title")), {"queue": name, "job": j["id"]}))
        return dict(j)
    j, q = queue_edit(mb, name, fn, actor=me["name"])
    return (q, j) if j else None


def cmd_queue_take(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    name = check_queue_name(a.name)
    mb.require_perm(me, "queue.take", name)
    got = queue_try_take(mb, me, name)
    if got is None and (a.wait or 0) > 0:
        state = {"key": None, "at": time.time()}

        def tick():
            # 队列文件变了才去抢；租约可能悄悄到期，所以至少每 10 秒也试一次
            try:
                st = os.stat(queue_path(mb, name))
                key = (st.st_size, st.st_mtime_ns)
            except OSError:
                key = None
            if key == state["key"] and time.time() - state["at"] < 10:
                return None
            state["key"], state["at"] = key, time.time()
            return queue_try_take(mb, me, name)
        got = poll_until(a.wait, tick)
    if got is None:
        if a.json:
            emit_json({"empty": True, "queue": name})
        else:
            emit(("TIMEOUT %d 秒内" % a.wait if (a.wait or 0) > 0 else "现在") + "队列 %s 没有可领的任务" % name)
        return 3
    q, j = got
    if a.json:
        emit_job_result(mb, a, q, j, {"lease_seconds": q.get("lease_seconds")})
    else:
        emit("已领取队列 %s 的任务 #%d，租约 %d 秒（做完 queue done %s %d；需要更久 queue renew；做不了 queue release）" % (
            name, j["id"], q.get("lease_seconds"), name, j["id"]))
        emit_job_result(mb, a, q, j)
    return 0


def cmd_queue_act(a):
    op = a.op
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    name = check_queue_name(a.name)
    admin = mb.can(me, "queue.admin", name)
    raw = text_arg(a, "说明", required=False)
    text = check_text(raw, "结果" if op == "done" else "说明", JOB_MAX, a.allow_secret,
                      mb.cfg.get("secret_patterns")) if raw is not None else ""

    def fn(q, notes):
        j = queue_job(q, a.job)
        ref = {"queue": name, "job": j["id"]}
        if op in ("done", "fail", "release", "renew"):
            if j["status"] != "claimed":
                hint = "（租约可能已到期、任务已放回排队或判为失败）" if j["status"] in ("pending", "failed") else ""
                raise CliError("任务 #%d 现在是「%s」，不是已领状态%s" % (j["id"], JOB_ZH.get(j["status"]), hint), 2)
            if j.get("claimed_by") != me["name"] and not admin:
                raise CliError("任务 #%d 由 %s 领取，你不能处置（要 queue.admin 权限）" % (j["id"], j.get("claimed_by")), 2)
        if op == "done":
            j.update(status="done", result=text, finished_at=iso_utc(), finished_by=me["name"], lease_until="")
            job_log(j, "done", me["name"])
            notes.append(([j.get("from")], "job-done", "队列 %s 的任务 #%d「%s」已由 %s 完成%s" % (
                name, j["id"], j.get("title"), me["name"], ("：" + clip(text, 1500)) if text else ""), ref))
        elif op == "fail":
            if a.retry and int(j.get("attempts") or 0) < int(q.get("max_attempts") or 1):
                j.update(status="pending", claimed_by="", claimed_at="", lease_until="", error=clip(text, 2000))
                job_log(j, "retry", me["name"], text)
            else:
                j.update(status="failed", error=clip(text or "（未说明原因）", 2000), finished_at=iso_utc(),
                         finished_by=me["name"], lease_until="")
                job_log(j, "failed", me["name"], text)
                notes.append(([j.get("from")], "job-failed", "队列 %s 的任务 #%d「%s」失败（%s）：%s" % (
                    name, j["id"], j.get("title"), me["name"], j["error"]), ref))
        elif op == "release":
            j.update(status="pending", claimed_by="", claimed_at="", lease_until="",
                     attempts=max(0, int(j.get("attempts") or 0) - 1))
            job_log(j, "released", me["name"], text)
        elif op == "renew":
            j["lease_until"] = iso_at(time.time() + int(q.get("lease_seconds") or 900))
            job_log(j, "renewed", me["name"])
        else:
            if j.get("from") != me["name"] and not admin:
                raise CliError("只有投放者（%s）或有 queue.admin 权限的人能取消任务 #%d" % (j.get("from"), j["id"]), 2)
            if j["status"] in JOB_FINAL:
                raise CliError("任务 #%d 已经%s" % (j["id"], JOB_ZH[j["status"]]), 2)
            holder = j.get("claimed_by") if j["status"] == "claimed" else ""
            j.update(status="cancelled", error=clip(text, 2000), finished_at=iso_utc(), finished_by=me["name"],
                     lease_until="")
            job_log(j, "cancelled", me["name"], text)
            if holder:
                notes.append(([holder], "job", "%s 取消了你领着的队列 %s 任务 #%d「%s」%s" % (
                    me["name"], name, j["id"], j.get("title"), ("：" + text) if text else ""), ref))
        return dict(j)
    j, q = queue_edit(mb, name, fn, actor=me["name"])
    if a.json:
        emit_job_result(mb, a, q, j)
    else:
        word = {"done": "完成", "fail": "记为失败" if j["status"] == "failed" else "放回重试", "release": "放回排队",
                "renew": "续租", "cancel": "取消"}[op]
        emit("已%s队列 %s 的任务 #%d" % (word, name, j["id"]) +
             ("（租约到 %s）" % fmt_when(mb, j.get("lease_until")) if op == "renew" else ""))
    return 0


def cmd_queue_show(a):
    mb = open_mb(a)
    name = check_queue_name(a.name)
    q = queue_view(mb, name)
    pend = queue_pending(q)
    claimed = [j for j in q["jobs"] if j["status"] == "claimed"]
    done = sorted([j for j in q["jobs"] if j["status"] in JOB_FINAL], key=lambda j: (j.get("finished_at") or "", j["id"]))
    if not a.all:
        done = done[-10:]
    if a.json:
        emit_json({"queue": dict((k, v) for k, v in q.items() if k != "jobs"),
                   "pending": [job_brief(q, j) for j in pend], "claimed": [job_brief(q, j) for j in claimed],
                   "finished": [job_brief(q, j) for j in done]})
        return 0
    emit("队列 %s%s｜排队 %d｜已领 %d｜租约 %d 秒｜最多尝试 %d 次" % (
        name, ("（%s）" % q["desc"]) if q.get("desc") else "", len(pend), len(claimed), q["lease_seconds"], q["max_attempts"]))
    for label, xs in (("排队", pend), ("已领", claimed), ("最近完成" if not a.all else "已结束", done)):
        if xs:
            emit("%s：" % label)
            for j in xs:
                emit("  " + job_lines(mb, q, j)[0])
    return 0


def cmd_queue_job(a):
    mb = open_mb(a)
    name = check_queue_name(a.name)
    q = queue_view(mb, name)
    emit_job_result(mb, a, q, queue_job(q, a.job))
    return 0


def cmd_queue_wait(a):
    mb = open_mb(a)
    name = check_queue_name(a.name)
    queue_job(queue_view(mb, name), a.job)  # 先确认有这个任务
    got = queue_wait_final(mb, name, a.job, a.timeout)
    if not got:
        if a.json:
            emit_json({"timeout": True, "queue": name, "job": a.job})
        else:
            emit("TIMEOUT %d 秒内任务 #%d 没有结束" % (a.timeout, a.job))
        return 3
    emit_job_result(mb, a, got[0], got[1])
    return 0


def roster_admins(mb):
    return [p["name"] for p in mb.participants(True)
            if (p["role"] == "controller") or mb.can(p, "roster.admin")]


def cmd_join(a):
    mb = open_mb(a)
    name = a.me or ""
    if not NAME_RE.match(name) or name == "user":
        raise CliError("申请加入要用 --as 给出想用的名字（小写字母、数字、. _ -，不能叫 user）：%r" % name, 2)
    path = request_path(mb, name)
    p = mb.get(name)
    if a.status:
        d = load_json_quiet(path)
        res = {"name": name, "request": d, "in_roster": bool(p), "active": bool(p and p["active"]),
               "role": p["role"] if p else ""}
        if a.json:
            emit_json(res)
        else:
            emit("%s：%s；申请 %s" % (name, ("在花名册里（%s%s）" % (ROLE_ZH.get(p["role"]), "，在任" if p["active"] else "，不在任"))
                                    if p else "不在花名册里", (d or {}).get("status") or "无"))
        return 0
    if p and p["active"]:
        emit("%s 已在花名册里（%s，在任），不用申请" % (name, ROLE_ZH.get(p["role"], p["role"])))
        return 0
    mb.require_writable()
    fresh = False
    with FileLock(mb.p("locks", "requests.lock")):
        cur = load_json_quiet(path) or {}
        pend = [r for r in requests_all(mb) if r.get("status") == "pending" and r.get("name") != name]
        if len(pend) >= REQ_MAX_PENDING:
            raise CliError("待批的加入申请已有 %d 个，请等主控处理" % len(pend), 2)
        d = {"name": name, "model": a.model or cur.get("model") or "", "harness": a.harness or cur.get("harness") or "",
             "display": a.display or cur.get("display") or "", "note": clip(a.note or cur.get("note") or "", 300),
             "role": a.role, "host": HOST, "updated_at": iso_utc(), "status": "pending"}
        fresh = cur.get("status") != "pending" or any(cur.get(k) != d[k] for k in ("model", "harness", "note", "role"))
        d["at"] = cur.get("at") if cur.get("status") == "pending" else iso_utc()
        atomic_write(path, dump_json(d))
    if fresh:
        notify_system(mb, roster_admins(mb), "join", "%s（%s / %s）申请以%s身份加入%s：用 roster approve %s 批准（可加 --allow 授权），"
                                                     "或 roster reject %s（MCP：mailbox_roster approve / reject）" % (
                          name, d["model"] or "-", d["harness"] or "-", ROLE_ZH.get(d["role"]),
                          ("，附言：" + d["note"]) if d["note"] else "", name, name), name, "加入申请")
    if (a.wait or 0) > 0:
        def check():
            r = load_json_quiet(path) or {}
            return r if r.get("status") in ("approved", "rejected") else None
        r = poll_until(a.wait, check, interval=1.0)
        if not r:
            emit("TIMEOUT %d 秒内主控没有处理 %s 的加入申请" % (a.wait, name))
            return 3
        if r["status"] == "approved":
            emit("已获批准：%s 以%s身份加入（由 %s 批准）" % (name, ROLE_ZH.get(r.get("role")), r.get("decided_by")))
            return 0
        emit("加入申请被拒（%s）：%s" % (r.get("decided_by"), r.get("reason") or "未说明理由"))
        return 1
    emit("已申请加入（%s），等待主控批准；可用 join --status 查看，或 join --wait 秒数 等结果" % name)
    return 0


def cmd_roster_requests(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_roster_admin(me)
    rows = [r for r in requests_all(mb) if a.all or r.get("status") == "pending"]
    if a.json:
        emit_json({"requests": rows})
        return 0
    emit("加入申请 %d 个%s" % (len(rows), "" if a.all else "（待批）"))
    for r in rows:
        emit("  %s｜%s / %s｜申请%s｜%s｜%s%s" % (
            r.get("name"), r.get("model") or "-", r.get("harness") or "-", ROLE_ZH.get(r.get("role"), r.get("role")),
            fmt_when(mb, r.get("at")), {"pending": "待批", "approved": "已批", "rejected": "已拒"}.get(r.get("status"), "?"),
            ("｜附言：" + r["note"]) if r.get("note") else ""))
    return 0


def cmd_roster_decide(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_roster_admin(me)
    path = request_path(mb, a.name)
    req = load_json_quiet(path)
    if not req or req.get("status") != "pending":
        raise CliError("没有 %s 的待批加入申请（roster requests 查看）" % a.name, 2)
    stamp = iso_utc()
    if a.op == "reject":
        req.update(status="rejected", decided_by=me["name"], decided_at=stamp, reason=clip(a.reason or "", 300))
        with FileLock(mb.p("locks", "requests.lock")):
            atomic_write(path, dump_json(req))
        mb.log("roster", {"at": stamp, "actor": me["name"], "action": "reject", "detail": {"name": a.name}})
        emit("已拒绝 %s 的加入申请" % a.name)
        return 0
    allow, deny = parse_perm_args(mb, a.allow), parse_perm_args(mb, a.deny)
    if allow or deny:
        mb.require_controller(me)  # 代管花名册的人能批准加入，但不能顺带授权
    role = a.role or req.get("role") or "assistant"
    old = mb.get(a.name)
    box = a.box or (old or {}).get("box") or (default_box(a.name) if role == "assistant" else "")
    if old is not None and old["active"]:
        raise CliError("%s 已在任，不用批准" % a.name, 2)
    if old is not None and box and BOX_RE.match(box) and not mb.read_only:
        mb.adopt_box(a.name, box)  # 前任重新在任：箱里的历史信记为已读

    def fn(cfg):
        rec = {"name": a.name, "role": role, "display": req.get("display") or a.name, "model": req.get("model") or "",
               "harness": req.get("harness") or "", "box": box, "active": True, "since": stamp, "until": "",
               "note": a.note or req.get("note") or "", "allow": allow, "deny": deny}
        for p in cfg["participants"]:
            if p["name"] == a.name:
                aliases = p.get("aliases") or []
                p.update(rec)
                p["aliases"] = aliases
                return
        cfg["participants"].append(norm_participant(dict(rec, aliases=[], session="")))
    cfg = mb.edit_config(me["name"], "approve", fn, {"name": a.name, "role": role, "allow": allow, "deny": deny})
    req.update(status="approved", decided_by=me["name"], decided_at=stamp, role=role)
    with FileLock(mb.p("locks", "requests.lock")):
        atomic_write(path, dump_json(req))
    p = mb.get(a.name)
    notify_system(mb, [a.name], "joined", "%s 批准你以%s身份加入；收件箱 %s%s。先看来信（inbox）和通知，再按主控登记开工。" % (
        me["name"], ROLE_ZH.get(role), "、".join(mb.boxes_of(p)) or "-",
        ("；额外授予 " + "、".join(allow)) if allow else ""), me["name"], "加入已批准")
    emit("已批准 %s 以%s身份加入（收件箱 %s）%s；在任助手 %d/%d" % (
        a.name, ROLE_ZH.get(role), box or "-", ("，授予 " + "、".join(allow)) if allow else "",
        len([x for x in cfg["participants"] if x["role"] == "assistant" and x["active"]]), cfg["max_assistants"]))
    return 0


# ------------------------------------------------------------------ 总览与体检
def cmd_who(a):
    mb = open_mb(a)
    letters = mb.scan()[0]
    sent = {}
    try:
        with open(mb.p("log", "sent.jsonl"), "rb") as f:
            for line in f.read().decode("utf-8", "replace").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                sent[e.get("from")] = e
    except OSError:
        pass
    rows = []
    for p in mb.participants(True):
        pres = load_json_quiet(mb.p("presence", p["name"] + ".json")) or {}
        online = presence_online(mb, p["name"])
        cur = load_json_quiet(mb.cursor_path(p["name"])) or {}
        nseen, tseen = int(cur.get("notify_seen") or 0), dict(cur.get("talk_seen") or {})
        talk_unread_n = sum(len([x for x in talk_unread(mb, p["name"], m, tseen) if x.get("kind", "msg") == "msg"])
                            for m in talk_mine(mb, p["name"]))
        notify_n = len(notify_unseen(mb, p["name"], nseen))
        read = set(cur.get("read") or [])
        unread = None
        if cur.get("baseline_at"):
            unread = 0
            for l in my_letters(mb, p, letters):
                if l.name in read:
                    continue
                h = mb.info(l)
                if h and mb.classify(p, l, h) != "other" and h.get("from") != p["name"]:
                    unread += 1
        t = load_tasks(mb, p["name"])
        last = (cur.get("last_sent") or {}).get("at") or (sent.get(p["name"]) or {}).get("at")
        rows.append({"name": p["name"], "role": p["role"], "model": p["model"], "harness": p["harness"],
                     "online": online, "watch": {"pid": pres.get("pid"), "host": pres.get("host"),
                                                 "beat": pres.get("beat"), "stopped": pres.get("stopped")},
                     "unread": unread, "talk_unread": talk_unread_n, "notify_unseen": notify_n, "last_sent": last,
                     "tasks_open": len([x for x in t["tasks"] if x.get("status") != "done"]),
                     "waiting_on": (t.get("waiting_on") or {}).get("what")})
    pending = [r["name"] for r in requests_all(mb) if r.get("status") == "pending"]
    if a.json:
        emit_json(rows)
        return 0
    emit("信箱 %s｜线程 %s｜%s%s" % (slash(mb.root), mb.thread or "（无）", mb.fmt_time(time.time()),
                                 ("｜待批加入 " + "、".join(pending)) if pending else ""))
    for r in rows:
        last = ts_of(r["last_sent"])
        emit("  %s %-18s %-4s %-18s 监视%s｜未读 %s｜通话未读 %d｜通知 %d｜上次发信 %s｜未完成 %d%s" % (
            "●" if r["online"] else "○", r["name"], ROLE_ZH.get(r["role"]), clip("%s/%s" % (r["model"] or "-", r["harness"] or "-"), 18),
            "在线" if r["online"] else ("停于 " + (r["watch"]["stopped"] or "?") if r["watch"]["pid"] else "从未"),
            "-" if r["unread"] is None else r["unread"], r["talk_unread"], r["notify_unseen"],
            ago(time.time() - last) if last else "无记录",
            r["tasks_open"], ("｜等待 " + clip(r["waiting_on"], 40)) if r["waiting_on"] else ""))
    return 0


def cmd_doctor(a):
    root = resolve_mailbox_dir(a)
    if not os.path.isdir(root):
        raise CliError("信箱目录不存在：%s" % root, 2)
    mb = Mailbox(root, a.config, a.state_dir, True)
    errors, warns, infos = [], [], []
    cfg = None
    try:
        cfg = mb.load_config(strict=False)
        mb._cfg = cfg
        errors += ["花名册：" + e for e in mb.config_errors]
        warns += ["花名册：" + w for w in mb.config_warnings]
    except CliError as e:
        warns.append(str(e).split("\n")[0] + "（以下只按文件名体检）")
    if cfg is not None and not mb.config_errors:
        letters, boards, others = mb.scan(None if a.all_threads else "__cfg__")
    else:
        letters, boards, others = mb.scan(None)
    infos.append("信件 %d 封，状态板 %d 个，其它文件 %d 个" % (len(letters), len(boards), len(others)))
    by_box = {}
    for l in letters:
        by_box.setdefault((l.box, l.thread), []).append(l)
    for (box, th), ls in sorted(by_box.items()):
        nums = {}
        for l in ls:
            nums.setdefault(l.num, []).append(l.name)
        dups = dict((k, v) for k, v in nums.items() if len(v) > 1)
        lo, hi = min(nums), max(nums)
        gaps = (hi - lo + 1) - len(nums)  # 直接算，编号再大也不逐个枚举
        infos.append("箱 %s%s：%d 封，编号 %d–%d%s" % (box, ("（线程 %s）" % th) if th else "（无线程旧信）", len(ls), lo, hi,
                                                  "，缺号 %d 个" % gaps if gaps else ""))
        for k, v in sorted(dups.items()):
            warns.append("同号多信：%s（引用编号时须带后缀或写完整文件名）" % "、".join(sorted(v)))
    cr, fffd, bom, nohash, big = [], [], [], [], []
    limit = cfg["max_bytes"] if cfg else 65536
    for l in letters:
        try:
            with open(l.path, "rb") as f:
                data = f.read()
        except OSError as e:
            errors.append("读不了 %s：%s" % (l.name, e))
            continue
        if b"\r" in data:
            cr.append(l.name)
        if b"\xef\xbf\xbd" in data:
            fffd.append(l.name)
        if data.startswith(b"\xef\xbb\xbf"):
            bom.append(l.name)
        if not data.lstrip().startswith(b"#"):
            nohash.append(l.name)
        if len(data) > limit:
            big.append(l.name)
    for label, xs in (("含 CR", cr), ("含 U+FFFD 损坏字", fffd), ("带 BOM", bom), ("首行不是 # 标题", nohash),
                      ("超过大小上限", big)):
        if xs:
            warns.append("%s 的信 %d 封（原件不改，按需另函更正），例：%s" % (label, len(xs), "、".join(xs[:5])))
    stray = [n for n in others if not n.startswith(".")]
    if stray:
        warns.append("信箱里有不合命名的文件 %d 个，例：%s" % (len(stray), "、".join(sorted(stray)[:5])))
    if cfg is not None and not mb.config_errors:
        boxes_known = set([cfg["controller_box"], cfg["broadcast_box"]] + [p["box"] for p in cfg["participants"] if p["box"]])
        for (box, th) in by_box:
            if box not in boxes_known:
                warns.append("箱 %s 不属于花名册里任何人（历史箱可忽略）" % box)
        for p in mb.participants(True):
            if p["role"] != "observer" and not mb.boxes_of(p):
                errors.append("%s 没有任何收件箱" % p["name"])
        shared = [b for b in boxes_known if len(mb.members(b)) > 1 and b != cfg["broadcast_box"]]
        for b in shared:
            ls = [l for l in letters if l.box == b][-50:]
            unrouted = [l for l in ls if not (mb.info(l) or {}).get("to")]
            if unrouted:
                infos.append("共享箱 %s 最近 50 封里有 %d 封标题没有点名收件人（会同时提醒箱内各方）" % (b, len(unrouted)))
        try:
            for n in os.listdir(mb.p("locks")):
                p = mb.p("locks", n)
                age = time.time() - os.stat(p).st_mtime
                if age > 120:
                    warns.append("锁文件 %s 已存在 %s，可能是崩溃残留" % (n, ago(age)))
        except OSError:
            pass
        try:
            with open(mb.p("log", "sent.jsonl"), "rb") as f:
                for line in f.read().decode("utf-8", "replace").splitlines()[-200:]:
                    try:
                        e = json.loads(line)
                        p = os.path.join(mb.root, e["file"])
                        skew = abs(os.stat(p).st_mtime - ts_of(e["at"]))
                        if skew > 120:
                            warns.append("%s 的投递日志时间与文件时间相差 %d 秒" % (e["file"], skew))
                        back, _ = sha256_file(p)
                        if back != e.get("sha256"):
                            errors.append("%s 的内容与投递时的 SHA-256 不一致（投递后被改动？）" % e["file"])
                    except (ValueError, KeyError, OSError, TypeError):
                        continue
        except OSError:
            pass
    if cfg is not None and not mb.config_errors:
        doctor_collab(mb, cfg, errors, warns, infos)
    res = {"mailbox": slash(root), "errors": errors, "warnings": warns, "info": infos}
    if a.json:
        emit_json(res)
    else:
        emit("体检 %s（只读）" % slash(root))
        for x in infos:
            emit("  · " + x)
        for x in warns:
            emit("  注意：" + x)
        for x in errors:
            emit("  错误：" + x)
        emit("结论：%s" % ("有 %d 个错误" % len(errors) if errors else "无错误（注意项 %d 个）" % len(warns)))
    return 1 if errors else 0


def doctor_collab(mb, cfg, errors, warns, infos):
    """通话、队列、通知流、加入申请与权限令牌的体检。"""
    convs, bad_lines = 0, []
    for cid in talk_ids(mb):
        convs += 1
        try:
            if talk_meta(mb, cid, fresh=True) is None:
                errors.append("通话 %s 的元数据读不了" % cid)
        except CliError as e:
            errors.append(str(e).split("\n")[0])
        try:
            raw = read_bytes_retry(talk_log_path(mb, cid)) or b""
        except CliError:
            raw = b""
        n = len([l for l in raw.split(b"\n") if l.strip()]) - len(jsonl_read(talk_log_path(mb, cid)))
        if n > 0:
            bad_lines.append("%s（%d 行）" % (cid, n))
    if convs:
        infos.append("通话 %d 个" % convs)
    if bad_lines:
        warns.append("通话记录里有看不懂的行（多半是写到一半崩溃留下的残行，已跳过）：%s" % "、".join(bad_lines))
    now = time.time()
    for name in queue_names(mb):
        try:
            q = read_json_strict(queue_path(mb, name))
        except CliError as e:
            errors.append(str(e).split("\n")[0])
            continue
        jobs = (q or {}).get("jobs") or []
        stale = [j["id"] for j in jobs if j.get("status") == "claimed" and (ts_of(j.get("lease_until")) or 0) < now]
        infos.append("队列 %s：排队 %d，已领 %d" % (name, len([j for j in jobs if j.get("status") == "pending"]),
                                              len([j for j in jobs if j.get("status") == "claimed"])))
        if stale:
            warns.append("队列 %s 里任务 %s 的租约已过期（下次有人操作这个队列时自动收回）" % (
                name, "、".join("#%d" % x for x in stale)))
    pend = [r.get("name") for r in requests_all(mb) if r.get("status") == "pending"]
    if pend:
        infos.append("待批的加入申请：%s（roster requests 查看）" % "、".join(pend))
    custom = any(p.get("allow") or p.get("deny") for p in cfg["participants"]) or cfg.get("presets")
    ctrl = [p for p in cfg["participants"] if p["role"] == "controller" and p["active"]]
    bare = ([ctrl[0]["name"] + "（主控）"] if ctrl and not ctrl[0].get("token_sha256") else []) + (
        ["user（用户）"] if not cfg.get("user_token_sha256") else [])
    if custom and bare:
        warns.append("已按人设了权限，但 %s 没有身份令牌：能跑命令行的模型可以用 --as 冒充他们绕开权限（perm token <名字> 设令牌）"
                     % "、".join(bare))


def cmd_next(a):
    mb = open_mb(a)
    if a.box:
        box = a.box
    else:
        t = mb.find(a.to or "controller")
        if t == "@assistants":
            raise CliError("next 一次只看一个收件箱：--to 写具体参与方，或用 --box", 2)
        box = mb.cfg["broadcast_box"] if t == "@all" else mb.box_of_target(t)
    taken = mb.same_number(box, mb.thread)
    n = (max(taken) + 1) if taken else 1
    emit(mb.fmt_name(box, mb.thread, n, "URGENT" if a.urgent else ""))
    return 0


def cmd_template(a):
    tdir = os.path.join(SKILL_DIR, "templates")
    names = sorted(x[:-3] for x in os.listdir(tdir) if x.endswith(".md")) if os.path.isdir(tdir) else []
    if not a.name:
        emit("可用模板：" + "、".join(names))
        return 0
    if a.name not in names:
        raise CliError("没有模板 %s；可用：%s" % (a.name, "、".join(names)), 2)
    with open(os.path.join(tdir, a.name + ".md"), "rb") as f:
        emit(f.read().decode("utf-8").rstrip("\n"))
    return 0


INSTALL_TARGETS = {
    "agents": "~/.agents/skills",
    "claude": "~/.claude/skills",
    "codex": "~/.codex/skills",
    "pi": "~/.pi/agent/skills",
}


def cmd_install(a):
    src = SKILL_DIR
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        raise CliError("找不到 SKILL.md：%s" % src, 2)
    done = {}
    for t in a.target:
        if t.startswith("dir:"):
            base = os.path.abspath(os.path.expanduser(t[4:]))
        elif t in INSTALL_TARGETS:
            base = os.path.abspath(os.path.expanduser(INSTALL_TARGETS[t]))
        else:
            raise CliError("未知安装目标 %s；可用 %s 或 dir:<目录>" % (t, " / ".join(INSTALL_TARGETS)), 2)
        real = os.path.realpath(base)
        dest = os.path.join(base, SKILL_NAME)
        if real in done:
            emit("%s 与 %s 实为同一目录（%s），跳过" % (t, done[real], slash(real)))
            continue
        done[real] = t
        if os.path.exists(dest) and os.path.realpath(dest) == os.path.realpath(src):
            emit("%s 就是源目录，跳过" % slash(dest))
            continue
        if a.dry_run:
            emit("将安装到 %s%s" % (slash(dest), "（已存在，将备份后覆盖）" if os.path.exists(dest) else ""))
            continue
        os.makedirs(base, exist_ok=True)
        if os.path.exists(dest):
            if not a.force:
                raise CliError("%s 已存在；要覆盖请加 --force（旧版会移到 ~/.agent-mailbox/backups/）" % slash(dest), 2)
            bk = os.path.join(os.path.expanduser("~"), ".agent-mailbox", "backups", "%s-%s-%s" % (
                SKILL_NAME, re.sub(r"[^A-Za-z0-9]+", "_", t), time.strftime("%Y%m%d-%H%M%S")))
            os.makedirs(os.path.dirname(bk), exist_ok=True)
            shutil.move(dest, bk)
            emit("旧版已移到 %s" % slash(bk))
        tmp = os.path.join(base, ".%s.tmp-%s" % (SKILL_NAME, secrets.token_hex(3)))
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "INSTALL.json"))
        files = {}
        for d, _, fs in os.walk(tmp):
            for fn in fs:
                p = os.path.join(d, fn)
                files[slash(os.path.relpath(p, tmp))] = sha256_file(p)[0]
        with open(os.path.join(tmp, "INSTALL.json"), "wb") as f:
            f.write(dump_json({"skill": SKILL_NAME, "version": VERSION, "source": slash(src), "target": t,
                               "installed_at": iso_utc(), "files": files}))
        os.replace(tmp, dest)
        emit("已安装到 %s（%d 个文件）" % (slash(dest), len(files)))
    return 0


# ------------------------------------------------------------------ 自检
def cmd_selftest(a):
    py = sys.executable
    me_script = os.path.abspath(__file__)
    work = tempfile.mkdtemp(prefix="agent-mailbox-selftest-")
    mbdir = os.path.join(work, "mailbox")
    os.makedirs(mbdir)
    fails = []

    def run(*args, **kw):
        stdin = kw.get("stdin")
        p = subprocess.run([py, "-I", "-B", me_script, "--mailbox", mbdir] + list(args), input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=kw.get("timeout", 60))
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def ok(cond, msg, extra=""):
        emit(("通过  " if cond else "失败  ") + msg + (("｜" + extra) if (extra and not cond) else ""))
        if not cond:
            fails.append(msg)

    def body(text):
        p = os.path.join(work, "draft-%s.md" % secrets.token_hex(3))
        with open(p, "wb") as f:
            f.write(text.encode("utf-8"))
        return p

    c, o, e = run("init", "--controller", "boss", "--model", "gpt-5.5", "--harness", "pi", "--thread", "0a0b0c0d",
                  "--controller-box", "codex")
    ok(c == 0, "初始化（主控 boss，主控箱 codex）", e)
    for n, mdl, h, box in (("helper-a", "claude-opus-5-5", "claude-code", "claude"),
                           ("helper-b", "kimi-k2", "kimi-cli", "claude"), ("helper-c", "glm-4.6", "zcode", "")):
        args = ["roster", "add", n, "--as", "boss", "--model", mdl, "--harness", h]
        if box:
            args += ["--box", box]
        c, o, e = run(*args)
        ok(c == 0, "加入助手 %s（%s）" % (n, mdl), e)
    c, o, e = run("roster", "add", "rogue", "--as", "helper-a", "--model", "x")
    ok(c != 0, "助手不能改花名册")
    c, o, e = run("roster", "handover", "helper-a", "--as", "helper-b")
    ok(c != 0, "助手不能发起主控交接")
    c, o, e = run("send", "--as", "boss", "--to", "helper-a", "--subject", "登记 T1", "--body", "请先读回再开工。")
    ok(c == 0 and "to-claude-0a0b0c0d-001.md" in o, "主控给共享箱的 helper-a 发登记", e)
    c, o, e = run("inbox", "--as", "helper-a", "--json")
    d = json.loads(o) if c == 0 else {}
    ok(d.get("unread") == 1 and d["letters"][0]["class"] == "mine", "helper-a 收到点名信", e)
    c, o, e = run("inbox", "--as", "helper-b", "--json")
    d = json.loads(o) if c == 0 else {}
    ok(d.get("unread") == 0, "同箱的 helper-b 不把点名给别人的信当未读", e)
    c, o, e = run("send", "--as", "helper-a", "--subject", "开工回执", "--reply-to", "claude-1", "--body", "已读 1。")
    ok(c == 0 and "to-codex-0a0b0c0d-001.md" in o, "助手默认写给主控", e)
    c, o, e = run("read", "--as", "boss", "codex-1")
    ok(c == 0 and "回复：to-claude-0a0b0c0d-001" in o and "发件：helper-a（助手，claude-opus-5-5" in o, "主控读信，信头齐全", e)
    c, o, e = run("send", "--as", "helper-c", "--to", "helper-b", "--subject", "跨模型协作", "--body", "glm → kimi")
    ok(c == 0, "助手之间可直接通信（默认策略 allow）", e)

    outbuf = {}

    def bg():
        outbuf["r"] = run("watch", "--as", "helper-b", "--once", "--seconds", "40", "--interval", "0.3",
                          "--coalesce", "0.5", timeout=90)
    run("mark", "--as", "helper-b", "--all")
    th = threading.Thread(target=bg)
    th.start()
    time.sleep(2.0)
    run("send", "--as", "boss", "--to", "helper-b", "--urgent", "--subject", "紧急：停测", "--body", "立即停。")
    th.join(100)
    c, o, e = outbuf.get("r", (None, "", ""))
    ok(c == 0 and "MAIL [急]" in o and "URGENT" in o, "watch --once 收到紧急信即退出", e or o)

    procs = [subprocess.Popen([py, "-I", "-B", me_script, "--mailbox", mbdir, "send", "--as", "helper-c", "--subject",
                               "并发 %d" % i, "--body", "并发投递测试 %d" % i], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for i in range(8)]
    outs = [p.communicate(timeout=120) for p in procs]
    files = sorted(x for x in os.listdir(mbdir) if x.startswith("to-codex-"))
    ok(all(p.returncode == 0 for p in procs) and len(files) == 9, "8 个进程同时投递：%d 封全部落盘、编号不重" % len(files),
       " ".join(o[1].decode("utf-8", "replace") for o in outs))
    with open(os.path.join(mbdir, "to-codex-0a0b0c0d-010.md"), "xb") as f:
        f.write("# 旧工具直接占号\n".encode("utf-8"))
    c, o, e = run("send", "--as", "helper-a", "--urgent", "--subject", "撞号", "--body", "x", "--json")
    ok(c == 0 and json.loads(o)["sent"][0]["number"] == 11, "同号不同后缀不会重号（跳过已占的 10）", e or o)
    c, o, e = run("send", "--as", "helper-a", "--subject", "坏字", "--body", "坏字" + chr(0xFFFD) + "在此")
    ok(c != 0 and "U+FFFD" in e, "拒收 U+FFFD 损坏字")
    c, o, e = run("send", "--as", "helper-a", "--subject", "令牌", "--body", "Bearer abcdefghijklmnopqrstuvwxyz0123456789")
    ok(c != 0 and "凭据" in e, "拒收疑似凭据")
    c, o, e = run("send", "--as", "helper-a", "--draft", body("# 致 主控：CRLF\r\n\r\n正文\r\n"))
    ok(c != 0 and "CR" in e, "拒收 CRLF（可用 --normalize 修正）")
    c, o, e = run("send", "--as", "boss", "--to", "helper-a", "--draft", body("# 标题里没有点名收件人\n\n正文\n"))
    ok(c != 0 and "共用" in e, "共享箱的信标题必须点名收件人", e)
    c, o, e = run("send", "--as", "boss", "--to", "helper-a", "--draft", body("# 致 helper-b：标题点名错人\n\n正文\n"))
    ok(c != 0 and "漏投" in e, "标题点名的人不在收件人里时拒发", e)
    c, o, e = run("status", "put", "RUN-1", "--as", "helper-c", stdin=b'{"step": 3, "of": 9}')
    c2, o2, e2 = run("status", "get", "RUN-1")
    ok(c == 0 and c2 == 0 and '"step": 3' in o2, "状态板原子替换并可读回", e or e2)
    c, o, e = run("roster", "handover", "helper-c", "--as", "boss", "--old-role", "former")
    ok(c == 0, "主控交接 boss → helper-c", e)
    c, o, e = run("roster", "show", "--json")
    d = json.loads(o) if c == 0 else {"participants": []}
    ctrls = [p["name"] for p in d["participants"] if p["role"] == "controller" and p["active"]]
    ok(ctrls == ["helper-c"], "交接后主控唯一", str(ctrls))
    c, o, e = run("send", "--as", "boss", "--to", "helper-a", "--subject", "前任", "--body", "x")
    ok(c != 0, "前任主控不能再发信")
    c, o, e = run("send", "--as", "helper-a", "--subject", "致新主控", "--body", "交接后照常写主控箱")
    c2, o2, e2 = run("inbox", "--as", "helper-c", "--json")
    d = json.loads(o2) if c2 == 0 else {"letters": []}
    ok(c == 0 and any("致新主控" in x["title"] for x in d["letters"]), "交接后新主控收到主控箱来信", e or e2)
    c, o, e = run("roster", "limit", "1", "--as", "helper-c")
    ok(c != 0, "上限不能低于在任助手数")
    c, o, e = run("roster", "remove", "helper-b", "--as", "helper-c")
    ok(c == 0, "移除助手", e)
    c, o, e = run("roster", "add", "helper-d", "--as", "helper-c", "--model", "grok-4", "--harness", "grok")
    ok(c == 0, "再加助手（数量可调）", e)
    c, o, e = run("--read-only", "send", "--as", "helper-a", "--subject", "只读", "--body", "x")
    ok(c != 0, "只读模式拒绝投递")
    c, o, e = run("perm", "revoke", "helper-a", "talk:helper-d", "--as", "helper-c")
    c2, o2, e2 = run("talk", "call", "helper-d", "--as", "helper-a", "--text", "x", "--wait", "0")
    ok(c == 0 and c2 != 0 and "权限不足" in e2, "主控按人收回通话权限后即时生效", e or e2)
    callbuf = {}

    def caller():
        callbuf["r"] = run("talk", "call", "helper-d", "--as", "helper-c", "--text", "2+2=?", "--wait", "60", "--json",
                           timeout=120)
    th = threading.Thread(target=caller)
    th.start()
    nxt = None
    for _ in range(200):
        c, o, e = run("talk", "next", "--as", "helper-d", "--json")
        if c == 0:
            nxt = json.loads(o)
            break
        time.sleep(0.3)
    if nxt:
        run("talk", "say", nxt["conv"]["id"], "--as", "helper-d", "--text", "4")
    th.join(150)
    c, o, e = callbuf.get("r", (None, "", ""))
    ok(c == 0 and [x["text"] for x in json.loads(o)["replies"]] == ["4"], "通话：呼叫、来电队列接通、回复送回呼叫方", e or o)
    run("queue", "push", "review", "--as", "helper-c", "--title", "审 1", "--text", "看 diff")
    c, o, e = run("queue", "take", "review", "--as", "helper-a", "--json")
    c2, o2, e2 = run("queue", "done", "review", "1", "--as", "helper-a", "--text", "LGTM")
    c3, o3, e3 = run("notify", "list", "--as", "helper-c", "--json")
    ok(c == 0 and c2 == 0 and any(x["kind"] == "job-done" for x in json.loads(o3)["items"]),
       "队列：投放、领取、完成，结果通知回投放者", e or e2 or e3)
    run("join", "--as", "newbie", "--model", "qwen-3")
    c, o, e = run("roster", "approve", "newbie", "--as", "helper-c")
    ok(c == 0 and "newbie" in run("roster")[1], "新模型申请加入，主控批准", e)
    srv = os.path.join(os.path.dirname(me_script), "mcp_server.py")
    if os.path.isfile(srv):
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "selftest", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mailbox_inbox", "arguments": {"all": True}}}]
        p = subprocess.Popen([py, "-I", "-B", srv, "--mailbox", mbdir, "--as", "helper-a", "--no-watch"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            raw_out, _ = p.communicate("".join(json.dumps(r) + "\n" for r in reqs).encode("utf-8"), timeout=90)
        except subprocess.TimeoutExpired:
            p.kill()
            raw_out = b""
        byid = {}
        for line in raw_out.decode("utf-8", "replace").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                byid["bad"] = line
                continue
            if isinstance(m, dict):
                byid[m.get("id")] = m
        ok(((byid.get(1) or {}).get("result") or {}).get("serverInfo", {}).get("name") == "agent-mailbox"
           and len(((byid.get(2) or {}).get("result") or {}).get("tools") or []) == 19
           and "structuredContent" in ((byid.get(3) or {}).get("result") or {}) and "bad" not in byid,
           "MCP 服务：握手、列工具、调工具，stdout 只有协议消息", raw_out[:300].decode("utf-8", "replace"))
    c, o, e = run("doctor")
    ok(c == 0, "体检无错误", o + e)
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        emit("自检目录保留在 %s" % slash(work))
    emit("自检%s：%d 项失败" % ("通过" if not fails else "未通过", len(fails)))
    return 1 if fails else 0


# ------------------------------------------------------------------ 参数
GLOBAL_DEFAULTS = {"mailbox": None, "config": None, "state_dir": None, "me": None, "read_only": False,
                   "json": False, "token_file": None}


class Parser(argparse.ArgumentParser):
    """帮助、用法错误也走本线程的输出出口（MCP 服务在进程内调用时不能写到协议的 stdout 上）。"""

    def _print_message(self, message, file=None):
        if message:
            (_err() if file is sys.stderr else _out()).write(message)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("通用选项")
    g.add_argument("--mailbox", "-m", default=argparse.SUPPRESS,
                   help="信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）")
    g.add_argument("--config", default=argparse.SUPPRESS, help="花名册文件（默认 <信箱>/.agent-mailbox/config.json）")
    g.add_argument("--state-dir", default=argparse.SUPPRESS, help="控制目录（默认 <信箱>/.agent-mailbox）")
    g.add_argument("--as", dest="me", default=argparse.SUPPRESS, help="本方参与方名（默认环境变量 AGENT_MAILBOX_ME）")
    g.add_argument("--token-file", default=argparse.SUPPRESS,
                   help="身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）")
    g.add_argument("--read-only", action="store_true", default=argparse.SUPPRESS, help="只读：不写游标、锁、在线状态，拒绝投递")
    g.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出 JSON")

    p = Parser(prog="mailbox.py", parents=[common],
                                description="agent-mailbox %s：多模型协作信箱（主控唯一，助手可增减）" % VERSION)
    p.add_argument("--version", action="version", version="agent-mailbox " + VERSION)
    sub = p.add_subparsers(dest="cmd", metavar="<命令>")

    def sp(name, help_):
        return sub.add_parser(name, parents=[common], help=help_, description=help_)

    s = sp("init", "初始化信箱：建控制目录与花名册（指定唯一主控）")
    s.add_argument("--controller", help="主控名")
    s.add_argument("--model", default="")
    s.add_argument("--harness", default="")
    s.add_argument("--session", default="")
    s.add_argument("--display", default="")
    s.add_argument("--box", default="", help="主控的个人收件箱（可选）")
    s.add_argument("--alias", action="append")
    s.add_argument("--thread", default="", help="8 位十六进制线程号（默认随机）")
    s.add_argument("--controller-box", default="controller")
    s.add_argument("--broadcast-box", default="all")
    s.add_argument("--tz", default="+08:00")
    s.add_argument("--tz-label", default="北京")
    s.add_argument("--max-assistants", type=int, default=8)
    s.add_argument("--from-config", help="用现成的花名册 JSON 初始化")
    s.set_defaults(func=cmd_init)

    s = sp("use", "设定或查看本机默认信箱")
    s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_use)

    s = sp("roster", "花名册：查看、增减助手、交接主控")
    rs = s.add_subparsers(dest="action", metavar="<动作>")
    s.set_defaults(func=cmd_roster_show)
    r = rs.add_parser("show", parents=[common], help="查看花名册")
    r.set_defaults(func=cmd_roster_show)
    r = rs.add_parser("add", parents=[common], help="加入参与方（主控或用户）")
    r.add_argument("name")
    r.add_argument("--role", choices=("assistant", "observer"), default="assistant")
    r.add_argument("--model", default="")
    r.add_argument("--harness", default="")
    r.add_argument("--session", default="")
    r.add_argument("--display", default="")
    r.add_argument("--box", default="", help="收件箱（默认用名字；多方可共用一箱，靠标题点名区分）")
    r.add_argument("--alias", action="append")
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_roster_add)
    r = rs.add_parser("update", parents=[common], help="改参与方信息（本人可改 model/harness/session/note）")
    r.add_argument("name")
    for k in ("display", "model", "harness", "session", "box", "note"):
        r.add_argument("--" + k, default=None)
    r.add_argument("--alias-add", action="append")
    r.add_argument("--alias-remove", action="append")
    r.add_argument("--activate", choices=("assistant", "observer"), help="让前任重新在任")
    r.set_defaults(func=cmd_roster_update)
    r = rs.add_parser("remove", parents=[common], help="移除助手（改为前任，历史保留）")
    r.add_argument("name")
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_roster_remove)
    r = rs.add_parser("handover", parents=[common], help="交接主控（主控唯一）")
    r.add_argument("name", help="新主控（须已在花名册）")
    r.add_argument("--old-role", choices=("former", "assistant", "observer"), default="former")
    r.add_argument("--old-box", default="", help="旧主控转助手时用的收件箱")
    r.add_argument("--no-inherit", action="store_true", help="新主控不继承旧主控的主控箱已读记录")
    r.set_defaults(func=cmd_roster_handover)
    r = rs.add_parser("limit", parents=[common], help="设在任助手上限")
    r.add_argument("n", type=int)
    r.set_defaults(func=cmd_roster_limit)
    r = rs.add_parser("requests", parents=[common], help="加入申请（主控或 roster.admin）")
    r.add_argument("--all", action="store_true", help="连同已批、已拒的")
    r.set_defaults(func=cmd_roster_requests)
    r = rs.add_parser("approve", parents=[common], help="批准加入申请（--allow/--deny 顺带授权只有主控能用）")
    r.add_argument("name")
    r.add_argument("--role", choices=("assistant", "observer"), help="默认按申请")
    r.add_argument("--box", default="", help="收件箱（默认用名字）")
    r.add_argument("--allow", action="append", help="额外授予的权限（可逗号分隔）")
    r.add_argument("--deny", action="append", help="收回的权限（可逗号分隔）")
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_roster_decide, op="approve")
    r = rs.add_parser("reject", parents=[common], help="拒绝加入申请")
    r.add_argument("name")
    r.add_argument("--reason", default="")
    r.set_defaults(func=cmd_roster_decide, op="reject")
    r = rs.add_parser("policy", parents=[common], help="查看或改通信策略")
    r.add_argument("--assistant-to-assistant", choices=("allow", "cc-controller", "deny"))
    r.add_argument("--broadcast", choices=("controller", "anyone"))
    r.add_argument("--inactive-send", choices=("deny", "allow"))
    r.set_defaults(func=cmd_roster_policy)

    s = sp("perm", "权限：查看；授予、收回、重置、改角色预设、发身份令牌（只有主控或用户能改）")
    ps_ = s.add_subparsers(dest="action", metavar="<动作>")
    s.set_defaults(func=cmd_perm_show, name=None)
    r = ps_.add_parser("list", parents=[common], help="权限目录与各角色预设")
    r.set_defaults(func=cmd_perm_list)
    r = ps_.add_parser("show", parents=[common], help="有效权限（给名字看某人；不给名字看本方，没有 --as 时列全体在任者）")
    r.add_argument("name", nargs="?")
    r.set_defaults(func=cmd_perm_show)
    r = ps_.add_parser("check", parents=[common], help="判定某人能否用某权限（附依据）")
    r.add_argument("name")
    r.add_argument("perm", help="权限键，也可写成 键:范围")
    r.add_argument("--target", help="对谁（参与方、角色组或队列名）")
    r.set_defaults(func=cmd_perm_check)
    for op, help_ in (("grant", "授予权限（同时去掉同名的收回条目）"), ("revoke", "收回权限（显式收回，压过角色预设）"),
                      ("reset", "去掉显式条目，回到角色预设（不给权限则全部去掉）")):
        r = ps_.add_parser(op, parents=[common], help=help_)
        r.add_argument("name")
        r.add_argument("perms", nargs="*" if op == "reset" else "+", help="权限，如 talk、talk:kimi-1、queue.take:review")
        r.set_defaults(func=cmd_perm_edit, op=op)
    r = ps_.add_parser("preset", parents=[common], help="查看或改角色预设")
    r.add_argument("role", choices=PRESET_ROLES)
    r.add_argument("--set", help="整体改为这些权限（逗号分隔；空串表示无）")
    r.add_argument("--add", action="append")
    r.add_argument("--remove", action="append")
    r.add_argument("--reset", action="store_true", help="恢复为由通信策略推出的预设")
    r.set_defaults(func=cmd_perm_preset)
    r = ps_.add_parser("token", parents=[common], help="给某人（或 user）设身份令牌，防止 --as 冒充")
    r.add_argument("name", help="参与方名或 user")
    r.add_argument("--clear", action="store_true", help="清除令牌")
    r.add_argument("--out", help="把令牌写进这个文件（只有本人可读），而不是打印")
    r.set_defaults(func=cmd_perm_token)

    s = sp("send", "投递一封信（排他编号、现取时间、读回 SHA）")
    s.add_argument("--to", help="收件人：参与方名/别名、controller、all、assistants，可逗号分隔；助手默认写给主控")
    s.add_argument("--subject", help="主题（与 --draft 自带标题二选一）")
    src = s.add_mutually_exclusive_group()
    src.add_argument("--draft", help="完整草稿文件（首行可为「# 致 X：主题」）")
    src.add_argument("--body-file", help="正文文件")
    src.add_argument("--stdin", action="store_true", help="正文从标准输入读")
    src.add_argument("--body", help="短正文（长正文请用文件，避免 shell 转义）")
    s.add_argument("--urgent", action="store_true", help="紧急：文件名带 -URGENT")
    s.add_argument("--reply-to", help="回复哪封（941、claude-941 或文件名）")
    s.add_argument("--corrects", help="更正哪封（原件不改，另函更正）")
    s.add_argument("--attach", action="append", help="附件文件：投递时实算字节数与 SHA-256 列在信末")
    s.add_argument("--start", type=int, help="起始编号：取不小于它的第一个空号（兼容按起始号投递的旧脚本）")
    s.add_argument("--normalize", action="store_true", help="自动去 BOM、CRLF 换成 LF")
    s.add_argument("--keep-header", action="store_true", help="保留草稿自带的「发件：」信头")
    s.add_argument("--force-title", action="store_true", help="不检查标题是否点名收件人")
    s.add_argument("--allow-secret", action="store_true", help="确认不是凭据时放行疑似凭据")
    s.add_argument("--allow-inactive", action="store_true", help="允许投给不在任者的旧收件箱")
    s.add_argument("--dry-run", action="store_true", help="只渲染预览，不投递")
    s.set_defaults(func=cmd_send)

    s = sp("inbox", "列出本方未读（--all 列全部）")
    s.add_argument("--all", action="store_true")
    s.add_argument("--others", action="store_true", help="也列共享箱里点名给别人的信")
    s.add_argument("--paths", action="store_true", help="每行附完整路径")
    s.add_argument("--box", help="只看某个收件箱")
    s.add_argument("--since", type=int, help="只看编号大于它的信")
    s.add_argument("--limit", type=int, default=0)
    s.set_defaults(func=cmd_inbox)

    s = sp("read", "读信并标为已读")
    s.add_argument("refs", nargs="+", help="941、claude-941、codex-338-URGENT 或完整文件名")
    s.add_argument("--peek", action="store_true", help="只看不标已读")
    s.set_defaults(func=cmd_read)

    s = sp("mark", "标为已读（--unread 反过来标为未读，下次 watch 会再报）")
    s.add_argument("refs", nargs="*")
    s.add_argument("--all", action="store_true", help="本方各箱全部")
    s.add_argument("--from-num", type=int, help="本方各箱里编号不小于它的全部（可配 --box）")
    s.add_argument("--box")
    s.add_argument("--unread", action="store_true", help="标为未读")
    s.set_defaults(func=cmd_mark)

    s = sp("search", "按正则搜信")
    s.add_argument("pattern")
    s.add_argument("--box")
    s.add_argument("--from", dest="sender", help="只看某参与方发的信")
    s.add_argument("--since", type=int)
    s.add_argument("--title-only", action="store_true")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_search)

    s = sp("watch", "监视来信与事件；--once 时有事件即退出（配合后台任务唤醒）")
    s.add_argument("--once", action="store_true")
    s.add_argument("--seconds", type=float, default=None, help="最长监视秒数（--once 默认 1790，循环模式默认不限）")
    s.add_argument("--interval", type=float, default=5.0, help="轮询间隔秒")
    s.add_argument("--coalesce", type=float, default=2.0, help="首个事件后再等几秒合并同批事件（最长 15 秒）")
    s.add_argument("--others", choices=("digest", "trigger", "off"), default="digest",
                   help="共享箱里写给别人的信：附带摘要 / 也唤醒 / 忽略")
    s.add_argument("--mentions", action="store_true", help="别的箱里点名本方的信也唤醒")
    s.add_argument("--ledger", action="append", help="监视总账等文件里含标记的新行")
    s.add_argument("--marker", action="append", help="总账标记（默认本方名与别名）")
    s.add_argument("--journal", action="append", help="监视 JSONL 工作流日志（glob）")
    s.add_argument("--journal-types", default="started,result")
    s.add_argument("--wait-file", action="append", help="等这个文件出现")
    s.add_argument("--wait-from", help="从文件读要等的路径（每行一个，避免路径出现在命令行）")
    s.add_argument("--remind-status", type=float, default=0, help="多少分钟没发信且有未完成任务就提醒报状态")
    s.add_argument("--remind-wait", type=float, default=0, help="waiting-on 等多少分钟算超时（指数退避，最多 3 次）")
    s.add_argument("--nudge", type=float, default=0, help="有未完成任务时多少分钟无事件发心跳（指数退避，最多 4 次）")
    s.add_argument("--no-takeover", action="store_true", help="已有同名监视在跑时拒绝启动（默认接管）")
    s.add_argument("--no-rules", action="store_true", help="事件输出不附处理规则")
    s.set_defaults(func=cmd_watch)

    s = sp("tasks", "本方任务清单、等待对象、等待文件")
    s.add_argument("action", nargs="?", choices=("show", "set", "remove", "replace", "waiting-on", "wait-add",
                                                 "wait-remove"))
    s.add_argument("id", nargs="?")
    s.add_argument("--status", choices=TASK_STATES, default="open")
    s.add_argument("--next")
    s.add_argument("--what")
    s.add_argument("--clear", action="store_true")
    s.add_argument("--path")
    s.add_argument("--note")
    s.add_argument("--file", help="replace 用：JSON 数组 [{id, status, next}]（不给则读标准输入）")
    s.set_defaults(func=cmd_tasks)

    s = sp("notes", "本方续接备忘（会话重启后从这里续）")
    s.add_argument("action", nargs="?", choices=("show", "append", "replace", "path"))
    s.add_argument("--text")
    s.add_argument("--file")
    s.set_defaults(func=cmd_notes)

    s = sp("status", "状态板：长任务的实时快照（原子替换的 JSON）")
    s.add_argument("action", nargs="?", choices=("list", "get", "put"))
    s.add_argument("key", nargs="?")
    s.add_argument("--to", help="写给谁的箱（默认主控箱）")
    s.add_argument("--box")
    s.add_argument("--file")
    s.set_defaults(func=cmd_status)

    def text_opts(r, what, required=False):
        g = r.add_mutually_exclusive_group(required=required)
        g.add_argument("--text", help=what)
        g.add_argument("--file", help="从文件读%s（长内容用它，免得 shell 转义）" % what)
        r.add_argument("--allow-secret", action="store_true", help="确认不是凭据时放行疑似凭据")

    s = sp("notify", "通知：给别人发一条轻量提醒（不占信件编号），或查看本方的通知")
    ns = s.add_subparsers(dest="action", metavar="<动作>")
    s.set_defaults(func=cmd_notify_list, all=False, limit=0, peek=False)
    r = ns.add_parser("send", parents=[common], help="发通知（to 可写名字、controller、assistants、all）")
    r.add_argument("--to", required=True)
    text_opts(r, "通知内容（最多 4 KB）", True)
    r.add_argument("--title", default="")
    r.add_argument("--urgent", action="store_true")
    r.set_defaults(func=cmd_notify_send)
    r = ns.add_parser("list", parents=[common], help="本方通知（默认只列没看过的，并记为已看）")
    r.add_argument("--all", action="store_true", help="列最近的全部（配 --limit）")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--peek", action="store_true", help="只看不记为已看")
    r.set_defaults(func=cmd_notify_list)

    s = sp("talk", "通话：多模型自由对话（一对一或多方）、同步呼叫、来电队列")
    ts = s.add_subparsers(dest="action", metavar="<动作>")
    s.set_defaults(func=cmd_talk_list, all=False, everyone=False)
    r = ts.add_parser("start", parents=[common], help="发起通话（to 可写多人、assistants、all）并发第一句")
    r.add_argument("--to", required=True)
    r.add_argument("--topic", default="")
    text_opts(r, "内容（最多 16 KB）", True)
    r.add_argument("--urgent", action="store_true")
    r.add_argument("--wait", type=float, default=0, help="等对方回复的秒数（0 不等）")
    r.set_defaults(func=cmd_talk_start)
    r = ts.add_parser("say", parents=[common], help="在通话里发言")
    r.add_argument("conv", help="通话编号，如 c12（也可只写 12）")
    text_opts(r, "内容（最多 16 KB）", True)
    r.add_argument("--reply-to", type=int, help="回复第几条")
    r.add_argument("--urgent", action="store_true")
    r.add_argument("--wait", type=float, default=0, help="等别人回复的秒数（0 不等）")
    r.set_defaults(func=cmd_talk_say)
    r = ts.add_parser("call", parents=[common], help="呼叫某人：沿用与他的一对一通话（没有就新建），发言并等回复")
    r.add_argument("who")
    text_opts(r, "内容（最多 16 KB）", True)
    r.add_argument("--topic", default="")
    r.add_argument("--new", action="store_true", help="另起一个新通话")
    r.add_argument("--urgent", action="store_true", help="紧急：排到对方来电队列最前")
    r.add_argument("--wait", type=float, default=120, help="等回复的秒数（默认 120，0 不等）")
    r.set_defaults(func=cmd_talk_call)
    r = ts.add_parser("read", parents=[common], help="读通话记录（默认最近 50 条），并把读到的记为已看")
    r.add_argument("conv")
    r.add_argument("--since", type=int, help="只看第几条之后的")
    r.add_argument("--unread", action="store_true", help="只看未读")
    r.add_argument("--limit", type=int, default=50)
    r.add_argument("--peek", action="store_true", help="只看不记为已看")
    r.set_defaults(func=cmd_talk_read)
    r = ts.add_parser("list", parents=[common], help="本方的通话与未读数")
    r.add_argument("--all", action="store_true", help="连同已结束的")
    r.add_argument("--everyone", action="store_true", help="信箱里全部通话（要 talk.audit）")
    r.set_defaults(func=cmd_talk_list)
    r = ts.add_parser("queue", parents=[common], help="来电队列：等着回复的通话（紧急优先，先来后到）")
    r.add_argument("--for", dest="who", help="看别人的（要 talk.audit）")
    r.set_defaults(func=cmd_talk_queue)
    r = ts.add_parser("next", parents=[common], help="接下一个来电：取队首通话的未读消息并记为已看")
    r.add_argument("--wait", type=float, default=0, help="队列空时等多少秒")
    r.add_argument("--peek", action="store_true")
    r.set_defaults(func=cmd_talk_next)
    r = ts.add_parser("wait", parents=[common], help="等通话里的新消息")
    r.add_argument("conv", nargs="?", help="只等这个通话（默认本方全部通话）")
    r.add_argument("--timeout", type=float, default=600)
    r.set_defaults(func=cmd_talk_wait)
    for op, help_ in (("invite", "邀请别人加入通话"), ("leave", "退出通话"), ("close", "结束通话（发起人或主控）")):
        r = ts.add_parser(op, parents=[common], help=help_)
        r.add_argument("conv")
        if op == "invite":
            r.add_argument("--to", required=True)
        r.set_defaults(func=cmd_talk_change, op=op)

    s = sp("queue", "工作队列：投任务、排队领取（租约、优先级、到期回收）、等结果")
    qs = s.add_subparsers(dest="action", metavar="<动作>")
    s.set_defaults(func=cmd_queue_list)
    r = qs.add_parser("list", parents=[common], help="全部队列与各状态任务数")
    r.set_defaults(func=cmd_queue_list)
    r = qs.add_parser("create", parents=[common], help="建队列或改设置（queue.admin）")
    r.add_argument("name")
    r.add_argument("--desc")
    r.add_argument("--lease", type=int, help="领取后的租约秒数（默认 900，到期没完成就放回排队）")
    r.add_argument("--max-attempts", type=int, help="最多尝试几次（默认 3）")
    r.add_argument("--consumers", help="领取名单（逗号分隔；空串表示不限）")
    r.add_argument("--notify", dest="notify", action="store_const", const=True, default=None, help="新任务通知可领的人（默认）")
    r.add_argument("--no-notify", dest="notify", action="store_const", const=False, help="新任务不发通知")
    r.set_defaults(func=cmd_queue_create)
    r = qs.add_parser("push", parents=[common], help="投任务（queue.push；队列不存在时有 queue.admin 会自动建）")
    r.add_argument("name")
    r.add_argument("--title", required=True)
    text_opts(r, "任务内容（最多 16 KB）")
    r.add_argument("--priority", type=int, default=0, help="越大越先领")
    r.add_argument("--to", help="指派给某人（只有他能领）")
    r.add_argument("--wait", type=float, default=0, help="等任务结束的秒数")
    r.set_defaults(func=cmd_queue_push)
    r = qs.add_parser("take", parents=[common], help="领下一个任务（queue.take）；--wait 时排队等")
    r.add_argument("name")
    r.add_argument("--wait", type=float, default=0)
    r.set_defaults(func=cmd_queue_take)
    for op, help_ in (("done", "完成（--text 写结果）"), ("fail", "记为失败（--retry 还有次数就放回重试）"),
                      ("release", "做不了，放回排队（不算一次尝试）"), ("renew", "续租"),
                      ("cancel", "取消（投放者或 queue.admin）")):
        r = qs.add_parser(op, parents=[common], help=help_)
        r.add_argument("name")
        r.add_argument("job", type=int)
        text_opts(r, "结果" if op == "done" else "说明")
        if op == "fail":
            r.add_argument("--retry", action="store_true")
        r.set_defaults(func=cmd_queue_act, op=op)
    r = qs.add_parser("show", parents=[common], help="队列里的任务")
    r.add_argument("name")
    r.add_argument("--all", action="store_true", help="列出全部已结束的（默认最近 10 个）")
    r.set_defaults(func=cmd_queue_show)
    r = qs.add_parser("job", parents=[common], help="一个任务的详情（内容、结果、经过）")
    r.add_argument("name")
    r.add_argument("job", type=int)
    r.set_defaults(func=cmd_queue_job)
    r = qs.add_parser("wait", parents=[common], help="等任务结束，打印结果")
    r.add_argument("name")
    r.add_argument("job", type=int)
    r.add_argument("--timeout", type=float, default=600)
    r.set_defaults(func=cmd_queue_wait)

    s = sp("join", "申请加入信箱（--as 写想用的名字），排队等主控批准")
    s.add_argument("--model", default="")
    s.add_argument("--harness", default="")
    s.add_argument("--display", default="")
    s.add_argument("--note", default="", help="附言：你是谁、能做什么")
    s.add_argument("--role", choices=("assistant", "observer"), default="assistant")
    s.add_argument("--wait", type=float, default=0, help="等批准的秒数")
    s.add_argument("--status", action="store_true", help="只查申请状态")
    s.set_defaults(func=cmd_join)

    s = sp("who", "在线与进度总览：谁在监视、未读、上次发信、任务")
    s.set_defaults(func=cmd_who)

    s = sp("doctor", "只读体检：花名册、命名、撞号、卫生、锁、投递后改动")
    s.add_argument("--all-threads", action="store_true")
    s.set_defaults(func=cmd_doctor)

    s = sp("next", "查看下一个空号（不占号）")
    s.add_argument("--to")
    s.add_argument("--box")
    s.add_argument("--urgent", action="store_true")
    s.set_defaults(func=cmd_next)

    s = sp("template", "打印信件模板")
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_template)

    s = sp("install", "把本 skill 复制到各宿主的 skills 目录")
    s.add_argument("target", nargs="+", help="agents / claude / codex / pi / dir:<目录>")
    s.add_argument("--force", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_install)

    s = sp("selftest", "在临时目录里跑一遍全流程自检（不碰真实信箱）")
    s.add_argument("--keep", action="store_true")
    s.set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    setup_stdio()
    return dispatch(argv)


def dispatch(argv):
    parser = build_parser()
    try:
        a = parser.parse_args(argv)
    except SystemExit as e:  # argparse 用法错误或 --help
        return e.code if isinstance(e.code, int) else 2
    for k, v in GLOBAL_DEFAULTS.items():
        if not hasattr(a, k):
            setattr(a, k, v)
    if a.me is None:
        a.me = os.environ.get("AGENT_MAILBOX_ME") or None
    if not getattr(a, "func", None):
        parser.print_help(_out())
        return 2
    try:
        return a.func(a) or 0
    except CliError as e:
        warn("错误：%s" % e)
        return e.code
    except OSError as e:
        warn("错误：文件操作失败：%s" % e)
        return 1
    except KeyboardInterrupt:
        return 130


class _Buffer(object):
    def __init__(self):
        self.parts = []

    def write(self, s):
        self.parts.append(s)

    def flush(self):
        pass

    def getvalue(self):
        return "".join(self.parts)


def run_cli(argv):
    """在本进程里执行一条命令，返回（退出码，标准输出文本，错误文本）。
    供 MCP 服务并发调用：输出写进本线程的缓冲区；禁止读标准输入；argparse 的帮助与报错也收进缓冲区。"""
    out, err = _Buffer(), _Buffer()
    saved = (getattr(_TLS, "out", None), getattr(_TLS, "err", None), getattr(_TLS, "no_stdin", False))
    _TLS.out, _TLS.err, _TLS.no_stdin = out, err, True
    real_out, real_err = sys.stdout, sys.stderr
    try:
        code = dispatch(list(argv))
    except Exception as e:  # 兜底：任何意外都不能把 MCP 服务打崩
        code = 1
        err.write("错误：内部异常 %s: %s\n" % (type(e).__name__, e))
    finally:
        _TLS.out, _TLS.err, _TLS.no_stdin = saved
        sys.stdout, sys.stderr = real_out, real_err
    return code, out.getvalue(), err.getvalue()


if __name__ == "__main__":
    sys.exit(main())
