#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-mailbox：多模型协作信箱命令行（主控唯一，助手可增减）。

只用 Python 标准库（3.8+），Windows / Linux / macOS / WSL 通用；监视循环不派生子进程。

- 信件是信箱目录下的平铺文件：to-<收件箱>-<线程>-<编号>[-URGENT].md（UTF-8、LF、无 BOM）。
- 控制目录 <信箱>/.agent-mailbox/ 存花名册 config.json、游标、任务、备忘、在线状态、锁和日志。
- 投递：编号锁 + open(..., 'xb') 原子排他创建；编号被占就顺延，绝不覆盖；写后读回核 SHA-256。
- 信头时间由本工具在投递时现取，不靠手写。

协议全文见 ../references/protocol.md，命令详解见 ../references/commands.md。
"""
import argparse
import copy
import datetime as dt
import glob
import hashlib
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

VERSION = "1.0.0"
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
]
RULES = ("处理规则：① 先 read 原信或原件再行动，不凭摘要猜；② 只处理写给本方的事项，点名他人的不碰；"
         "③ 收到登记或返修先发开工回执；④ 写信只用 send（排他编号、现取时间、读回 SHA）；⑤ 处理完重挂 watch。")
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
    ctrls = [p.get("name") for p in ps if p.get("role") == "controller" and p.get("active")]
    if len(ctrls) != 1:
        errors.append("必须恰有一名在任主控，当前 %d 名：%s" % (len(ctrls), "、".join(ctrls) or "无"))
    assistants = [p for p in ps if p.get("role") == "assistant" and p.get("active")]
    if isinstance(mx, int) and mx >= 0 and len(assistants) > mx:
        errors.append("在任助手 %d 名，超过上限 max_assistants=%d" % (len(assistants), mx))
    return errors, warns


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
class Mailbox(object):
    def __init__(self, root, config_path=None, state_dir=None, read_only=False):
        self.root = os.path.abspath(root)
        self.ctl = os.path.abspath(state_dir) if state_dir else os.path.join(self.root, CONTROL)
        self.config_path = os.path.abspath(config_path) if config_path else os.path.join(self.ctl, "config.json")
        self.read_only = bool(read_only)
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

    def identity(self, name, need_active=True):
        if not name:
            raise CliError("没有本方身份：加 --as <参与方名>，或设环境变量 AGENT_MAILBOX_ME", 2)
        if name == "user":
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
        return p

    def require_controller(self, me):
        if me is USER:
            return
        c = self.controller()
        if me["name"] != c["name"]:
            raise CliError("只有主控（%s）或用户（--as user）能做这项操作；你是 %s" % (c["name"], me["name"]), 2)

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
    mb = Mailbox(root, a.config, a.state_dir, a.read_only)
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
                     "aliases": p["aliases"], "since": p["since"], "until": p["until"], "note": p["note"]})
    return rows


def cmd_roster_show(a):
    mb = open_mb(a)
    cfg = mb.cfg
    rows = roster_rows(mb)
    if a.json:
        emit_json({"thread": cfg["thread"], "revision": cfg.get("revision"), "controller_box": cfg["controller_box"],
                   "broadcast_box": cfg["broadcast_box"], "max_assistants": cfg["max_assistants"],
                   "policy": cfg["policy"], "participants": rows, "warnings": mb.config_warnings})
        return 0
    act = [r for r in rows if r["role"] == "assistant" and r["active"]]
    emit("信箱 %s｜线程 %s｜花名册第 %s 版｜主控箱 %s｜广播箱 %s｜在任助手 %d/%d" % (
        slash(mb.root), cfg["thread"] or "（无）", cfg.get("revision"), cfg["controller_box"],
        cfg["broadcast_box"], len(act), cfg["max_assistants"]))
    for r in sorted(rows, key=lambda r: (not r["active"], ROLES.index(r["role"]) if r["role"] in ROLES else 9)):
        emit("  %s %-18s %-4s  %s / %s%s  收件箱 %s%s" % (
            "●" if r["active"] else "○", r["name"], ROLE_ZH.get(r["role"], r["role"]), r["model"] or "-",
            r["harness"] or "-", ("  会话 " + r["session"]) if r["session"] else "",
            ",".join(r["inbox"]) or "-", ("  别名 " + "、".join(r["aliases"])) if r["aliases"] else ""))
    for w in mb.config_warnings:
        emit("  注意：" + w)
    return 0


def default_box(name):
    return re.sub(r"[._]+", "-", name).strip("-")


def cmd_roster_add(a):
    mb = open_mb(a)
    me = mb.identity(a.me or "")
    mb.require_controller(me)
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
        mb.require_controller(me)

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
    mb.require_controller(me)
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
    mb.require_controller(me)

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


# ------------------------------------------------------------------ 发信
def cmd_send(a):
    mb = open_mb(a)
    mb.require_writable()
    cfg = mb.cfg
    pol = cfg["policy"]
    me = mb.identity(a.me or "", need_active=False)
    if me is not USER:
        if me["role"] == "observer":
            raise CliError("旁听者（observer）不发信", 2)
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
    if broadcast and pol["broadcast"] == "controller" and not (me is USER or me["role"] == "controller"):
        raise CliError("按策略只有主控能广播（policy.broadcast=controller）", 2)
    cc = []
    if me is not USER and me["role"] == "assistant":
        peers = [t for t in people if t["role"] == "assistant"]
        if peers and pol["assistant_to_assistant"] == "deny":
            raise CliError("按策略助手之间不直接通信（policy.assistant_to_assistant=deny），请经主控转达", 2)
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

    def setup(self):
        letters = self.mb.scan()[0]
        self.st = copy.deepcopy(self.mb.state(self.me, my_letters(self.mb, self.me, letters)))
        self.notified = set(self.st.get("notified") or [])
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
                mt = os.stat(path).st_mtime
            except OSError:
                if src != "tasks":
                    keep.append((path, note, src))
                continue
            if fired.get(path) == mt:
                continue
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

        def fn(d):
            d["notified"] = sorted(set(d.get("notified") or []) | new)
            d.setdefault("baseline_at", st.get("baseline_at") or iso_utc())
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
        beat = ts_of(pres.get("beat"))
        online = bool(beat and not pres.get("stopped") and time.time() - beat < 3 * Presence.BEAT)
        cur = load_json_quiet(mb.cursor_path(p["name"])) or {}
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
                     "unread": unread, "last_sent": last,
                     "tasks_open": len([x for x in t["tasks"] if x.get("status") != "done"]),
                     "waiting_on": (t.get("waiting_on") or {}).get("what")})
    if a.json:
        emit_json(rows)
        return 0
    emit("信箱 %s｜线程 %s｜%s" % (slash(mb.root), mb.thread or "（无）", mb.fmt_time(time.time())))
    for r in rows:
        last = ts_of(r["last_sent"])
        emit("  %s %-18s %-4s %-18s 监视%s｜未读 %s｜上次发信 %s｜未完成 %d%s" % (
            "●" if r["online"] else "○", r["name"], ROLE_ZH.get(r["role"]), clip("%s/%s" % (r["model"] or "-", r["harness"] or "-"), 18),
            "在线" if r["online"] else ("停于 " + (r["watch"]["stopped"] or "?") if r["watch"]["pid"] else "从未"),
            "-" if r["unread"] is None else r["unread"], ago(time.time() - last) if last else "无记录",
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
           and len(((byid.get(2) or {}).get("result") or {}).get("tools") or []) == 14
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
                   "json": False}


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
    r = rs.add_parser("policy", parents=[common], help="查看或改通信策略")
    r.add_argument("--assistant-to-assistant", choices=("allow", "cc-controller", "deny"))
    r.add_argument("--broadcast", choices=("controller", "anyone"))
    r.add_argument("--inactive-send", choices=("deny", "allow"))
    r.set_defaults(func=cmd_roster_policy)

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
