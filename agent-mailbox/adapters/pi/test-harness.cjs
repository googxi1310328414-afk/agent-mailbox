// agent-mailbox-monitor 的离线测试：只用临时目录，不碰真实信箱和正在运行的 Pi。
// 用法：node test-harness.cjs   （可用 PI_CODING_AGENT_DIR 指定 pi-coding-agent 安装目录，AGENT_MAILBOX_PYTHON 指定 python）
const fs = require("fs"), os = require("os"), path = require("path"), { spawnSync } = require("child_process");

function findPi() {
	if (process.env.PI_CODING_AGENT_DIR) return process.env.PI_CODING_AGENT_DIR;
	const r = spawnSync("npm", ["root", "-g"], { encoding: "utf8", shell: process.platform === "win32" });
	const root = (r.stdout || "").trim();
	if (!root) throw new Error("找不到 npm 全局目录；请用 PI_CODING_AGENT_DIR 指定 pi-coding-agent 的安装目录");
	return path.join(root, "@earendil-works", "pi-coding-agent").replace(/\\/g, "/");
}
const PI = findPi();
const NM = PI + "/node_modules";
const PY = process.env.AGENT_MAILBOX_PYTHON || (process.platform === "win32" ? "python" : "python3");
const SCRIPT = path.resolve(__dirname, "../../scripts/mailbox.py");
const EXT = path.resolve(__dirname, "agent-mailbox-monitor.ts");
const T = fs.mkdtempSync(path.join(os.tmpdir(), "agent-mailbox-pi-test-"));
const MB = path.join(T, "mailbox");
fs.mkdirSync(MB);

function py(args, input) {
	const r = spawnSync(PY, ["-I", "-B", SCRIPT, "--mailbox", MB, ...args], { input, encoding: "utf8", windowsHide: true });
	return { code: r.status, out: r.stdout || "", err: r.stderr || "" };
}

let fails = 0;
const ok = (c, msg, extra) => { if (!c) fails++; console.log(c ? "通过 " : "失败 ", msg, !c && extra ? `｜${extra}` : ""); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitFor(fn, ms = 8000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (fn()) return true; await sleep(100); } return fn(); }

ok(py(["init", "--controller", "pi-main", "--model", "gpt-5.5", "--harness", "pi", "--thread", "0b0c0d0e", "--controller-box", "codex"]).code === 0, "初始化信箱（Pi 为主控）");
for (const n of ["claude-a", "claude-b"]) ok(py(["roster", "add", n, "--as", "pi-main", "--model", "claude-opus-5-5", "--harness", "claude-code", "--box", "claude"]).code === 0, `加入助手 ${n}`);

fs.writeFileSync(path.join(T, "settings.json"), JSON.stringify({ interval: 0.3, coalesceMs: 300, nudge: 0, remindStatus: 0, remindWait: 0 }));
Object.assign(process.env, {
	AGENT_MAILBOX_PI_SETTINGS: path.join(T, "settings.json"), AGENT_MAILBOX_PI_LOG_DIR: path.join(T, "log"),
	AGENT_MAILBOX_DIR: MB, AGENT_MAILBOX_ME: "pi-main", AGENT_MAILBOX_SCRIPT: SCRIPT, AGENT_MAILBOX_PYTHON: PY,
});

const { createJiti } = require(NM + "/jiti");
const jiti = createJiti(PI + "/package.json", { alias: {
	"@earendil-works/pi-coding-agent": PI + "/dist/index.js",
	"@earendil-works/pi-ai": NM + "/@earendil-works/pi-ai/dist/compat.js",
	"typebox/compile": NM + "/typebox/build/compile/index.mjs",
	"typebox/value": NM + "/typebox/build/value/index.mjs",
	"typebox": NM + "/typebox/build/index.mjs",
} });

const sent = [], handlers = {}, tools = {}, cmds = {}, notes = [];
let idle = true;
const pi = { on: (e, h) => (handlers[e] = h), registerTool: t => (tools[t.name] = t), registerCommand: (n, c) => (cmds[n] = c), sendMessage: (m, o) => sent.push({ m, o }) };
const ctx = { cwd: T, hasUI: true, isIdle: () => idle, hasPendingMessages: () => false,
	ui: { notify: (t, l) => notes.push([l, t]), setStatus: () => {} }, sessionManager: { getSessionId: () => "test-session" } };
const since = n => sent.slice(n);
const call = async (name, params) => tools[name].execute("t", params);

(async () => {
	const mod = jiti(EXT); (mod.default || mod)(pi);
	ok(tools.mailbox_send && tools.mailbox_inbox && tools.mailbox_read && tools.mailbox_monitor && cmds.mailbox, "注册四个工具和 /mailbox 命令");

	await handlers.session_start({ type: "session_start" }, ctx);
	await sleep(1500);
	ok(sent.length === 0, "启动后没有历史事件");

	let n = sent.length;
	py(["send", "--as", "claude-a", "--subject", "回执 T0", "--body", "已读，开工。"]);
	ok(await waitFor(() => since(n).length >= 1), "助手来信被注入会话");
	let m = since(n)[0];
	ok(m && /MAIL .*codex-1/.test(m.m.content) && /回执 T0/.test(m.m.content) && m.o.triggerTurn && !m.o.deliverAs, "空闲时直接开一轮，内容含信件摘要", m && JSON.stringify(m.o));

	idle = false; n = sent.length;
	py(["send", "--as", "claude-b", "--urgent", "--subject", "紧急：停测", "--body", "立即停。"]);
	ok(await waitFor(() => since(n).length >= 1), "第二封来信被注入");
	m = since(n)[0];
	ok(m && m.o.deliverAs === "followUp" && /\[急\]/.test(m.m.content), "会话忙时排在当前轮次之后，紧急信带标记", m && JSON.stringify(m.o));
	idle = true;

	let r = await call("mailbox_send", { to: "claude-a", subject: "登记 T1", content: "请先读回再开工。" });
	ok(r.details && /^to-claude-0b0c0d0e-\d{3}\.md$/.test(r.details.sent[0].file) && r.details.sent[0].readbackEqual, "mailbox_send 投进共享箱并读回一致", r.content && r.content[0].text);
	r = await call("mailbox_send", { content: "# 致 claude-b：带标题的草稿\n\n正文。", replyTo: "codex-2" });
	ok(r.details && r.details.sent[0].to === "claude-b", "草稿自带标题时按标题投递，回复引用可用");
	let threw = false;
	try { await call("mailbox_send", { to: "claude-a", subject: "令牌", content: "Bearer abcdefghijklmnopqrstuvwxyz0123456789" }); } catch (e) { threw = /凭据/.test(String(e.message)); }
	ok(threw, "疑似凭据被拒并把原因报给模型");
	const ia = JSON.parse(py(["inbox", "--as", "claude-a", "--json"]).out);
	ok(ia.unread === 1 && /登记 T1/.test(ia.letters[0].title), "claude-a 只收到点名给自己的那封");

	r = await call("mailbox_inbox", {});
	ok(r.details && r.details.unread === 2, "mailbox_inbox 列出两封未读", r.content[0].text.slice(0, 200));
	r = await call("mailbox_read", { refs: ["codex-1"] });
	ok(/已读，开工/.test(r.content[0].text), "mailbox_read 读出原信");
	r = await call("mailbox_inbox", {});
	ok(r.details.unread === 1, "读过的信不再算未读");

	await call("mailbox_monitor", { action: "tasks", tasks: [{ id: "T1", status: "open", next: "等 claude-a 回执" }] });
	await call("mailbox_monitor", { action: "waiting_on", what: "claude-a 的开工回执" });
	const tj = JSON.parse(py(["tasks", "--as", "pi-main", "--json"]).out);
	ok(tj.tasks[0].id === "T1" && tj.waiting_on.what === "claude-a 的开工回执", "任务清单与等待对象写进共享状态");

	n = sent.length;
	const resultFile = path.join(T, "RESULT.json");
	await call("mailbox_monitor", { action: "wait_add", path: resultFile, note: "执行结果" });
	await sleep(800);
	fs.writeFileSync(resultFile, '{"exit": 0}');
	ok(await waitFor(() => since(n).some(x => /WAITFILE/.test(x.m.content) && /"exit": 0/.test(x.m.content))), "登记的等待文件出现即唤醒，并带上内容");
	m = since(n).find(x => /WAITFILE/.test(x.m.content));
	ok(m && /当前任务：T1\[open\]/.test(m.m.content) && /等待中：claude-a 的开工回执/.test(m.m.content), "注入内容附带任务清单与等待对象");

	await call("mailbox_monitor", { action: "notes_append", text: "已派 T1；等回执" });
	r = await call("mailbox_monitor", { action: "notes_show" });
	ok(/已派 T1；等回执/.test(r.content[0].text), "续接备忘可追加和查看");

	r = await call("mailbox_monitor", { action: "status" });
	ok(r.details.running && r.details.who.some(w => w.name === "pi-main" && w.online), "status 显示监视在跑、本方在线");

	const ext = spawnSync(PY, ["-I", "-B", SCRIPT, "--mailbox", MB, "--as", "pi-main", "watch", "--once", "--seconds", "2", "--interval", "0.3"], { encoding: "utf8" });
	ok(ext.status === 3, "外部同名监视接管后超时退出", ext.stdout + ext.stderr);
	ok(await waitFor(() => notes.some(([l, t]) => /被另一进程接管/.test(t))), "扩展发现被接管后提示且不自动抢回");
	r = await call("mailbox_monitor", { action: "status" });
	ok(!r.details.running && r.details.state === "被接管", "被接管后监视进程已退出");
	await call("mailbox_monitor", { action: "start" });
	await sleep(1500);
	n = sent.length;
	py(["send", "--as", "claude-a", "--subject", "重启后", "--body", "x"]);
	ok(await waitFor(() => since(n).length >= 1), "start 后恢复收信");

	py(["roster", "update", "claude-b", "--as", "pi-main", "--model", "kimi-k2"]);
	await handlers.session_shutdown({});
	await sleep(500);
	n = sent.length;
	py(["send", "--as", "claude-a", "--subject", "关后来信", "--body", "x"]);
	await sleep(1500);
	ok(since(n).length === 0, "会话结束后不再注入");

	process.env.AGENT_MAILBOX_SCRIPT = path.join(T, "missing.py");
	notes.length = 0;
	await handlers.session_start({}, ctx);
	ok(notes.some(([l, t]) => /找不到 mailbox.py/.test(t)), "脚本缺失时只提示、不启动");
	await handlers.session_shutdown({});
	process.env.AGENT_MAILBOX_SCRIPT = SCRIPT;

	await handlers.session_start({ reason: "resume" }, ctx);
	ok(await waitFor(() => sent.slice(n).some(x => /关后来信/.test(x.m.content))), "重新开会话后补报停机期间的来信");
	await handlers.session_shutdown({});

	const logText = fs.readFileSync(path.join(T, "log", "events.log"), "utf8");
	console.log(fails ? `${fails} 项失败` : "全部通过", "| events.log 行数", logText.trim().split("\n").length, "| 临时目录", T);
	process.exitCode = fails ? 1 : 0;
})().catch(e => { console.error("HARNESS-ERROR", e); process.exitCode = 1; });
