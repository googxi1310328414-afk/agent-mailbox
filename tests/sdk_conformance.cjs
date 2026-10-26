// 用官方 MCP TypeScript SDK 的客户端连 agent-mailbox MCP 服务，做一致性检查（只用临时目录）。
// SDK 会按自带 schema 校验服务的每个响应。用法：node tests/sdk_conformance.cjs
// 环境变量：MCP_SDK_DIR 指向 @modelcontextprotocol/sdk 目录；AGENT_MAILBOX_PYTHON 指定 python；AMB_SCRIPT 指向 mailbox.py。
const fs = require("fs"), os = require("os"), path = require("path"), { spawnSync } = require("child_process");

function findSdk() {
	if (process.env.MCP_SDK_DIR) return process.env.MCP_SDK_DIR;
	try {
		const entry = require.resolve("@modelcontextprotocol/sdk/client", { paths: [process.cwd(), __dirname] });
		return entry.slice(0, entry.lastIndexOf("dist"));
	} catch (e) {
		throw new Error("找不到 @modelcontextprotocol/sdk：先在某个目录 npm install @modelcontextprotocol/sdk，再用 MCP_SDK_DIR 指向它");
	}
}
const SDK = findSdk();
const { Client } = require(path.join(SDK, "dist/cjs/client/index.js"));
const { StdioClientTransport } = require(path.join(SDK, "dist/cjs/client/stdio.js"));
const types = require(path.join(SDK, "dist/cjs/types.js"));
const PY = process.env.AGENT_MAILBOX_PYTHON || (process.platform === "win32" ? "python" : "python3");
const SCRIPT = process.env.AMB_SCRIPT || path.resolve(__dirname, "../agent-mailbox/scripts/mailbox.py");
const SERVER = path.join(path.dirname(SCRIPT), "mcp_server.py");
const T = fs.mkdtempSync(path.join(os.tmpdir(), "amb-sdk-")), MB = path.join(T, "mailbox");
fs.mkdirSync(MB);

const py = (...args) => spawnSync(PY, ["-I", "-B", SCRIPT, "--mailbox", MB, ...args], { encoding: "utf8" });
let fails = 0;
const ok = (c, msg, extra) => { if (!c) fails++; console.log(c ? "通过 " : "失败 ", msg, !c && extra ? `｜${extra}` : ""); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const text = r => (r.content || []).map(c => c.text || "").join("\n");

async function connect(me) {
	const transport = new StdioClientTransport({ command: PY, args: ["-I", "-B", SERVER, "--mailbox", MB, "--as", me,
		"--interval", "0.2", "--coalesce", "0.3", "--progress-interval", "1"], stderr: "ignore" });
	const client = new Client({ name: "sdk-conformance", version: "1.0.0" }, { capabilities: {} });
	const seen = [];
	client.fallbackNotificationHandler = async n => { seen.push(n); };
	client.setNotificationHandler(types.ResourceUpdatedNotificationSchema, async n => { seen.push(n); });
	await client.connect(transport);
	return { client, seen };
}

(async () => {
	ok(py("init", "--controller", "boss", "--model", "gpt-5.5", "--harness", "pi", "--thread", "0a0b0c0d", "--controller-box", "codex").status === 0, "初始化临时信箱");
	ok(py("roster", "add", "helper-a", "--as", "boss", "--model", "claude", "--harness", "claude-code", "--box", "claude").status === 0, "加入助手");
	const boss = await connect("boss"), a = await connect("helper-a");
	const ver = a.client.getServerVersion(), caps = a.client.getServerCapabilities();
	ok(ver && ver.name === "agent-mailbox", "握手成功并取得服务信息", JSON.stringify(ver));
	ok(caps && caps.tools && caps.resources && caps.resources.subscribe && caps.prompts && caps.logging, "能力声明齐全", JSON.stringify(caps));
	ok(/helper-a/.test(a.client.getInstructions() || ""), "服务说明里带本方身份");
	const tools = (await a.client.listTools()).tools;
	ok(tools.length === 14 && tools.every(t => t.inputSchema && t.inputSchema.type === "object"), "listTools 通过 SDK 校验，14 个工具");
	ok((await a.client.listPrompts()).prompts.length === 10, "listPrompts 通过校验");
	const res = (await a.client.listResources()).resources;
	ok(res.some(r => r.uri === "mailbox://inbox"), "listResources 通过校验");
	ok((await a.client.listResourceTemplates()).resourceTemplates.length === 2, "listResourceTemplates 通过校验");
	const pr = await a.client.getPrompt({ name: "registration", arguments: { to: "helper-a" } });
	ok(/mailbox_send/.test(pr.messages[0].content.text), "getPrompt 通过校验");
	await a.client.subscribeResource({ uri: "mailbox://inbox" });
	await a.client.setLoggingLevel("info");

	let r = await boss.client.callTool({ name: "mailbox_send", arguments: { to: "helper-a", subject: "SDK 登记", body: "请先读回。" } });
	ok(!r.isError && r.structuredContent && r.structuredContent.sent[0].readbackEqual, "callTool 发信，结构化结果通过校验", text(r));
	r = await a.client.callTool({ name: "mailbox_inbox", arguments: {} });
	ok(!r.isError && r.structuredContent.unread === 1, "收件箱看到 1 封未读", text(r));
	const rr = await a.client.readResource({ uri: "mailbox://letter/" + r.structuredContent.letters[0].file });
	ok(/请先读回/.test(rr.contents[0].text), "readResource 读到信件原文");

	// 上一封信的来信事件可能稍后才进待取队列，会先被 mailbox_wait 取到；循环等到目标信为止
	let progress = 0, got = "";
	setTimeout(() => boss.client.callTool({ name: "mailbox_send", arguments: { to: "helper-a", subject: "SDK 等待中来信", body: "x" } }), 2500);
	for (let i = 0; i < 5 && !/SDK 等待中来信/.test(got); i++) {
		r = await a.client.callTool({ name: "mailbox_wait", arguments: { timeout_seconds: 30 } }, undefined,
			{ onprogress: () => { progress++; }, timeout: 4000, resetTimeoutOnProgress: true });
		got = text(r);
	}
	ok(/SDK 等待中来信/.test(got), "mailbox_wait 在来信时返回", got);
	ok(progress >= 1, "等待期间收到进度通知，且按进度续命没有超时", "progress=" + progress);
	await sleep(500);
	ok(a.seen.some(n => n.method === "notifications/claude/channel" && /SDK 等待中来信/.test(n.params.content)), "收到频道推送");
	ok(a.seen.some(n => n.method === "notifications/resources/updated" && n.params.uri === "mailbox://inbox"), "收到资源更新通知");
	ok(a.seen.some(n => n.method === "notifications/message"), "收到日志通知");
	r = await a.client.callTool({ name: "mailbox_send", arguments: { subject: "缺正文" } });
	ok(r.isError === true, "工具错误以 isError 返回");

	await a.client.close();
	await boss.client.close();
	console.log(fails ? `${fails} 项失败` : "全部通过", "| SDK", require(path.join(SDK, "package.json")).version);
	fs.rmSync(T, { recursive: true, force: true });
	process.exitCode = fails ? 1 : 0;
})().catch(e => { console.error("SDK-CONFORMANCE-ERROR", e); process.exitCode = 1; });
