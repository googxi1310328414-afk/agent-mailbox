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
        self.assertEqual(len(tools), 14)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
        self.assertEqual(len(c.request("prompts/list")["result"]["prompts"]), 10)
        uris = [x["uri"] for x in c.request("resources/list")["result"]["resources"]]
        self.assertIn("mailbox://inbox", uris)
        self.assertEqual(len(c.request("resources/templates/list")["result"]["resourceTemplates"]), 2)
        self.assertEqual(c.request("ping")["result"], {})
        self.assertEqual(c.request("nope/nope")["error"]["code"], -32601)

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
        ch = a.notes("notifications/claude/channel")
        self.assertTrue(ch and "紧急：停测" in ch[-1]["params"]["content"])
        self.assertEqual(ch[-1]["params"]["meta"]["urgent"], "1")
        self.assertTrue(all(k.replace("_", "").isalnum() for k in ch[-1]["params"]["meta"]))
        up = [m for m in a.notes("notifications/resources/updated") if m["params"]["uri"] == "mailbox://inbox"]
        self.assertTrue(up)
        r = a.call("mailbox_wait", {"timeout_seconds": 2}, timeout=30)
        self.assertTrue(r["structuredContent"].get("timeout"))

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


if __name__ == "__main__":
    unittest.main()
