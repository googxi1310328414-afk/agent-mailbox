"""agent-mailbox 单元与集成测试（只用临时目录）。
用法：python -I -B -m unittest -v tests/test_mailbox.py      （在 agent-mailbox-skill 目录下）
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.environ.get("AMB_SCRIPT") or os.path.join(os.path.dirname(HERE), "agent-mailbox", "scripts", "mailbox.py")
spec = importlib.util.spec_from_file_location("mailbox_mod", SCRIPT)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class Box(object):
    """一个临时信箱：init + 若干助手。"""

    def __init__(self, assistants=(("helper-a", "claude"), ("helper-b", "claude"), ("helper-c", "")), env=None):
        self.dir = tempfile.mkdtemp(prefix="amb-test-")
        self.mb = os.path.join(self.dir, "mailbox")
        os.makedirs(self.mb)
        self.env = dict(os.environ, **(env or {}))
        r = self.run("init", "--controller", "boss", "--model", "gpt-5.5", "--harness", "pi", "--thread", "0a0b0c0d",
                     "--controller-box", "codex")
        assert r[0] == 0, r
        for name, box in assistants:
            args = ["roster", "add", name, "--as", "boss", "--model", "m-" + name, "--harness", "h"]
            if box:
                args += ["--box", box]
            r = self.run(*args)
            assert r[0] == 0, r

    def run(self, *args, **kw):
        p = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--mailbox", self.mb] + list(args),
                           input=kw.get("stdin"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=kw.get("timeout", 120), env=kw.get("env", self.env))
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def jrun(self, *args):
        c, o, e = self.run(*(list(args) + ["--json"]))
        assert c == 0, (c, o, e)
        return json.loads(o)

    def popen(self, *args):
        return subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "--mailbox", self.mb] + list(args),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)

    def files(self, prefix="to-"):
        return sorted(x for x in os.listdir(self.mb) if x.startswith(prefix) and x.endswith(".md"))

    def mailbox(self, read_only=False):
        return M.Mailbox(self.mb, read_only=read_only)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class ParsingTest(unittest.TestCase):
    def test_letter_names(self):
        cases = {
            "to-codex-7c3e9a10-1273.md": ("codex", "7c3e9a10", 1273, ""),
            "to-codex-7c3e9a10-338-URGENT.md": ("codex", "7c3e9a10", 338, "URGENT"),
            "to-claude-001.md": ("claude", "", 1, ""),
            "to-agent-main-7c3e9a10-005.md": ("agent-main", "7c3e9a10", 5, ""),
            "to-all-0a0b0c0d-012.md": ("all", "0a0b0c0d", 12, ""),
        }
        for name, want in cases.items():
            m = M.LETTER_RE.match(name)
            self.assertIsNotNone(m, name)
            l = M.Letter("/x", name, m)
            self.assertEqual((l.box, l.thread, l.num, l.tag), want, name)
        for bad in ("to-codex-7c3e9a10-PINWIN-W1-STATUS.json", "draft-001.md", "to-codex-7c3e9a10-12.md",
                    ".to-codex-7c3e9a10-001.md.x.tmp"):
            self.assertIsNone(M.LETTER_RE.match(bad), bad)
        m = M.STATUS_RE.match("to-codex-7c3e9a10-PINWIN-W1-STATUS.json")
        self.assertEqual((m.group("box"), m.group("thread"), m.group("key")), ("codex", "7c3e9a10", "PINWIN-W1"))

    def test_title_parties_real_titles(self):
        cases = [
            ("# 致 Lead 主控：101 回复——上午的测试中断", ["Lead 主控"], ""),
            ("# 致 agent-main、agent-ui：统一口径", ["agent-main", "agent-ui"], ""),
            ("# Lead 主控 → Agent 助手 153：编号规则", ["Agent 助手"], "Lead 主控"),
            ("# lead-old → agent-main-20260101（001）", ["agent-main-20260101"], "lead-old"),
            ("# Lead主控 → Helper cal9 52e8c41b：D1", ["Helper cal9"], "Lead主控"),
            ("# Agent助手→Lead主控 250：撞号说明", ["Lead主控"], "Agent助手"),
            ("# Lead 602：致agent-ui，602/603 已处理", ["agent-ui", "602", "603 已处理"], "Lead 602"),
            ("致agent-ui-20260101/backend-design：620 R4", ["agent-ui-20260101", "backend-design"], ""),
            ("# Helper 助手→Lead 主控 511：用户指派", ["Lead 主控"], "Helper 助手"),
            ("# Lead主控：目标已恢复", [], "Lead主控"),
            ("# Agent 回执：一致性检查已过", [], "Agent 回执"),
        ]
        for title, to, frm in cases:
            got_to, got_frm = M.title_parties(title)
            self.assertEqual(got_to, to, title)
            self.assertEqual(got_frm, frm, title)

    def test_header_fields(self):
        raw = ("# 致 boss：x\n\n发件：helper-a（助手，m，h）｜时间：2026-10-26 18:00:00（北京）/ 2026-10-26T10:00:00Z"
               "｜编号：to-codex-0a0b0c0d-007｜回复：to-claude-0a0b0c0d-003\n\n正文\n").encode("utf-8")
        h = M.parse_head(raw)
        self.assertEqual(h["from_token"], "helper-a")
        self.assertEqual(h["fields"]["编号"], "to-codex-0a0b0c0d-007")
        self.assertEqual(h["fields"]["回复"], "to-claude-0a0b0c0d-003")
        legacy = "# x\n发件：agent-ui 7c3e9a10　时间：2026-10-26 17:26（北京）/ 09:26Z\n".encode("utf-8")
        h = M.parse_head(legacy)
        self.assertEqual(h["from_token"], "agent-ui")
        self.assertTrue(h["fields"]["时间"].startswith("2026-10-26 17:26"))

    def test_router(self):
        cfg = M.default_config("0a0b0c0d")
        cfg["participants"] = [M.norm_participant(p) for p in [
            {"name": "lead-new", "role": "controller", "display": "Lead 主控", "aliases": ["Lead"], "session": "52e8c41b"},
            {"name": "lead-helper", "role": "former", "aliases": [], "session": "52e8c7fa"},
            {"name": "agent-main", "role": "assistant", "box": "claude", "aliases": ["agent-main-20260101"]},
            {"name": "old", "role": "former", "aliases": ["agent-main"]},
        ]]
        r = M.Router(cfg)
        self.assertEqual(r.resolve("Lead 主控（52e8c41b）"), "lead-new")
        self.assertEqual(r.resolve("lead-helper"), "lead-helper")
        self.assertEqual(r.resolve("Lead cal9"), "lead-new")
        self.assertIsNone(r.resolve("leadership 组"))
        self.assertEqual(r.resolve("agent-main-20260101/x"), "agent-main")
        self.assertEqual(r.resolve("agent-main"), "agent-main")  # 在任者优先于同别名的前任
        self.assertEqual(r.resolve("某会话 52e8c7fa"), "lead-helper")  # 会话号兜底
        self.assertEqual(r.resolve("全体助手"), "@assistants")
        self.assertEqual(r.resolve("主控"), "@controller")

    def test_hygiene_and_secrets(self):
        self.assertEqual(M.hygiene("# a\n正文\n".encode("utf-8"), False), "# a\n正文\n")
        with self.assertRaises(M.CliError):
            M.hygiene(b"\xef\xbb\xbf# a\n", False)
        self.assertEqual(M.hygiene(b"\xef\xbb\xbf# a\r\nb\r\n", True), "# a\nb\n")
        with self.assertRaises(M.CliError):
            M.hygiene(("# a\n坏" + chr(0xFFFD) + "\n").encode("utf-8"), True)
        with self.assertRaises(M.CliError):
            M.hygiene(b"\xff\xfe", True)
        fake_ant = "sk-" + "ant-api03-" + "abcdefghijklmnopqrstuvwxyz"  # 分段拼接，免得被当成真密钥
        fake_gh = "gh" + "p_" + "a" * 36
        hits = M.scan_secrets("a\nkey " + fake_ant + "\n" + fake_gh + "\n", [])
        self.assertEqual(sorted(set(h[0] for h in hits)), [2, 3])
        clean = "SHA-256 c00aedd2f850663e8a51897c80e1d8b4d874e4b6bcf1af843ca11af2ee5841ff；token 刷新；task-sk-1 完成\n"
        self.assertEqual(M.scan_secrets(clean, []), [])
        self.assertTrue(M.scan_secrets("x MYPROJ_secret_123456", [r"MYPROJ_secret_\d+"]))


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.b = Box()

    def tearDown(self):
        self.b.close()

    def test_routing_and_classification(self):
        b = self.b
        self.assertEqual(b.run("send", "--as", "boss", "--to", "helper-a", "--subject", "s1", "--body", "x")[0], 0)
        self.assertEqual(b.run("send", "--as", "boss", "--to", "helper-a,helper-b", "--subject", "s2", "--body", "x")[0], 0)
        self.assertEqual(b.run("send", "--as", "boss", "--to", "all", "--subject", "s3", "--body", "x")[0], 0)
        self.assertEqual(b.run("send", "--as", "boss", "--to", "assistants", "--subject", "s4", "--body", "x")[0], 0)
        # 共享箱里没点名的旧式信（模拟旧工具写入）
        with open(os.path.join(b.mb, "to-claude-0a0b0c0d-050.md"), "wb") as f:
            f.write("# Codex主控：旧式未点名\n".encode("utf-8"))
        ia = b.jrun("inbox", "--as", "helper-a", "--all")
        ib = b.jrun("inbox", "--as", "helper-b", "--all")
        ic = b.jrun("inbox", "--as", "helper-c", "--all")
        ta = sorted(x["title"].split("：")[-1] for x in ia["letters"])
        tb = sorted(x["title"].split("：")[-1] for x in ib["letters"])
        tc = sorted(x["title"].split("：")[-1] for x in ic["letters"])
        self.assertEqual(ta, ["s1", "s2", "s3", "s4", "旧式未点名"])
        self.assertEqual(tb, ["s2", "s3", "s4", "旧式未点名"])
        self.assertEqual(tc, ["s3", "s4"])
        cls = dict((x["title"].split("：")[-1], x["class"]) for x in ia["letters"])
        self.assertEqual(cls["旧式未点名"], "shared")
        self.assertEqual(cls["s3"], "all")
        self.assertEqual(cls["s4"], "mine")
        # helper-b 被移除后，点名给它的信不会落到 helper-a 头上
        self.assertEqual(b.run("send", "--as", "boss", "--to", "helper-b", "--subject", "只给b", "--body", "x")[0], 0)
        self.assertEqual(b.run("roster", "remove", "helper-b", "--as", "boss")[0], 0)
        ia = b.jrun("inbox", "--as", "helper-a", "--all")
        self.assertNotIn("只给b", " ".join(x["title"] for x in ia["letters"]))

    def test_assistant_policy_and_broadcast(self):
        b = self.b
        c, o, e = b.run("send", "--as", "helper-a", "--to", "all", "--subject", "x", "--body", "x")
        self.assertNotEqual(c, 0)
        self.assertIn("广播", e)
        self.assertEqual(b.run("roster", "policy", "--as", "boss", "--assistant-to-assistant", "cc-controller")[0], 0)
        r = b.jrun("send", "--as", "helper-a", "--to", "helper-c", "--subject", "抄送测试", "--body", "x")
        self.assertEqual(sorted(x["box"] for x in r["sent"]), ["codex", "helper-c"])
        text = open(r["sent"][0]["path"], encoding="utf-8").read()
        self.assertIn("抄送：boss", text)
        self.assertEqual(b.run("roster", "policy", "--as", "boss", "--assistant-to-assistant", "deny")[0], 0)
        self.assertNotEqual(b.run("send", "--as", "helper-a", "--to", "helper-c", "--subject", "x", "--body", "x")[0], 0)
        self.assertEqual(b.run("send", "--as", "user", "--to", "helper-c", "--subject", "用户来信", "--body", "x")[0], 0)

    def test_numbering_start_and_suffix_race(self):
        b = self.b
        for i in range(3):
            b.run("send", "--as", "helper-a", "--subject", "n%d" % i, "--body", "x")
        os.unlink(os.path.join(b.mb, "to-codex-0a0b0c0d-002.md"))  # 人为留一个缺号
        r = b.jrun("send", "--as", "helper-a", "--subject", "补缺号", "--body", "x", "--start", "1")
        self.assertEqual(r["sent"][0]["number"], 2)
        r = b.jrun("send", "--as", "helper-a", "--subject", "默认", "--body", "x")
        self.assertEqual(r["sent"][0]["number"], 4)
        # 模拟并发：本信（带 URGENT）排他创建前，另一个不认锁的旧工具抢先写了同号的无后缀信
        mb = b.mailbox()
        calls = {"n": 0}

        def render(num, name):
            calls["n"] += 1
            if calls["n"] == 1:
                with open(os.path.join(b.mb, mb.fmt_name("codex", "0a0b0c0d", num)), "xb") as f:
                    f.write("# 旧工具同号信\n".encode("utf-8"))
            return ("# 致 boss：撞号\n\n%s\n" % name).encode("utf-8")
        res = mb.deliver("codex", render, tag="URGENT")
        self.assertEqual(res["number"], 6)
        self.assertTrue(res["file"].endswith("-006-URGENT.md"))
        self.assertTrue(os.path.exists(os.path.join(b.mb, "to-codex-0a0b0c0d-005.md")))
        self.assertFalse(os.path.exists(os.path.join(b.mb, "to-codex-0a0b0c0d-005-URGENT.md")))
        self.assertIn("让号", " ".join(res["notes"]))
        # 反过来：本信无后缀、对方带后缀，本信保留，提示对方让号
        calls["n"] = 0

        def render2(num, name):
            calls["n"] += 1
            if calls["n"] == 1:
                with open(os.path.join(b.mb, mb.fmt_name("codex", "0a0b0c0d", num, "URGENT")), "xb") as f:
                    f.write(b"# x\n")
            return ("# 致 boss：x\n\n%s\n" % name).encode("utf-8")
        res = mb.deliver("codex", render2)
        self.assertEqual(res["number"], 7)
        self.assertIn("对方应让号", " ".join(res["notes"]))
        d = b.run("doctor")
        self.assertIn("同号多信", d[1])

    def test_concurrency_stress(self):
        b = self.b
        stop = threading.Event()
        legacy_written = []

        def legacy_writer():
            # 模拟旧 mail_send.py：取不小于起始号的第一个空号，'xb' 排他创建无后缀名
            k = 1
            while not stop.is_set() and len(legacy_written) < 10:
                path = os.path.join(b.mb, "to-codex-0a0b0c0d-%03d.md" % k)
                try:
                    with open(path, "xb") as f:
                        body = ("# 旧工具 %d\n" % k).encode("utf-8")
                        f.write(body)
                    legacy_written.append((path, body))
                except FileExistsError:
                    pass
                k += 1
                time.sleep(0.01)
        t = threading.Thread(target=legacy_writer)
        t.start()
        procs = []
        for i in range(6):
            for j in range(4):
                args = ["send", "--as", "helper-%s" % "abc"[i % 3], "--subject", "压测 %d-%d" % (i, j),
                        "--body", "内容 %d-%d" % (i, j), "--json"]
                if j % 2:
                    args.append("--urgent")
                procs.append(b.popen(*args))
        outs = [p.communicate(timeout=180) for p in procs]
        stop.set()
        t.join(30)
        self.assertTrue(all(p.returncode == 0 for p in procs), [o[1].decode("utf-8", "replace") for o in outs])
        sent = [json.loads(o[0].decode("utf-8"))["sent"][0] for o in outs]
        for s in sent:
            data = open(s["path"], "rb").read()
            self.assertEqual(M.sha256_bytes(data), s["sha256"], s["file"])
        for path, body in legacy_written:
            self.assertEqual(open(path, "rb").read(), body)
        nums = {}
        for n in b.files("to-codex-"):
            m = M.LETTER_RE.match(n)
            nums.setdefault(int(m.group("num")), []).append(n)
        self.assertEqual(len(sent) + len(legacy_written), sum(len(v) for v in nums.values()))
        ours = set(s["file"] for s in sent)
        for k, v in nums.items():
            # 本工具投的信绝不与任何信同号
            if len(v) > 1:
                self.assertFalse(ours & set(v), (k, v))

    def test_cursor_corruption_not_wiped(self):
        b = self.b
        b.run("send", "--as", "boss", "--to", "helper-c", "--subject", "x", "--body", "x")
        b.run("read", "--as", "helper-c", "helper-c-1")
        cur = os.path.join(b.mb, ".agent-mailbox", "cursors", "helper-c.json")
        with open(cur, "wb") as f:
            f.write(b'{"read": ["to-helper-c')
        c, o, e = b.run("mark", "--as", "helper-c", "--all")
        self.assertNotEqual(c, 0)
        self.assertIn("损坏", e)
        self.assertEqual(open(cur, "rb").read(), b'{"read": ["to-helper-c')

    def test_read_only_writes_nothing(self):
        b = self.b
        b.run("send", "--as", "boss", "--to", "helper-c", "--subject", "x", "--body", "x")
        before = sorted(os.listdir(os.path.join(b.mb, ".agent-mailbox", "cursors")))
        for args in (["inbox", "--as", "helper-a"], ["who"], ["doctor"], ["read", "--as", "helper-c", "helper-c-1"],
                     ["watch", "--as", "helper-c", "--once", "--seconds", "1", "--interval", "0.2"]):
            b.run("--read-only", *args)
        self.assertEqual(sorted(os.listdir(os.path.join(b.mb, ".agent-mailbox", "cursors"))), before)
        self.assertFalse(os.path.exists(os.path.join(b.mb, ".agent-mailbox", "presence", "helper-c.json")))

    def test_handover_inherits_and_roster_rules(self):
        b = self.b
        for i in range(3):
            b.run("send", "--as", "helper-a", "--subject", "旧主控的信 %d" % i, "--body", "x")
        b.run("read", "--as", "boss", "codex-1", "codex-2")
        self.assertNotEqual(b.run("roster", "add", "boss2", "--as", "boss", "--role", "assistant", "--model", "x",
                                  "--harness", "y", "--box", "codex")[0], 0)  # 个人箱不能叫主控箱的名字
        self.assertEqual(b.run("roster", "add", "boss2", "--as", "boss", "--model", "x", "--harness", "y")[0], 0)
        self.assertEqual(b.run("roster", "handover", "boss2", "--as", "boss", "--old-role", "assistant")[0], 0)
        ro = b.jrun("roster")
        ctrl = [p["name"] for p in ro["participants"] if p["role"] == "controller" and p["active"]]
        self.assertEqual(ctrl, ["boss2"])
        boss = [p for p in ro["participants"] if p["name"] == "boss"][0]
        self.assertTrue(boss["active"] and boss["role"] == "assistant")
        ib = b.jrun("inbox", "--as", "boss2")
        self.assertEqual([x["title"].split("：")[-1] for x in ib["letters"]], ["旧主控的信 2"])
        self.assertNotEqual(b.run("roster", "limit", "2", "--as", "boss2")[0], 0)  # 现有 4 名在任助手
        self.assertEqual(b.run("roster", "limit", "4", "--as", "boss2")[0], 0)
        self.assertNotEqual(b.run("roster", "add", "x5", "--as", "boss2", "--model", "x")[0], 0)
        self.assertEqual(b.run("roster", "update", "helper-a", "--as", "helper-a", "--session", "abc12345")[0], 0)
        self.assertNotEqual(b.run("roster", "update", "helper-a", "--as", "helper-a", "--box", "zzz")[0], 0)

    def test_draft_title_inference_and_checks(self):
        b = self.b
        d = os.path.join(b.dir, "d.md")
        with open(d, "wb") as f:
            f.write("# 致 helper-a、helper-c：草稿标题点名\n\n第 {{NUM}} 号，文件 {{FILE}}\n".encode("utf-8"))
        r = b.jrun("send", "--as", "boss", "--draft", d)
        self.assertEqual(sorted(x["to"] for x in r["sent"]), ["helper-a", "helper-c"])
        text = open(r["sent"][0]["path"], encoding="utf-8").read()
        self.assertIn("第 %d 号，文件 %s" % (r["sent"][0]["number"], r["sent"][0]["file"]), text)
        with open(d, "wb") as f:
            f.write("# 致 boss：x\n\n发件：helper-a（手写信头）\n\n正文\n".encode("utf-8"))
        self.assertNotEqual(b.run("send", "--as", "helper-a", "--draft", d)[0], 0)
        self.assertEqual(b.run("send", "--as", "helper-a", "--draft", d, "--keep-header")[0], 0)
        big = os.path.join(b.dir, "big.md")
        with open(big, "wb") as f:
            f.write(("x" * 70000).encode("utf-8"))
        c, o, e = b.run("send", "--as", "helper-a", "--subject", "大", "--body-file", big)
        self.assertNotEqual(c, 0)
        self.assertIn("超过上限", e)
        c, o, e = b.run("send", "--as", "helper-a", "--subject", "预览", "--body", "x", "--dry-run")
        self.assertEqual(c, 0)
        self.assertIn("编号：（投递时分配）", o)
        self.assertEqual(len(b.files("to-codex-")), 1)

    def test_attachments_and_corrects(self):
        b = self.b
        att = os.path.join(b.dir, "REPORT.md")
        with open(att, "wb") as f:
            f.write(b"report\n")
        r = b.jrun("send", "--as", "helper-a", "--subject", "交付", "--body", "x", "--attach", att)
        text = open(r["sent"][0]["path"], encoding="utf-8").read()
        self.assertIn(M.sha256_bytes(b"report\n"), text)
        r = b.jrun("send", "--as", "helper-a", "--subject", "更正", "--body", "x", "--corrects", "codex-1")
        text = open(r["sent"][0]["path"], encoding="utf-8").read()
        self.assertIn("更正：to-codex-0a0b0c0d-001", text)


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.b = Box()

    def tearDown(self):
        self.b.close()

    def watch(self, *extra, **kw):
        return self.b.run("watch", "--as", kw.get("me", "helper-c"), "--once", "--seconds", str(kw.get("seconds", 20)),
                          "--interval", "0.2", "--coalesce", "0.3", *extra)

    def test_ledger_journal_waitfile_mentions(self):
        b = self.b
        ledger = os.path.join(b.dir, "PROGRESS.md")
        with open(ledger, "wb") as f:
            f.write("| T1 | 旧行 helper-c |\n".encode("utf-8"))
        jdir = os.path.join(b.dir, "wf", "wf_1")
        os.makedirs(jdir)
        journal = os.path.join(jdir, "journal.jsonl")
        with open(journal, "wb") as f:
            f.write(b'{"type":"started","label":"a"}\n')
        self.assertEqual(self.watch("--seconds", "1", "--ledger", ledger, "--journal", os.path.join(b.dir, "wf", "*", "journal.jsonl"),
                                    "--mentions")[0], 3)  # 首次只建基线

        def later():
            time.sleep(1.0)
            with open(ledger, "ab") as f:
                f.write("| T2 | 新登记 owner helper-c |\n".encode("utf-8"))
            with open(journal, "ab") as f:
                f.write(b'{"type":"result","label":"rev","result":{"status":"ok","verdict":"pass"}}\n')
            b.run("send", "--as", "helper-a", "--subject", "提到 helper-c 的信", "--body", "请 helper-c 知悉")
        threading.Thread(target=later).start()
        c, o, e = self.watch("--ledger", ledger, "--journal", os.path.join(b.dir, "wf", "*", "journal.jsonl"), "--mentions")
        self.assertEqual(c, 0, o + e)
        self.assertIn("LEDGER", o)
        self.assertIn("T2", o)
        self.assertIn("JOURNAL wf_1 result rev ok pass", o)
        self.assertIn("MENTION", o)
        c, o, e = self.watch("--seconds", "1", "--ledger", ledger, "--mentions")
        self.assertEqual(c, 3, o)  # 已报过的不再报
        b.run("send", "--as", "helper-a", "--subject", "重挂间隙里点名", "--body", "helper-c 请看")
        c, o, e = self.watch("--mentions")
        self.assertEqual(c, 0, o + e)
        self.assertIn("重挂间隙里点名", o)  # 两次监视之间到的点名信不漏
        res = os.path.join(b.dir, "RESULT.json")
        self.assertEqual(b.run("tasks", "wait-add", "--as", "helper-c", "--path", res, "--note", "结果")[0], 0)
        threading.Timer(1.0, lambda: open(res, "wb").write(b'{"exit": 0}')).start()
        c, o, e = self.watch()
        self.assertEqual(c, 0, o + e)
        self.assertIn("WAITFILE", o)
        self.assertIn('"exit": 0', o)
        self.assertEqual(b.jrun("tasks", "--as", "helper-c")["waits"], [])

    def test_reminders(self):
        b = self.b
        b.run("tasks", "set", "T9", "--as", "helper-c", "--status", "open", "--next", "x")
        b.run("tasks", "waiting-on", "--as", "helper-c", "--what", "主控裁定")
        cur = os.path.join(b.mb, ".agent-mailbox", "cursors", "helper-c.json")
        tasks = os.path.join(b.mb, ".agent-mailbox", "tasks", "helper-c.json")
        b.run("inbox", "--as", "helper-c")  # 建基线
        d = json.load(open(cur, encoding="utf-8"))
        d["last_sent"] = {"at": "2026-01-01T00:00:00Z"}
        d["last_real_at"] = "2026-01-01T00:00:00Z"
        open(cur, "w", encoding="utf-8").write(json.dumps(d))
        t = json.load(open(tasks, encoding="utf-8"))
        t["waiting_on"]["since"] = "2026-01-01T00:00:00Z"
        open(tasks, "w", encoding="utf-8").write(json.dumps(t))
        c, o, e = self.watch("--remind-status", "60")
        self.assertEqual(c, 0, o + e)
        self.assertIn("STATUS-DUE", o)
        c, o, e = self.watch("--remind-status", "60", "--seconds", "1")
        self.assertEqual(c, 3, o)  # 同一周期内不重复提醒
        c, o, e = self.watch("--remind-wait", "30")
        self.assertIn("WAIT-STALE", o)
        self.assertEqual(json.load(open(tasks, encoding="utf-8"))["waiting_on"]["reminded"], 1)
        b.run("tasks", "waiting-on", "--as", "helper-c", "--clear")
        c, o, e = self.watch("--nudge", "10")
        self.assertIn("HEARTBEAT", o)

    def test_roster_change_and_takeover(self):
        b = self.b
        p = b.popen("watch", "--as", "helper-c", "--once", "--seconds", "30", "--interval", "0.2", "--coalesce", "0.3")
        time.sleep(1.5)
        self.assertEqual(b.run("roster", "handover", "helper-c", "--as", "boss")[0], 0)
        out, err = p.communicate(timeout=60)
        out = out.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0, out + err.decode("utf-8", "replace"))
        self.assertIn("ROSTER", out)
        self.assertIn("主控", out)
        p = b.popen("watch", "--as", "helper-a", "--once", "--seconds", "30", "--interval", "0.2")
        time.sleep(1.5)
        c, o, e = b.run("watch", "--as", "helper-a", "--once", "--seconds", "1", "--no-takeover")
        self.assertEqual(c, 5, o + e)
        q = b.popen("watch", "--as", "helper-a", "--once", "--seconds", "3", "--interval", "0.2")
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 4, out.decode("utf-8", "replace"))
        self.assertIn("SUPERSEDED", out.decode("utf-8", "replace"))
        q.communicate(timeout=60)
        p = b.popen("watch", "--as", "helper-b", "--once", "--seconds", "30", "--interval", "0.2")
        time.sleep(1.5)
        b.run("roster", "remove", "helper-b", "--as", "helper-c")
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("已不在在任名单", out.decode("utf-8", "replace"))
        who = b.jrun("who")
        self.assertEqual(sorted(w["name"] for w in who), ["helper-a", "helper-c"])  # boss 已卸任、helper-b 已移除

    def test_reported_not_repeated_and_gap_safe(self):
        b = self.b
        b.run("send", "--as", "boss", "--to", "helper-c", "--subject", "第一封", "--body", "x")
        c, o, e = self.watch()
        self.assertEqual(c, 0)
        self.assertIn("第一封", o)
        b.run("send", "--as", "boss", "--to", "helper-c", "--subject", "重挂间隙到的", "--body", "x")
        c, o, e = self.watch()
        self.assertIn("重挂间隙到的", o)
        self.assertNotIn("第一封", o)
        b.run("mark", "--as", "helper-c", "--from-num", "1", "--unread")
        c, o, e = self.watch()
        self.assertIn("第一封", o)
        self.assertIn("重挂间隙到的", o)


def slurp(path, mode="rb"):
    with open(path, mode) as f:
        return f.read()


class ReviewFixesTest(unittest.TestCase):
    """独立审查发现的问题逐条回归。"""

    def setUp(self):
        self.b = Box(assistants=(("helper-a", "claude"), ("helper-b", "claude"), ("helper-c", "")))

    def tearDown(self):
        self.b.close()

    def test_box_names_with_number_segments(self):
        b = self.b
        self.assertEqual(b.run("roster", "add", "agent-main-20260101", "--as", "boss", "--model", "x")[0], 0)
        self.assertEqual(b.run("roster", "add", "agent-007", "--as", "boss", "--model", "x")[0], 0)
        for who in ("agent-main-20260101", "agent-007"):
            for i in range(2):
                r = b.jrun("send", "--as", "boss", "--to", who, "--subject", "s%d" % i, "--body", "x")
                self.assertEqual(r["sent"][0]["number"], i + 1)
            ib = b.jrun("inbox", "--as", who, "--all")
            self.assertEqual(len(ib["letters"]), 2, who)
            c, o, e = b.run("next", "--to", who)
            self.assertTrue(o.strip().endswith("-003.md"), o)
        m = M.parse_letter_name("to-agent-main-20260101-0a0b0c0d-001.md", None)
        self.assertEqual((m.group("box"), m.group("thread"), m.group("num")), ("agent-main-20260101", "0a0b0c0d", "001"))
        d = b.jrun("doctor")
        self.assertFalse(d["errors"])

    def test_watch_gap_between_runs(self):
        b = self.b
        ledger = os.path.join(b.dir, "PROGRESS.md")
        with open(ledger, "wb") as f:
            f.write("| T1 | helper-c |\n".encode("utf-8"))
        wf = os.path.join(b.dir, "wf")
        os.makedirs(os.path.join(wf, "wf_1"))
        with open(os.path.join(wf, "wf_1", "journal.jsonl"), "wb") as f:
            f.write(b'{"type":"started","label":"a"}\n')
        g = os.path.join(wf, "*", "journal.jsonl")
        args = ["watch", "--as", "helper-c", "--once", "--interval", "0.2", "--coalesce", "0.3", "--ledger", ledger,
                "--journal", g, "--mentions"]
        c, o, e = b.run(*(args + ["--seconds", "1"]))
        self.assertEqual(c, 3, o + e)  # 第 1 轮超时：基线也必须落盘
        with open(ledger, "ab") as f:
            f.write("| T2 | 间隙里登记给 helper-c |\n".encode("utf-8"))
        os.makedirs(os.path.join(wf, "wf_2"))
        with open(os.path.join(wf, "wf_2", "journal.jsonl"), "wb") as f:
            f.write(b'{"type":"result","label":"rev2","result":{"status":"done","verdict":"FAIL"}}\n')
        b.run("roster", "add", "helper-d", "--as", "boss", "--model", "x")
        b.run("send", "--as", "boss", "--to", "helper-d", "--subject", "新箱里点名", "--body", "请转告 helper-c")
        c, o, e = b.run(*(args + ["--seconds", "20"]))
        self.assertEqual(c, 0, o + e)
        self.assertIn("间隙里登记给 helper-c", o)
        self.assertIn("JOURNAL wf_2 result rev2 done FAIL", o)
        self.assertIn("新箱里点名", o)

    def test_header_routing_and_display_validation(self):
        b = self.b
        c, o, e = b.run("roster", "add", "x1", "--as", "boss", "--display", "Claude/Kimi")
        self.assertNotEqual(c, 0)
        self.assertIn("分隔符", e)
        # 标题点名与信头「收件：」不一致时以信头为准（模拟别的工具写的信）
        with open(os.path.join(b.mb, "to-claude-0a0b0c0d-090.md"), "wb") as f:
            f.write("# 致 helper-b：标题写的是 b\n\n发件：boss（主控）｜收件：helper-a｜编号：x\n\n正文\n".encode("utf-8"))
        ia = b.jrun("inbox", "--as", "helper-a")
        ib = b.jrun("inbox", "--as", "helper-b")
        self.assertEqual([x["class"] for x in ia["letters"]], ["mine"])
        self.assertEqual(ib["unread"], 0)

    def test_handover_ordering_and_since(self):
        b = self.b
        for i in range(2):
            b.run("send", "--as", "boss", "--to", "all", "--subject", "旧广播 %d" % i, "--body", "x")
        for i in range(3):
            b.run("send", "--as", "helper-a", "--subject", "主控箱旧信 %d" % i, "--body", "x")
        past = time.time() - 600
        for n in os.listdir(b.mb):
            if n.startswith("to-"):
                os.utime(os.path.join(b.mb, n), (past, past))
        b.run("read", "--as", "boss", "codex-1", "codex-2", "codex-3")
        self.assertEqual(b.run("roster", "add", "newboss", "--as", "boss", "--model", "x")[0], 0)
        p = b.popen("watch", "--as", "newboss", "--once", "--seconds", "30", "--interval", "0.05", "--coalesce", "0.3")
        time.sleep(1.5)
        self.assertEqual(b.run("roster", "handover", "newboss", "--as", "boss")[0], 0)
        out, err = p.communicate(timeout=60)
        out = out.decode("utf-8", "replace")
        self.assertIn("ROSTER", out)
        self.assertNotIn("主控箱旧信", out)
        self.assertNotIn("旧广播", out)
        self.assertEqual(b.jrun("inbox", "--as", "newboss")["unread"], 0)

    def test_lock_release_when_reader_holds_file(self):
        lockp = os.path.join(self.b.dir, "x.lock")
        lk = M.FileLock(lockp)
        lk.acquire()
        f = open(lockp, "rb")
        threading.Timer(0.3, f.close).start()
        lk.release()
        time.sleep(0.5)
        self.assertFalse(os.path.exists(lockp))
        t0 = time.time()
        with M.FileLock(lockp, timeout=3):
            pass
        self.assertLess(time.time() - t0, 1.0)

    def test_persist_never_rolls_back(self):
        b = self.b
        mb = b.mailbox()
        me = mb.get("helper-c")
        cur = mb.cursor_path("helper-c")
        b.run("inbox", "--as", "helper-c")
        d = json.loads(slurp(cur).decode("utf-8"))
        d.update({"journals": {"j": 105}, "mention_marks": {"codex": 9}, "ledger": {"L": {"mtime": 200.0, "hashes": ["h2"]}},
                  "reminders": {"status_at": 500.0}})
        with open(cur, "w", encoding="utf-8") as f:
            f.write(json.dumps(d))

        class A(object):
            pass
        w = M.Watcher(mb, me, A())
        w.st = {"journals": {"j": 31}, "mention_marks": {"codex": 3}, "ledger": {"L": {"mtime": 100.0, "hashes": ["h1"]}},
                "reminders": {"status_at": 100.0, "nudges": 0}, "waits_fired": {}, "journal_globs": [],
                "last_real_at": "2026-01-01T00:00:00Z", "baseline_at": "2026-01-01T00:00:00Z"}
        w.new_notified = set()
        w.persist()
        d = json.loads(slurp(cur).decode("utf-8"))
        self.assertEqual(d["journals"]["j"], 105)
        self.assertEqual(d["mention_marks"]["codex"], 9)
        self.assertEqual(d["ledger"]["L"]["hashes"], ["h2"])
        self.assertEqual(d["reminders"]["status_at"], 500.0)

    def test_log_append_concurrency(self):
        b = self.b
        code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(s);"
                "s.loader.exec_module(m);mb=m.Mailbox(sys.argv[2])\n"
                "for i in range(150): mb.log('sent', {'p': sys.argv[3], 'i': i, 'pad': 'x' * 200})")
        procs = [subprocess.Popen([sys.executable, "-I", "-B", "-c", code, SCRIPT, b.mb, str(k)]) for k in range(6)]
        for p in procs:
            p.wait(120)
        lines = slurp(os.path.join(b.mb, ".agent-mailbox", "log", "sent.jsonl")).decode("utf-8").splitlines()
        ok = [l for l in lines if l.strip() and json.loads(l).get("pad")]
        self.assertEqual(len(ok), 900)

    def test_title_recipient_checks(self):
        b = self.b
        d = os.path.join(b.dir, "t.md")
        with open(d, "wb") as f:
            f.write("# 致 helper-zz：拼错名字\n\n正文\n".encode("utf-8"))
        c, o, e = b.run("send", "--as", "helper-a", "--draft", d)
        self.assertNotEqual(c, 0)
        self.assertIn("helper-zz", e)
        with open(d, "wb") as f:
            f.write("# 致 boss、helper-c：两人\n\n正文\n".encode("utf-8"))
        c, o, e = b.run("send", "--as", "helper-a", "--draft", d, "--to", "boss")
        self.assertNotEqual(c, 0)
        self.assertIn("漏投", e)
        self.assertEqual(len(b.files("to-codex-")), 0)

    def test_status_due_uses_fresh_last_sent(self):
        b = self.b
        b.run("tasks", "set", "T1", "--as", "helper-c", "--status", "open")
        b.run("inbox", "--as", "helper-c")
        mb = b.mailbox()
        cur = mb.cursor_path("helper-c")
        d = json.loads(slurp(cur).decode("utf-8"))
        d["last_sent"] = {"at": "2026-01-01T00:00:00Z"}
        with open(cur, "w", encoding="utf-8") as f:
            f.write(json.dumps(d))

        class A(object):
            remind_status, remind_wait, nudge = 60, 0, 0
        w = M.Watcher(mb, mb.get("helper-c"), A())
        w.st = dict(d, reminders={})
        b.run("send", "--as", "helper-c", "--subject", "刚发的状态", "--body", "x")
        w.poll_reminders()
        self.assertEqual([e["kind"] for e in w.events], [])

    def test_preflight_prevents_partial_delivery(self):
        b = self.b
        b.run("roster", "policy", "--as", "boss", "--broadcast", "anyone")
        head = len("# 致 helper-a、helper-c：s\n\n".encode("utf-8")) + 400
        name = M.format_letter_name("helper-c", "0a0b0c0d", 1)
        k = (65536 - head) // len(name)
        body = os.path.join(b.dir, "big.md")
        with open(body, "wb") as f:
            f.write(("{{FILE}}" * k).encode("utf-8"))
        c, o, e = b.run("send", "--as", "boss", "--to", "helper-a,helper-c", "--subject", "s", "--body-file", body)
        self.assertNotEqual(c, 0)
        self.assertIn("超过上限", e)
        self.assertEqual(b.files("to-claude-") + b.files("to-helper-c-"), [])

    def test_digest_on_timeout(self):
        b = self.b
        p = b.popen("watch", "--as", "helper-b", "--once", "--seconds", "4", "--interval", "0.2")
        time.sleep(1.5)
        b.run("send", "--as", "boss", "--to", "helper-a", "--subject", "只给 a", "--body", "x")
        out, err = p.communicate(timeout=60)
        out = out.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 3, out)
        self.assertIn("同期写给他人的信", out)
        self.assertIn("只给 a", out)

    def test_edge_arguments(self):
        b = self.b
        r = b.jrun("send", "--as", "helper-a", "--to", "assistants", "--subject", "致其他助手", "--body", "x")
        self.assertEqual(sorted(x["to"] for x in r["sent"]), ["helper-b", "helper-c"])  # 不含自己
        c, o, e = b.run("next", "--to", "assistants")
        self.assertEqual(c, 2, e)
        self.assertNotIn("Traceback", e)
        for args in (["send", "--as", "helper-a", "--subject", "x", "--body-file", "nope.md"],
                     ["send", "--as", "helper-a", "--draft", "nope.md"],
                     ["tasks", "replace", "--as", "helper-a", "--file", "nope.json"],
                     ["status", "put", "K", "--as", "helper-a", "--file", "nope.json"],
                     ["watch", "--as", "helper-a", "--once", "--seconds", "1", "--wait-from", "nope.txt"]):
            c, o, e = b.run(*args)
            self.assertNotEqual(c, 0, args)
            self.assertIn("找不到文件", e, args)
            self.assertNotIn("Traceback", e, args)
        c, o, e = b.run("tasks", "waiting-on", "--as", "helper-a")
        self.assertEqual(c, 2)
        for n in ("helper-a", "helper-b", "helper-c"):
            b.run("roster", "remove", n, "--as", "boss")
        c, o, e = b.run("send", "--as", "boss", "--to", "assistants", "--subject", "x", "--body", "x")
        self.assertEqual(c, 2, e)
        self.assertNotIn("Traceback", e)


class InstallTest(unittest.TestCase):
    def test_install_dedupe_force_backup(self):
        home = tempfile.mkdtemp(prefix="amb-home-")
        env = dict(os.environ, USERPROFILE=home, HOME=home)
        try:
            t1 = os.path.join(home, "skills-a")
            t2 = os.path.join(home, "skills-link")
            os.makedirs(t1)
            run = lambda *a: subprocess.run([sys.executable, "-I", "-B", SCRIPT, "install"] + list(a),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            r = run("dir:" + t1)
            self.assertEqual(r.returncode, 0, r.stderr)
            inst = json.load(open(os.path.join(t1, "agent-mailbox", "INSTALL.json"), encoding="utf-8"))
            self.assertIn("SKILL.md", inst["files"])
            self.assertIn("scripts/mailbox.py", inst["files"])
            self.assertNotEqual(run("dir:" + t1).returncode, 0)
            r = run("dir:" + t1, "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.listdir(os.path.join(home, ".agent-mailbox", "backups")))
            if os.name != "nt":
                os.symlink(t1, t2)
                r = run("dir:" + t1, "dir:" + t2, "--force")
                self.assertIn("同一目录", r.stdout.decode("utf-8"))
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
