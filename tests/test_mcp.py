"""agent-mailbox MCP 服务的协议测试：以真实客户端的方式拉起服务、经 stdio 讲 JSON-RPC（只用临时目录）。
用法：python -I -B tests/test_mcp.py      （AMB_SCRIPT 可指向别处的 mailbox.py，服务取同目录的 mcp_server.py）
"""
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
SERVER = os.path.join(os.path.dirname(SCRIPT), "mcp_server.py")


def cli(mb, *args):
    p = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--mailbox", mb] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=120)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


class Client(object):
    def __init__(self, mb, me, *extra, **kw):
        self.p = subprocess.Popen([sys.executable, "-I", "-B", SERVER, "--mailbox", mb, "--as", me, "--interval", "0.2",
                                   "--coalesce", "0.3", "--progress-interval", "1"] + list(extra),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.msgs, self.bad, self.err = [], [], []
        self.cv = threading.Condition()
        self.n = 0
        threading.Thread(target=self._read, daemon=True).start()
        if kw.get("drain_stderr", True):
            threading.Thread(target=self._drain, daemon=True).start()
        else:
            self.p.stderr.close()  # 没有线程在读它，关掉读端后服务写 stderr 会失败

    def _read(self):
        for line in self.p.stdout:
            try:
                m = json.loads(line.decode("utf-8"))
            except ValueError:
                self.bad.append(line)
                continue
            if isinstance(m, list):
                m = {"__batch__": m}
            with self.cv:
                self.msgs.append(m)
                self.cv.notify_all()

    def raw(self, data):
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def _drain(self):
        for line in self.p.stderr:
            self.err.append(line.decode("utf-8", "replace"))

    def send(self, m):
        self.p.stdin.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def wait_for(self, pred, timeout=30):
        deadline = time.time() + timeout
        with self.cv:
            while True:
                for m in self.msgs:
                    if pred(m):
                        return m
                left = deadline - time.time()
                if left <= 0:
                    return None
                self.cv.wait(left)

    def request(self, method, params=None, timeout=30, rid=None):
        if rid is None:
            self.n += 1
            rid = self.n
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self.wait_for(lambda m: m.get("id") == rid and ("result" in m or "error" in m), timeout)

    def call(self, name, args=None, timeout=60, meta=None):
        params = {"name": name, "arguments": args or {}}
        if meta:
            params["_meta"] = meta
        r = self.request("tools/call", params, timeout)
        assert r is not None and "result" in r, r
        return r["result"]

    def init(self, version="2025-11-25"):
        r = self.request("initialize", {"protocolVersion": version, "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def notes(self, method):
        with self.cv:
            return [m for m in self.msgs if m.get("method") == method]

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            return self.p.wait(15)
        except subprocess.TimeoutExpired:
            self.p.kill()
            return None


def text_of(result):
    return "\n".join(c.get("text", "") for c in result.get("content", []))


class McpBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="amb-mcp-")
        self.mb = os.path.join(self.dir, "mailbox")
        os.makedirs(self.mb)
        assert cli(self.mb, "init", "--controller", "boss", "--model", "gpt-5.5", "--harness", "pi", "--thread", "0a0b0c0d",
                   "--controller-box", "codex")[0] == 0
        for n, box in (("helper-a", "claude"), ("helper-b", "claude")):
            assert cli(self.mb, "roster", "add", n, "--as", "boss", "--model", "claude", "--harness", "claude-code",
                       "--box", box)[0] == 0
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
            self.assertEqual(c.bad, [], "stdout 里出现了非 JSON-RPC 内容")
        shutil.rmtree(self.dir, ignore_errors=True)

    def client(self, me, *extra, **kw):
        c = Client(self.mb, me, *extra, **kw)
        self.clients.append(c)
        return c


class McpTest(McpBase):
    def test_legacy_handshake_and_lists(self):
        c = self.client("helper-a")
        r = c.init("2025-11-25")["result"]
        self.assertEqual(r["protocolVersion"], "2025-11-25")
        self.assertEqual(set(r["capabilities"]), {"tools", "resources", "prompts", "logging", "experimental"})
        self.assertIn("claude/channel", r["capabilities"]["experimental"])
        self.assertIn("helper-a", r["instructions"])
        self.assertEqual(c.request("initialize", {"protocolVersion": "2024-11-05"})["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(c.request("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], "2025-11-25")
        tools = c.request("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 19)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
        self.assertEqual(len(c.request("prompts/list")["result"]["prompts"]), 10)
        uris = [x["uri"] for x in c.request("resources/list")["result"]["resources"]]
        self.assertIn("mailbox://inbox", uris)
        self.assertEqual(len(c.request("resources/templates/list")["result"]["resourceTemplates"]), 4)
        self.assertEqual(c.request("ping")["result"], {})
        self.assertEqual(c.request("nope/nope")["error"]["code"], -32601)

    def test_version_flag(self):
        # --version 不要 --mailbox、--as，也不读 stdin；输出与命令行相同，握手报的也是这个版本
        p = subprocess.run([sys.executable, "-I", "-B", SERVER, "--version"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        out = p.stdout.decode("utf-8").strip()
        self.assertRegex(out, r"^agent-mailbox \d+\.\d+\.\d+$")
        want = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--version"], stdout=subprocess.PIPE,
                              timeout=60).stdout.decode("utf-8").strip()
        self.assertEqual(out, want)
        c = self.client("helper-a")
        self.assertEqual("agent-mailbox " + c.init()["result"]["serverInfo"]["version"], out)

    def test_modern_discover_and_meta(self):
        c = self.client("helper-a")
        r = c.request("server/discover")["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertIn("2026-07-28", r["supportedVersions"])
        self.assertIn("2025-11-25", r["supportedVersions"])
        self.assertEqual(r["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "agent-mailbox")
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
        r = c.request("tools/list", {"_meta": meta})["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertIn("io.modelcontextprotocol/serverInfo", r["_meta"])
        e = c.request("tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}})["error"]
        self.assertEqual(e["code"], -32022)
        self.assertEqual(e["data"]["requested"], "1900-01-01")

    def test_send_inbox_read_between_two_servers(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        r = boss.call("mailbox_send", {"to": "helper-a", "subject": "登记 T1", "body": "请先读回再开工。"})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertEqual(r["structuredContent"]["sent"][0]["box"], "claude")
        r = a.call("mailbox_inbox")
        self.assertEqual(r["structuredContent"]["unread"], 1, text_of(r))
        self.assertIn("登记 T1", text_of(r))
        r = a.call("mailbox_read", {"refs": ["claude-1"]})
        self.assertIn("请先读回再开工", text_of(r))
        self.assertEqual(a.call("mailbox_inbox")["structuredContent"]["unread"], 0)
        r = a.call("mailbox_send", {"subject": "回执 T1", "body": "已读，开工。", "reply_to": "claude-1"})
        self.assertFalse(r.get("isError"), text_of(r))
        r = boss.call("mailbox_read", {"refs": ["codex-1"]})
        self.assertIn("回复：to-claude-0a0b0c0d-001", text_of(r))

    def test_wait_progress_channel_and_subscription(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        a.request("resources/subscribe", {"uri": "mailbox://inbox"})
        time.sleep(1.0)

        def later():
            time.sleep(2.5)
            boss.call("mailbox_send", {"to": "helper-a", "subject": "紧急：停测", "body": "立即停。", "urgent": True})
        threading.Thread(target=later).start()
        r = a.call("mailbox_wait", {"timeout_seconds": 30}, timeout=60, meta={"progressToken": "w1"})
        self.assertIn("紧急：停测", text_of(r))
        self.assertIn("[急]", text_of(r))
        self.assertGreaterEqual(r["structuredContent"]["count"], 1)
        prog = [m for m in a.notes("notifications/progress") if m["params"]["progressToken"] == "w1"]
        self.assertGreaterEqual(len(prog), 1)
        r = a.call("mailbox_wait", {"timeout_seconds": 2}, timeout=30)
        self.assertTrue(r["structuredContent"].get("timeout"))
        # 没在等的时候来的信由后台监视推送：频道注入、订阅了的资源更新。
        # （正在 mailbox_wait 时信随等待结果交出，监视不再重复推送同一封）
        old = set(id(m) for m in a.msgs)  # 只看这之后收到的推送
        boss.call("mailbox_send", {"to": "helper-a", "subject": "紧急：再停", "body": "立即停。", "urgent": True})
        ch = a.wait_for(lambda m: id(m) not in old and m.get("method") == "notifications/claude/channel"
                        and "紧急：再停" in m["params"]["content"], 20)
        self.assertIsNotNone(ch)
        self.assertEqual(ch["params"]["meta"]["urgent"], "1")
        self.assertTrue(all(k.replace("_", "").isalnum() for k in ch["params"]["meta"]))
        self.assertIsNotNone(a.wait_for(lambda m: id(m) not in old and m.get("method") == "notifications/resources/updated"
                                        and m["params"]["uri"] == "mailbox://inbox", 20))

    def test_banner_on_other_tools(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        time.sleep(1.0)
        boss.call("mailbox_send", {"to": "helper-a", "subject": "顺带看到的信", "body": "x"})
        time.sleep(2.5)
        r = a.call("mailbox_whoami")
        self.assertEqual(r["structuredContent"]["identity"]["role"], "assistant")
        self.assertIn("【新事件】", text_of(r))
        self.assertIn("顺带看到的信", text_of(r))
        self.assertTrue(r["structuredContent"]["new_events"])
        self.assertNotIn("【新事件】", text_of(a.call("mailbox_whoami")))  # 只报一次

    def test_cancel_wait(self):
        a = self.client("helper-a")
        a.init()
        a.send({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                "params": {"name": "mailbox_wait", "arguments": {"timeout_seconds": 30}}})
        time.sleep(1.0)
        a.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99, "reason": "test"}})
        self.assertIsNone(a.wait_for(lambda m: m.get("id") == 99, timeout=4))
        self.assertEqual(a.request("ping")["result"], {})

    def test_tool_errors_and_read_only(self):
        a = self.client("helper-a")
        a.init()
        r = a.call("mailbox_send", {"subject": "缺正文"})
        self.assertTrue(r.get("isError"))
        r = a.call("mailbox_send", {"subject": "令牌", "body": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"})
        self.assertTrue(r.get("isError"))
        self.assertIn("凭据", text_of(r))
        self.assertEqual(a.request("tools/call", {"name": "mailbox_nope", "arguments": {}})["error"]["code"], -32602)
        ro = self.client("helper-b", "--read-only")
        ro.init()
        r = ro.call("mailbox_send", {"subject": "只读", "body": "x"})
        self.assertTrue(r.get("isError"))
        self.assertIn("只读", text_of(r))

    def test_modern_subscriptions_listen_and_graceful_close(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
        a.send({"jsonrpc": "2.0", "id": "L1", "method": "subscriptions/listen", "params": {
            "_meta": meta, "notifications": {"resourcesListChanged": True, "resourceSubscriptions": ["mailbox://inbox"]}}})
        ack = a.wait_for(lambda m: m.get("method") == "notifications/subscriptions/acknowledged", 10)
        self.assertEqual(ack["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"], "L1")
        self.assertEqual(ack["params"]["notifications"]["resourceSubscriptions"], ["mailbox://inbox"])
        time.sleep(1.0)
        boss.call("mailbox_send", {"to": "helper-a", "subject": "新协议订阅", "body": "x"})
        up = a.wait_for(lambda m: m.get("method") == "notifications/resources/updated"
                        and m["params"].get("_meta", {}).get("io.modelcontextprotocol/subscriptionId") == "L1", 15)
        self.assertIsNotNone(up)
        a.send({"jsonrpc": "2.0", "id": "L2", "method": "subscriptions/listen", "params": {
            "_meta": meta, "notifications": {"resourcesListChanged": True}}})
        a.wait_for(lambda m: m.get("method") == "notifications/subscriptions/acknowledged"
                   and m["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "L2", 10)
        a.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "L1"}})
        time.sleep(1.5)
        code = a.close()
        self.assertEqual(code, 0)
        self.assertIsNotNone(a.wait_for(lambda m: m.get("id") == "L2" and "result" in m, 2))
        self.assertIsNone(a.wait_for(lambda m: m.get("id") == "L1", 0.5))
        pres = json.load(open(os.path.join(self.mb, ".agent-mailbox", "presence", "helper-a.json"), encoding="utf-8"))
        self.assertTrue(pres.get("stopped"))

    def test_conflicting_watch_falls_back_to_polling(self):
        w = subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "--mailbox", self.mb, "--as", "helper-a", "watch",
                              "--once", "--seconds", "60", "--interval", "0.2"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(1.5)
            a, boss = self.client("helper-a"), self.client("boss")
            a.init()
            boss.init()
            time.sleep(1.5)
            self.assertEqual(a.call("mailbox_whoami")["structuredContent"]["watch"]["state"], "让位")

            def later():
                time.sleep(2.0)
                boss.call("mailbox_send", {"to": "helper-a", "subject": "轮询也能收到", "body": "x"})
            threading.Thread(target=later).start()
            r = a.call("mailbox_wait", {"timeout_seconds": 20}, timeout=60)
            self.assertIn("轮询也能收到", text_of(r))
        finally:
            w.kill()
            w.wait()

    def test_prompts_resources_and_admin_tools(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        g = a.request("prompts/get", {"name": "ack", "arguments": {"to": "boss", "reply_to": "claude-3"}})["result"]
        t = g["messages"][0]["content"]["text"]
        self.assertIn("mailbox_send", t)
        self.assertIn("to=boss", t)
        self.assertIn("已读", t)
        self.assertEqual(a.request("prompts/get", {"name": "nope"})["error"]["code"], -32602)
        r = boss.call("mailbox_send", {"to": "helper-a", "subject": "资源测试", "body": "资源正文"})
        f = r["structuredContent"]["sent"][0]["file"]
        rr = a.request("resources/read", {"uri": "mailbox://letter/" + f})["result"]
        self.assertIn("资源正文", rr["contents"][0]["text"])
        self.assertEqual(a.request("resources/read", {"uri": "mailbox://letter/../config.json"})["error"]["code"], -32002)
        self.assertIn("投递算法", a.request("resources/read", {"uri": "mailbox://doc/protocol"})["result"]["contents"][0]["text"])
        r = a.call("mailbox_tasks", {"action": "replace", "tasks": [{"id": "T1", "status": "open", "next": "等回执"}]})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertEqual(a.call("mailbox_tasks", {"action": "show"})["structuredContent"]["tasks"][0]["id"], "T1")
        a.call("mailbox_tasks", {"action": "waiting_on", "what": "主控裁定"})
        self.assertEqual(a.call("mailbox_tasks", {"action": "show"})["structuredContent"]["waiting_on"]["what"], "主控裁定")
        a.call("mailbox_notes", {"action": "append", "text": "续接要点 X"})
        self.assertIn("续接要点 X", text_of(a.call("mailbox_notes", {"action": "show"})))
        r = a.call("mailbox_status_board", {"action": "put", "key": "RUN-1", "data": {"step": 3}})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertIn('"step": 3', text_of(boss.call("mailbox_status_board", {"action": "get", "key": "RUN-1"})))
        self.assertTrue(boss.call("mailbox_roster", {"action": "add", "name": "kimi-1", "model": "kimi-k2"}).get("isError") is not True)
        self.assertTrue(a.call("mailbox_roster", {"action": "add", "name": "x9", "model": "m"}).get("isError"))
        names = [p["name"] for p in boss.call("mailbox_roster", {"action": "show"})["structuredContent"]["participants"]]
        self.assertIn("kimi-1", names)
        self.assertFalse(boss.call("mailbox_who").get("isError"))
        self.assertEqual(boss.call("mailbox_doctor")["structuredContent"]["errors"], [])
        self.assertIn("registration", text_of(a.call("mailbox_template")))
        r = a.call("mailbox_search", {"pattern": "资源", "title_only": True})
        self.assertIn("资源测试", text_of(r))
        r = a.call("mailbox_mark", {"from_num": 1, "unread": True})
        self.assertFalse(r.get("isError"), text_of(r))


class McpReviewFixesTest(McpBase):
    """MCP 独立审查发现的问题逐条回归。"""

    def test_restart_does_not_lose_mail_and_large_backlog(self):
        a = self.client("helper-a")
        a.init()
        time.sleep(0.5)
        cli(self.mb, "send", "--as", "boss", "--to", "helper-a", "--subject", "服务关着时到的信", "--body", "x")
        time.sleep(2.0)
        a.close()
        b = self.client("helper-a")
        b.init()
        r = b.call("mailbox_wait", {"timeout_seconds": 10}, timeout=40)
        self.assertIn("服务关着时到的信", text_of(r))
        for i in range(25):
            cli(self.mb, "send", "--as", "boss", "--to", "helper-a", "--subject", "积压 %02d" % i, "--body", "x")
        time.sleep(1.5)
        r = b.call("mailbox_wait", {"timeout_seconds": 10}, timeout=40)
        self.assertIn("另有 5 封未读", text_of(r))
        self.assertEqual(len(r["structuredContent"]["letters"]), 20)
        r = b.call("mailbox_wait", {"timeout_seconds": 10}, timeout=40)
        self.assertEqual(len(r["structuredContent"]["letters"]), 5)
        self.assertTrue(b.call("mailbox_wait", {"timeout_seconds": 2}, timeout=30)["structuredContent"].get("timeout"))

    def test_argument_injection_blocked(self):
        other = os.path.join(self.dir, "other")
        os.makedirs(other)
        cli(other, "init", "--controller", "x-boss", "--thread", "0a0b0c0d", "--controller-box", "codex")
        cli(other, "send", "--as", "x-boss", "--to", "x-boss", "--subject", "s", "--body", "OTHER-MAILBOX-SECRET",
            "--allow-inactive")
        cli(self.mb, "send", "--as", "boss", "--to", "helper-b", "--subject", "给 b", "--body", "x")
        a = self.client("helper-a")
        a.init()
        r = a.call("mailbox_mark", {"refs": ["--as=helper-b", "claude-1"]})
        self.assertTrue(r.get("isError"), text_of(r))
        c, o, e = cli(self.mb, "inbox", "--as", "helper-b", "--json")
        self.assertEqual(json.loads(o)["unread"], 1)
        r = a.call("mailbox_read", {"refs": ["--mailbox=" + other, "--as=user", "codex-1"]})
        self.assertTrue(r.get("isError"))
        self.assertNotIn("OTHER-MAILBOX-SECRET", text_of(r))
        cli(self.mb, "send", "--as", "boss", "--to", "helper-a", "--urgent", "--subject", "x-URGENT 标记", "--body", "x")
        self.assertIn("URGENT", text_of(a.call("mailbox_search", {"pattern": "-URGENT"})))
        self.assertFalse(a.call("mailbox_notes", {"action": "append", "text": "---"}).get("isError"))
        r = a.call("mailbox_send", {"subject": "-v2 版本", "body": "以 - 开头的主题"})
        self.assertFalse(r.get("isError"), text_of(r))

    def test_unread_again_and_read_before_delivery(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        boss.call("mailbox_send", {"to": "helper-a", "subject": "第一封", "body": "x"})
        time.sleep(1.5)
        self.assertIn("第一封", text_of(a.call("mailbox_wait", {"timeout_seconds": 10}, timeout=40)))
        r = a.call("mailbox_mark", {"refs": ["claude-1"], "unread": True})
        again = text_of(r)
        if "第一封" not in again:  # 标回未读后，可能已随 mark 结果末尾的【新事件】再次送达
            again = text_of(a.call("mailbox_wait", {"timeout_seconds": 10}, timeout=40))
        self.assertIn("第一封", again)
        boss.call("mailbox_send", {"to": "helper-a", "subject": "先读后等", "body": "x"})
        a.call("mailbox_read", {"refs": ["claude-2"]})
        time.sleep(1.5)
        r = a.call("mailbox_wait", {"timeout_seconds": 3}, timeout=30)
        self.assertNotIn("先读后等", text_of(r))

    def test_malformed_messages_keep_server_alive(self):
        a = self.client("helper-a")
        a.init()
        a.raw(b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":[1]}}\n')
        a.raw(b'{"jsonrpc":"2.0","id":201,"method":"ping","params":"x"}\n')
        a.raw(b'{"jsonrpc":"2.0","id":[1],"method":"ping"}\n')
        a.raw(b'{"jsonrpc":"2.0","id":202,"method":"tools/list","params":[1]}\n')
        a.raw(b'{"jsonrpc":"2.0","id":203,"method":"tools/list","params":{"_meta":5}}\n')
        a.raw(b"[]\n")
        a.raw(b"5\n")
        a.raw(b'{"jsonrpc":"2.0","id":204,"method":"tools/call","params":{"name":"\\ud800","arguments":{}}}\n')
        a.raw(b'{"jsonrpc":"2.0","id":205,"method":"prompts/get","params":{"name":"\\udfff"}}\n')
        for rid, code in ((201, -32602), (202, -32602), (203, -32602), (204, -32602), (205, -32602)):
            m = a.wait_for(lambda m, rid=rid: m.get("id") == rid, 10)
            self.assertIsNotNone(m, rid)
            self.assertEqual(m["error"]["code"], code, (rid, m))
        nulls = [m for m in a.msgs if m.get("id") is None and "error" in m]
        self.assertGreaterEqual(len(nulls), 3)
        self.assertEqual(a.request("ping")["result"], {})

    def test_schema_validation(self):
        cli(self.mb, "send", "--as", "boss", "--to", "helper-a", "--subject", "s", "--body", "x")
        a = self.client("helper-a")
        a.init()
        for name, args in (("mailbox_read", {"refs": "1"}), ("mailbox_notes", {"action": "append", "text": ["a"]}),
                           ("mailbox_send", {"subject": "x", "body": 123}), ("mailbox_inbox", {"bogus": True}),
                           ("mailbox_tasks", {"action": "replace", "tasks": [{"id": "T1"}]}),
                           ("mailbox_wait", {"timeout_seconds": "5"})):
            r = a.call(name, args)
            self.assertTrue(r.get("isError"), (name, text_of(r)))
            self.assertIn("参数错误", text_of(r))
        self.assertEqual(json.loads(cli(self.mb, "inbox", "--as", "helper-a", "--json")[1])["unread"], 1)

    def test_stderr_closed_still_works(self):
        boss, a = self.client("boss"), self.client("helper-a", drain_stderr=False)
        boss.init()
        a.init()
        time.sleep(1.0)
        boss.call("mailbox_send", {"to": "helper-a", "subject": "stderr 关了也能收", "body": "x"})
        self.assertIn("stderr 关了也能收", text_of(a.call("mailbox_wait", {"timeout_seconds": 15}, timeout=40)))
        boss.call("mailbox_send", {"to": "helper-a", "subject": "第二封也能收", "body": "x"})
        self.assertIn("第二封也能收", text_of(a.call("mailbox_wait", {"timeout_seconds": 15}, timeout=40)))

    def test_no_messages_before_handshake_and_batch(self):
        a = self.client("helper-a")
        time.sleep(1.5)
        cli(self.mb, "send", "--as", "boss", "--to", "helper-a", "--subject", "握手前到的信", "--body", "x")
        time.sleep(2.0)
        self.assertEqual(a.msgs, [])
        r = a.init()
        self.assertEqual(a.msgs[0].get("id"), r["id"])
        a.raw(b'[{"jsonrpc":"2.0","id":301,"method":"ping"},{"jsonrpc":"2.0","id":302,"method":"tools/list"}]\n')
        m = a.wait_for(lambda m: "__batch__" in m, 10)
        self.assertEqual(sorted(x["id"] for x in m["__batch__"]), [301, 302])

    def test_optional_positionals_on_old_argparse(self):
        a = self.client("helper-a")
        a.init()
        r = a.call("mailbox_tasks", {"action": "set", "id": "T-9", "status": "waiting", "next": "等主控"})
        self.assertFalse(r.get("isError"), text_of(r))
        d = a.call("mailbox_tasks", {"action": "show"})["structuredContent"]
        self.assertEqual([(t["id"], t["status"], t.get("next")) for t in d["tasks"]], [("T-9", "waiting", "等主控")])
        self.assertFalse(a.call("mailbox_tasks", {"action": "remove", "id": "T-9"}).get("isError"))
        self.assertEqual(a.call("mailbox_tasks", {"action": "show"})["structuredContent"]["tasks"], [])
        self.assertTrue(a.call("mailbox_tasks", {"action": "set", "id": "--as=boss"}).get("isError"))
        self.assertIn("已读", text_of(a.call("mailbox_template", {"name": "ack"})))
        self.assertTrue(a.call("mailbox_template", {"name": "--help"}).get("isError"))
        r = a.call("mailbox_status_board", {"action": "put", "key": "RUN-2", "data": {"k": 1}, "to": "boss"})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertIn('"k": 1', text_of(a.call("mailbox_status_board", {"action": "get", "key": "RUN-2"})))
        self.assertTrue(a.call("mailbox_status_board", {"action": "get", "key": "-RUN"}).get("isError"))

    def test_resources_status_json_subscribe_and_list_changed(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        boss.call("mailbox_status_board", {"action": "put", "key": "RUN-9", "data": {"step": 9}})
        txt = a.request("resources/read", {"uri": "mailbox://status/RUN-9"})["result"]["contents"][0]["text"]
        self.assertEqual(json.loads(txt)["step"], 9)
        self.assertEqual(a.request("resources/subscribe", {"uri": "mailbox://tasks"})["error"]["code"], -32602)
        time.sleep(1.0)
        boss.call("mailbox_send", {"to": "helper-a", "subject": "列表变化", "body": "x"})
        self.assertIsNotNone(a.wait_for(lambda m: m.get("method") == "notifications/resources/list_changed", 15))


class McpCollabTest(McpBase):
    """通话、队列、通知、权限、加入申请：两个真实服务进程之间的往来。"""

    def test_call_round_trip_between_servers(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        a.request("resources/subscribe", {"uri": "mailbox://talk"})
        time.sleep(1.0)
        got = {}

        def callee():
            r = a.call("mailbox_wait", {"timeout_seconds": 30}, timeout=60)
            got["wait"] = r
            conv = r["structuredContent"]["talk"][0]["conv"]
            got["say"] = a.call("mailbox_talk", {"action": "say", "conv": conv, "text": "4", "reply_to": 1})
        t = threading.Thread(target=callee)
        t.start()
        r = boss.call("mailbox_call", {"to": "helper-a", "text": "2+2=?\n只回数字", "timeout_seconds": 40}, timeout=90,
                      meta={"progressToken": "call-1"})
        t.join(60)
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertIn("helper-a（回复 #1）：4", text_of(r))
        self.assertEqual([x["text"] for x in r["structuredContent"]["replies"]], ["4"])
        self.assertFalse(r["structuredContent"]["timeout"])
        w = got["wait"]
        self.assertIn("TALK c1", text_of(w))
        self.assertIn("只回数字", text_of(w))  # 多行消息全文送到
        self.assertFalse(got["say"].get("isError"), text_of(got["say"]))
        self.assertNotIn("【新事件】", text_of(boss.call("mailbox_whoami")))  # 回复已随呼叫结果送达，不再重报
        self.assertEqual(a.call("mailbox_talk", {"action": "queue"})["structuredContent"]["queue"], [])
        r = boss.call("mailbox_talk", {"action": "read", "conv": "c1"})
        self.assertEqual(r["structuredContent"]["total"], 2)
        # 被叫方没在等的时候，新消息由后台监视推送：频道注入、订阅了的资源更新。
        # （正在 mailbox_wait 时消息随等待结果交出，监视不再重复推送同一条）
        old = set(id(m) for m in a.msgs)  # 只看这之后收到的推送
        boss.call("mailbox_talk", {"action": "say", "conv": "c1", "text": "再补一句"})
        ch = a.wait_for(lambda m: id(m) not in old and m.get("method") == "notifications/claude/channel"
                        and "再补一句" in m["params"]["content"], 15)
        self.assertIsNotNone(ch)
        self.assertIn("TALK", ch["params"]["meta"]["kinds"])
        self.assertIsNotNone(a.wait_for(lambda m: id(m) not in old and m.get("method") == "notifications/resources/updated"
                                        and m["params"]["uri"] == "mailbox://talk", 15))

    def test_call_timeout_then_late_reply_and_next(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        time.sleep(1.0)  # 等 helper-a 的后台监视登记在线
        r = boss.call("mailbox_call", {"to": "helper-a", "text": "慢慢回", "timeout_seconds": 2}, timeout=60,
                      meta={"progressToken": "p"})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertTrue(r["structuredContent"]["timeout"])
        self.assertIn("helper-a 监视在线", text_of(r))
        self.assertIn("来电队列第 1 位", text_of(r))
        n = a.call("mailbox_talk", {"action": "next", "wait_seconds": 10}, timeout=40)
        self.assertIn("慢慢回", text_of(n))
        a.call("mailbox_talk", {"action": "say", "conv": "c1", "text": "迟到的回复"})
        r = boss.call("mailbox_wait", {"timeout_seconds": 20}, timeout=60)
        self.assertIn("迟到的回复", text_of(r))
        self.assertEqual(r["structuredContent"]["talk"][0]["from"], "helper-a")
        self.assertTrue(boss.call("mailbox_wait", {"timeout_seconds": 2}, timeout=30)["structuredContent"].get("timeout"))

    def test_urgent_mail_interrupts_blocking_call(self):
        a, boss = self.client("helper-a"), self.client("boss")
        a.init()
        boss.init()
        time.sleep(1.0)
        threading.Timer(2.5, lambda: cli(self.mb, "send", "--as", "boss", "--to", "helper-a", "--urgent",
                                         "--subject", "紧急：先停下", "--body", "x")).start()
        t0 = time.time()
        r = a.call("mailbox_call", {"to": "helper-b", "text": "b 不在线也没关系", "timeout_seconds": 40}, timeout=90)
        self.assertLess(time.time() - t0, 30)
        self.assertTrue(r["structuredContent"].get("interrupted"), text_of(r))
        self.assertIn("紧急：先停下", text_of(r))  # 打断它的紧急信随结果送到

    def test_queue_take_wait_done_and_notifications(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        r = boss.call("mailbox_queue", {"action": "create", "queue": "review", "desc": "评审"})
        self.assertFalse(r.get("isError"), text_of(r))
        threading.Timer(2.0, lambda: boss.call("mailbox_queue", {"action": "push", "queue": "review", "title": "审 PR 7",
                                                                "text": "看 diff", "priority": 2})).start()
        r = a.call("mailbox_queue", {"action": "take", "queue": "review", "wait_seconds": 30}, timeout=60,
                   meta={"progressToken": "t"})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertEqual((r["structuredContent"]["title"], r["structuredContent"]["status"]), ("审 PR 7", "claimed"))
        self.assertIn("看 diff", text_of(r))
        r = a.call("mailbox_queue", {"action": "done", "queue": "review", "job": 1, "text": "LGTM，见 /tmp/r.md"})
        self.assertFalse(r.get("isError"), text_of(r))
        r = boss.call("mailbox_wait", {"timeout_seconds": 20}, timeout=60)
        kinds = [x["kind"] for x in r["structuredContent"]["notifications"]]
        self.assertIn("job-done", kinds)
        self.assertIn("LGTM", text_of(r))
        r = boss.call("mailbox_queue", {"action": "push", "queue": "review", "title": "等结果", "wait_seconds": 1}, timeout=40)
        self.assertTrue(r["structuredContent"].get("timeout"))
        r = a.call("mailbox_queue", {"action": "take", "queue": "review"})
        self.assertEqual(r["structuredContent"]["title"], "等结果")
        self.assertTrue(a.call("mailbox_queue", {"action": "take", "queue": "review"})["structuredContent"]["empty"])
        a.call("mailbox_queue", {"action": "fail", "queue": "review", "job": 2, "text": "环境挂了"})
        r = boss.call("mailbox_queue", {"action": "wait", "queue": "review", "job": 2, "wait_seconds": 5}, timeout=40)
        self.assertEqual(r["structuredContent"]["status"], "failed")
        self.assertEqual(len(boss.call("mailbox_queue", {"action": "list"})["structuredContent"]["queues"]), 1)
        self.assertTrue(a.call("mailbox_queue", {"action": "done", "queue": "review"}).get("isError"))  # 缺 job

    def test_permissions_enforced_and_pushed(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        time.sleep(1.0)
        r = boss.call("mailbox_perm", {"action": "revoke", "name": "helper-a", "perms": ["talk", "notify:boss"]})
        self.assertFalse(r.get("isError"), text_of(r))
        r = a.call("mailbox_wait", {"timeout_seconds": 15}, timeout=40)
        self.assertEqual(r["structuredContent"]["notifications"][0]["kind"], "perm")
        r = a.call("mailbox_call", {"to": "boss", "text": "能打吗", "timeout_seconds": 1})
        self.assertTrue(r.get("isError"))
        self.assertIn("权限不足", text_of(r))
        self.assertNotIn("错误：错误：", text_of(r))
        self.assertTrue(a.call("mailbox_notify", {"action": "send", "to": "boss", "text": "x"}).get("isError"))
        self.assertTrue(a.call("mailbox_perm", {"action": "grant", "name": "helper-a", "perms": ["talk"]}).get("isError"))
        d = a.call("mailbox_whoami")["structuredContent"]["permissions"]
        self.assertEqual(d["deny"], ["talk", "notify:boss"])
        c = a.call("mailbox_perm", {"action": "check", "name": "helper-a", "perm": "notify", "target": "helper-b"})
        self.assertTrue(c["structuredContent"]["allowed"])
        self.assertFalse(boss.call("mailbox_perm", {"action": "reset", "name": "helper-a"}).get("isError"))
        self.assertFalse(a.call("mailbox_notify", {"action": "send", "to": "boss", "text": "恢复了"}).get("isError"))
        self.assertIn("talk.audit", text_of(a.call("mailbox_perm", {"action": "list"})))
        r = boss.call("mailbox_perm", {"action": "preset", "role": "observer", "mode": "add", "perms": ["notify:@controller"]})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertIn("notify:@controller", r["structuredContent"]["preset"])

    def test_notify_subscription_and_resources(self):
        boss, a = self.client("boss"), self.client("helper-a")
        boss.init()
        a.init()
        a.request("resources/subscribe", {"uri": "mailbox://notifications"})
        time.sleep(1.0)
        r = boss.call("mailbox_notify", {"action": "send", "to": "assistants", "text": "八点开会", "title": "会议", "urgent": True})
        self.assertEqual(sorted(x["to"] for x in r["structuredContent"]["sent"]), ["helper-a", "helper-b"])
        up = a.wait_for(lambda m: m.get("method") == "notifications/resources/updated"
                        and m["params"]["uri"] == "mailbox://notifications", 15)
        self.assertIsNotNone(up)
        txt = a.request("resources/read", {"uri": "mailbox://notifications"})["result"]["contents"][0]["text"]
        self.assertIn("八点开会", txt)
        r = a.call("mailbox_notify", {"action": "list"})
        self.assertIn("八点开会", text_of(r))
        boss.call("mailbox_talk", {"action": "start", "to": "helper-a", "topic": "资源", "text": "资源里的通话"})
        txt = a.request("resources/read", {"uri": "mailbox://talk/c1"})["result"]["contents"][0]["text"]
        self.assertIn("资源里的通话", txt)
        for uri in ("mailbox://talk", "mailbox://queues", "mailbox://perm"):
            self.assertIsInstance(json.loads(a.request("resources/read", {"uri": uri})["result"]["contents"][0]["text"]), dict, uri)
        self.assertEqual(a.request("resources/read", {"uri": "mailbox://talk/../x"})["error"]["code"], -32002)

    def test_init_and_join_flow(self):
        fresh = os.path.join(self.dir, "fresh")
        lead = Client(fresh, "lead", "--init", "--model", "gpt-5.5", "--harness", "codex")
        self.clients.append(lead)
        r = lead.init()
        self.assertIn("lead（主控）", r["result"]["instructions"])
        newbie = Client(fresh, "kimi-9", "--join", "--model", "kimi-k2", "--note", "我能写测试")
        self.clients.append(newbie)
        r = newbie.init()
        self.assertIn("等待主控 lead 批准", r["result"]["instructions"])
        r = newbie.call("mailbox_inbox")
        self.assertTrue(r.get("isError"))
        self.assertIn("尚未加入", text_of(r))
        self.assertTrue(newbie.call("mailbox_whoami")["structuredContent"]["identity"]["pending"])
        r = lead.call("mailbox_wait", {"timeout_seconds": 20}, timeout=60)
        self.assertEqual(r["structuredContent"]["notifications"][0]["kind"], "join")
        threading.Timer(2.0, lambda: lead.call("mailbox_roster", {"action": "approve", "name": "kimi-9",
                                                                 "allow": ["broadcast"]})).start()
        r = newbie.call("mailbox_wait", {"timeout_seconds": 30}, timeout=60)
        self.assertTrue(r["structuredContent"].get("joined"), text_of(r))
        self.assertIn("kimi-9（助手）", text_of(r))
        r = newbie.call("mailbox_inbox")
        self.assertFalse(r.get("isError"), text_of(r))
        r = newbie.call("mailbox_notify", {"action": "send", "to": "all", "text": "大家好"})
        self.assertFalse(r.get("isError"), text_of(r))  # 批准时授予了广播
        self.assertIsNotNone(newbie.wait_for(lambda m: False, 1.5) or True)
        self.assertEqual(newbie.call("mailbox_whoami")["structuredContent"]["watch"]["state"], "监视中")
        reject = Client(fresh, "spam", "--join")
        self.clients.append(reject)
        reject.init()
        lead.call("mailbox_roster", {"action": "reject", "name": "spam", "reason": "不认识"})
        r = reject.call("mailbox_wait", {"timeout_seconds": 15}, timeout=40)
        self.assertTrue(r["structuredContent"].get("rejected"), text_of(r))
        self.assertIn("不认识", text_of(r))


DEFAULT_TIMING = ("--interval", "5", "--coalesce", "2", "--progress-interval", "15")


def tool_map(c):
    return dict((t["name"], t) for t in c.request("tools/list")["result"]["tools"])


def actions_of(t):
    return t["inputSchema"]["properties"]["action"]["enum"]


class McpSpeedAndGuardTest(McpBase):
    """门铃让通话即时送达；工具清单随身份和权限变化；通话安全阀与配额经 MCP 可用。"""

    def test_call_latency_with_default_intervals(self):
        # 后台监视每 5 秒才查一次；门铃让消息与回复都在一秒内送到（原来分别要等 mailbox_wait、mailbox_call 的轮询间隔）
        boss, a = self.client("boss", *DEFAULT_TIMING), self.client("helper-a", *DEFAULT_TIMING)
        boss.init()
        a.init()
        time.sleep(1.0)
        for k in range(2):
            got = {}

            def callee():
                w = a.call("mailbox_wait", {"timeout_seconds": 30}, timeout=60)
                got["t1"] = time.time()
                conv = w["structuredContent"]["talk"][0]["conv"]
                got["t2"] = time.time()
                a.call("mailbox_talk", {"action": "say", "conv": conv, "text": "收到 %d" % k})
            th = threading.Thread(target=callee)
            th.start()
            time.sleep(0.3)  # 被叫方刚进入等待：按原来的 2 秒轮询，要 1.7 秒后才查到
            t0 = time.time()
            r = boss.call("mailbox_call", {"to": "helper-a", "text": "第 %d 次" % k, "timeout_seconds": 30}, timeout=60)
            t3 = time.time()
            th.join(30)
            self.assertFalse(r.get("isError"), text_of(r))
            self.assertEqual([x["text"] for x in r["structuredContent"]["replies"]], ["收到 %d" % k])
            self.assertLess(got["t1"] - t0, 1.0, "消息送到被叫方太慢")
            self.assertLess(t3 - got["t2"], 1.5, "回复送回呼叫方太慢")

    def test_tools_follow_role_and_permissions(self):
        cli(self.mb, "roster", "add", "obs", "--as", "boss", "--role", "observer")
        boss, a, obs = self.client("boss"), self.client("helper-a", "--no-watch"), self.client("obs", "--no-watch")
        caps = boss.init()["result"]["capabilities"]
        self.assertTrue(caps["tools"]["listChanged"])
        a.init()
        obs.init()
        tb, ta, to = tool_map(boss), tool_map(a), tool_map(obs)
        self.assertEqual(len(tb), 19)
        self.assertEqual(len(ta), 19)
        self.assertIn("quota", actions_of(tb["mailbox_perm"]))
        self.assertEqual(actions_of(ta["mailbox_perm"]), ["list", "show", "check"])
        self.assertNotIn("limit", ta["mailbox_perm"]["inputSchema"]["properties"])
        self.assertEqual(actions_of(ta["mailbox_roster"]), ["show", "update"])
        self.assertNotIn("display", ta["mailbox_roster"]["inputSchema"]["properties"])
        self.assertIn("pause", actions_of(tb["mailbox_talk"]))
        self.assertNotIn("pause", actions_of(ta["mailbox_talk"]))
        self.assertNotIn("everyone", ta["mailbox_talk"]["inputSchema"]["properties"])  # 没有旁听权限
        self.assertNotIn("create", actions_of(ta["mailbox_queue"]))
        self.assertLess(len(json.dumps(list(ta.values()), ensure_ascii=False)), len(json.dumps(list(tb.values()), ensure_ascii=False)))
        # 旁听者：不能发信、不能通话，但能旁听
        self.assertNotIn("mailbox_send", to)
        self.assertNotIn("mailbox_call", to)
        self.assertEqual(actions_of(to["mailbox_notify"]), ["list"])
        self.assertNotIn("say", actions_of(to["mailbox_talk"]))
        self.assertIn("everyone", to["mailbox_talk"]["inputSchema"]["properties"])
        # 清单之外的工具照样能调：校验按完整定义，真正的判定在权限检查
        r = obs.call("mailbox_send", {"to": "boss", "subject": "s", "body": "x"})
        self.assertTrue(r.get("isError"))
        self.assertIn("权限不足", text_of(r))
        # 主控授权后，客户端收到 tools/list_changed，再拉清单就有新动作
        boss.call("mailbox_perm", {"action": "grant", "name": "helper-a", "perms": ["roster.admin"]})
        a.call("mailbox_whoami")
        self.assertIsNotNone(a.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed", 15))
        self.assertIn("approve", actions_of(tool_map(a)["mailbox_roster"]))

    def test_tools_option(self):
        core_c = self.client("helper-a", "--no-watch", "--tools", "core")
        core_c.init()
        self.assertEqual(sorted(tool_map(core_c)), sorted(["mailbox_whoami", "mailbox_wait", "mailbox_inbox", "mailbox_read",
                                                           "mailbox_send", "mailbox_call", "mailbox_talk", "mailbox_notify"]))
        pick = self.client("helper-a", "--no-watch", "--tools", "call, wait,mailbox_talk")
        pick.init()
        self.assertEqual(sorted(tool_map(pick)), ["mailbox_call", "mailbox_talk", "mailbox_wait"])
        full = self.client("helper-a", "--no-watch", "--tools", "all")
        caps = full.init()["result"]["capabilities"]
        self.assertFalse(caps["tools"]["listChanged"])
        self.assertIn("quota", actions_of(tool_map(full)["mailbox_perm"]))
        p = subprocess.run([sys.executable, "-I", "-B", SERVER, "--mailbox", self.mb, "--as", "helper-a", "--tools", "nope"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("nope", p.stderr.decode("utf-8", "replace"))
        self.assertEqual(p.stdout, b"")

    def test_pause_ends_call_and_quota_over_mcp(self):
        boss, a, b = self.client("boss"), self.client("helper-a"), self.client("helper-b")
        boss.init()
        a.init()
        b.init()
        r = boss.call("mailbox_roster", {"action": "policy", "talk_max_turns": 2, "talk_burst": "off"})
        self.assertFalse(r.get("isError"), text_of(r))
        time.sleep(1.0)
        got = {}

        def callee():
            w = b.call("mailbox_wait", {"timeout_seconds": 30}, timeout=60)
            got["say"] = b.call("mailbox_talk", {"action": "say", "conv": w["structuredContent"]["talk"][0]["conv"], "text": "二"})
        th = threading.Thread(target=callee)
        th.start()
        r = a.call("mailbox_call", {"to": "helper-b", "text": "一", "timeout_seconds": 30}, timeout=60)
        th.join(30)
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertIn("二", text_of(r))
        # 第二条到了上限：再呼叫立即报暂停，不会白等
        t0 = time.time()
        r = a.call("mailbox_call", {"conv": "c1", "text": "三", "timeout_seconds": 30}, timeout=60)
        self.assertTrue(r.get("isError"))
        self.assertIn("已暂停", text_of(r))
        self.assertLess(time.time() - t0, 10)
        n = boss.wait_for(lambda m: m.get("method") == "notifications/claude/channel" and "自动暂停" in m["params"]["content"], 15)
        self.assertIsNotNone(n)
        rd = boss.call("mailbox_talk", {"action": "read", "conv": "c1", "peek": True})
        self.assertIn("已暂停", text_of(rd))
        r = a.call("mailbox_talk", {"action": "resume", "conv": "c1"})
        self.assertTrue(r.get("isError"))  # 只有主控能继续
        r = boss.call("mailbox_talk", {"action": "resume", "conv": "c1", "max_turns": 10})
        self.assertFalse(r.get("isError"), text_of(r))
        r = a.call("mailbox_call", {"conv": "c1", "text": "三", "timeout_seconds": 1})
        self.assertFalse(r.get("isError"), text_of(r))
        # 配额
        r = boss.call("mailbox_perm", {"action": "quota", "name": "helper-a", "perm": "notify", "limit": "1/h"})
        self.assertFalse(r.get("isError"), text_of(r))
        self.assertFalse(a.call("mailbox_notify", {"action": "send", "to": "boss", "text": "1"}).get("isError"))
        r = a.call("mailbox_notify", {"action": "send", "to": "boss", "text": "2"})
        self.assertTrue(r.get("isError"))
        self.assertIn("配额用完", text_of(r))
        self.assertIn("发通知", text_of(a.call("mailbox_perm", {"action": "show"})))
        r = boss.call("mailbox_perm", {"action": "quota"})
        self.assertIn("helper-a", text_of(r))


if __name__ == "__main__":
    unittest.main()
