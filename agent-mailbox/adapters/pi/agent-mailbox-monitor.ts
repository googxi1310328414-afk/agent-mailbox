/**
 * agent-mailbox-monitor —— agent-mailbox 信箱的 Pi 扩展（可选组件）。
 *
 * 会话开始时在后台起一个 `mailbox.py watch --json` 循环进程，把它报的事件注入会话：
 * 会话空闲时直接开一轮，忙时排在当前轮次之后（followUp），不打断；一阵连续到达的事件合并成一次注入。
 * 收发、任务、备忘都调用同一个 mailbox.py，与 Claude、Codex 等其它参与方共用同一套协议和状态，不另起实现。
 *
 * 设置：~/.pi/agent/agent-mailbox.json（路径可用 AGENT_MAILBOX_PI_SETTINGS 改），环境变量 AGENT_MAILBOX_* 优先。
 * 至少要 mailbox（信箱目录）和 me（本方名）；script 默认 ~/.pi/agent/skills/agent-mailbox/scripts/mailbox.py。
 * 日志：~/.pi/agent/agent-mailbox/events.log（可用 AGENT_MAILBOX_PI_LOG_DIR 改）。
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { StringEnum } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const env = process.env;
const HOME = os.homedir();
const SETTINGS_FILE = env.AGENT_MAILBOX_PI_SETTINGS ?? path.join(HOME, ".pi", "agent", "agent-mailbox.json");
const LOG_DIR = env.AGENT_MAILBOX_PI_LOG_DIR ?? path.join(HOME, ".pi", "agent", "agent-mailbox");
const LOG_FILE = path.join(LOG_DIR, "events.log");

interface Settings {
	mailbox: string; me: string; python: string; script: string; config: string; stateDir: string; scope: string;
	interval: number; coalesceMs: number; others: string; mentions: boolean; ledger: string; markers: string[];
	journal: string[]; remindStatus: number; remindWait: number; nudge: number; takeover: boolean;
}

function loadSettings(): Settings {
	let f: any = {};
	try { f = JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf8").replace(/^\uFEFF/, "")); } catch { /* 没有设置文件时只用环境变量和默认值 */ }
	const pick = (key: string, envKey: string, dflt: any) => env[envKey] ?? f[key] ?? dflt;
	const num = (v: any, d: number) => (Number.isFinite(Number(v)) ? Number(v) : d);
	return {
		mailbox: pick("mailbox", "AGENT_MAILBOX_DIR", ""),
		me: pick("me", "AGENT_MAILBOX_ME", ""),
		python: pick("python", "AGENT_MAILBOX_PYTHON", process.platform === "win32" ? "python" : "python3"),
		script: pick("script", "AGENT_MAILBOX_SCRIPT", path.join(HOME, ".pi", "agent", "skills", "agent-mailbox", "scripts", "mailbox.py")),
		config: pick("config", "AGENT_MAILBOX_CONFIG", ""),
		stateDir: pick("stateDir", "AGENT_MAILBOX_STATE_DIR", ""),
		scope: pick("scope", "AGENT_MAILBOX_SCOPE", ""),
		interval: Math.max(0.2, num(f.interval, 5)),
		coalesceMs: Math.max(0, num(f.coalesceMs, 2000)),
		others: ["digest", "trigger", "off"].includes(f.others) ? f.others : "digest",
		mentions: Boolean(f.mentions),
		ledger: f.ledger ?? "",
		markers: Array.isArray(f.markers) ? f.markers.map(String) : [],
		journal: Array.isArray(f.journal) ? f.journal.map(String) : [],
		remindStatus: num(f.remindStatus, 75),
		remindWait: num(f.remindWait, 45),
		nudge: num(f.nudge, 15),
		takeover: Boolean(f.takeover),
	};
}

const RULES = [
	"处理规则：",
	"① 先 mailbox_read 原信或读原件，再行动；不凭摘要猜。",
	"② 只处理写给本方的事项；点名别人的不碰、不重做。",
	"③ 收到登记或返修，开工即用 mailbox_send 发 3–5 行回执（已读、执行者、独占目录、是否已写）。",
	"④ 写信只用 mailbox_send（排他编号、现取时间、读回 SHA）；信要短，只写新增事实和精确路径。",
	"⑤ 本轮结束前用 mailbox_monitor 更新 tasks 和 waiting_on，续接要点写 notes_append；只能等外部就登记 waiting_on 后直接结束本轮，不空转。",
].join("\n");

const stamp = (ms = Date.now()) => {
	const d = new Date(ms), p = (n: number) => String(n).padStart(2, "0");
	return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const norm = (p: string) => path.resolve(p).replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

function log(line: string) {
	try { fs.mkdirSync(LOG_DIR, { recursive: true }); fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${line}\n`, "utf8"); } catch { /* 日志失败不影响监视 */ }
}

function baseArgs(s: Settings): string[] {
	const a = ["-I", "-B", s.script, "--mailbox", s.mailbox, "--as", s.me];
	if (s.config) a.push("--config", s.config);
	if (s.stateDir) a.push("--state-dir", s.stateDir);
	return a;
}

// 一次性调用 mailbox.py（收发、任务、备忘）；监视循环里不调用它，只在事件投递和工具执行时用。
function cli(s: Settings, args: string[], input?: string): { code: number; out: string; err: string } {
	const r = spawnSync(s.python, [...baseArgs(s), ...args], { input, encoding: "utf8", windowsHide: true, timeout: 120000 });
	if (r.error) throw new Error(`运行 mailbox.py 失败（${s.python} ${s.script}）：${r.error.message}`);
	return { code: r.status ?? -1, out: r.stdout ?? "", err: r.stderr ?? "" };
}

function cliOk(s: Settings, args: string[], input?: string): string {
	const r = cli(s, args, input);
	if (r.code !== 0) throw new Error((r.err || r.out).trim() || `mailbox.py 退出码 ${r.code}`);
	return r.out;
}

export default function (pi: ExtensionAPI) {
	let ctx: ExtensionContext | null = null;
	let settings = loadSettings();
	let child: ChildProcess | null = null;
	let stopping = true;
	let restarts = 0;
	let restartTimer: ReturnType<typeof setTimeout> | null = null;
	let flushTimer: ReturnType<typeof setTimeout> | null = null;
	let pendingSince = 0;
	let lineBuf = "";
	let stderrTail: string[] = [];
	let lastExit: { code: number | null; at: string } | null = null;
	let state = "未启动";
	const pending: any[] = [];

	const notify = (text: string, level: "info" | "warning" | "error" = "info") => {
		try { if (ctx?.hasUI) ctx.ui.notify(text, level); } catch { /* 无界面模式 */ }
	};
	function refreshStatus() {
		try { if (ctx?.hasUI) ctx.ui.setStatus("agent-mailbox", `信箱 ${settings.me || "?"} · ${state}`); } catch { /* 状态栏失败不影响监视 */ }
	}

	function problem(c: ExtensionContext | null): string | null {
		if (!settings.mailbox || !settings.me) return `没有配置信箱目录或本方名（${SETTINGS_FILE} 或环境变量 AGENT_MAILBOX_DIR / AGENT_MAILBOX_ME）`;
		if (!fs.existsSync(settings.script)) return `找不到 mailbox.py：${settings.script}`;
		if (!fs.existsSync(settings.mailbox)) return `信箱目录不存在：${settings.mailbox}`;
		if (settings.scope && c) {
			const cwd = norm(c.cwd), root = norm(settings.scope);
			if (cwd !== root && !cwd.startsWith(root + "/")) return `当前目录不在 scope（${settings.scope}）下，不启动监视`;
		}
		return null;
	}

	function tasksSummary(): string {
		try {
			const d = JSON.parse(cliOk(settings, ["tasks", "--json"]));
			const open = (d.tasks ?? []).filter((t: any) => t.status !== "done");
			const parts: string[] = [];
			if (open.length) parts.push("当前任务：" + open.map((t: any) => `${t.id}[${t.status}]${t.next ? `→${t.next}` : ""}`).join("；"));
			if (d.waiting_on) parts.push(`等待中：${d.waiting_on.what}（自 ${d.waiting_on.since}）`);
			return parts.join("\n");
		} catch { return ""; }
	}

	function deliver(evs: any[]) {
		const real = evs.filter(e => e.kind !== "DIGEST");
		if (!real.length) return;
		const digest = evs.filter(e => e.kind === "DIGEST").map(e => e.text);
		const body = [`[agent-mailbox ${stamp()}] ${settings.me} 的新事件 ${real.length} 条`];
		for (const e of real) {
			body.push(`${e.kind} ${e.text}`);
			for (const sub of e.sub ?? []) body.push(`    ${sub}`);
		}
		if (digest.length) body.push("", "同期写给他人的信（仅供知悉，不处理）：", ...digest.map(d => `  · ${d}`));
		const t = tasksSummary();
		if (t) body.push("", t);
		body.push("", RULES);
		const message = { customType: "agent-mailbox", content: body.join("\n"), display: true, details: { kinds: real.map(e => e.kind), events: real } };
		try {
			const idle = ctx ? ctx.isIdle() && !ctx.hasPendingMessages() : true;
			if (idle) pi.sendMessage(message, { triggerTurn: true });
			else pi.sendMessage(message, { triggerTurn: true, deliverAs: "followUp" });
		} catch (e: any) { log(`DELIVER-ERROR ${e?.message}`); }
		log(`DELIVER ${real.map(e => e.kind).join(",")}`);
	}

	function flush() {
		if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
		pendingSince = 0;
		if (pending.length) deliver(pending.splice(0));
	}

	function onLine(line: string) {
		let e: any;
		try { e = JSON.parse(line); } catch { log(`BAD-LINE ${line.slice(0, 200)}`); return; }
		if (!e || typeof e.kind !== "string" || e.kind === "TIMEOUT") return;
		pending.push(e);
		if (!pendingSince) pendingSince = Date.now();
		if (flushTimer) clearTimeout(flushTimer);
		// 合并一阵连续到达的事件（例如先改总账再写信），最长等 15 秒
		flushTimer = setTimeout(flush, Math.max(0, Math.min(settings.coalesceMs, 15000 - (Date.now() - pendingSince))));
	}

	function watchArgs(s: Settings): string[] {
		const a = ["watch", "--json", "--no-rules", "--interval", String(s.interval), "--others", s.others,
			"--remind-status", String(s.remindStatus), "--remind-wait", String(s.remindWait), "--nudge", String(s.nudge)];
		if (s.mentions) a.push("--mentions");
		if (s.ledger) { a.push("--ledger", s.ledger); for (const m of s.markers) a.push("--marker", m); }
		for (const j of s.journal) a.push("--journal", j);
		if (!s.takeover) a.push("--no-takeover");
		return a;
	}

	function scheduleRestart(ms: number, why: string) {
		if (restartTimer) clearTimeout(restartTimer);
		state = `等 ${Math.round(ms / 1000)} 秒后重启（${why}）`;
		refreshStatus();
		restartTimer = setTimeout(() => { restartTimer = null; startChild(); }, ms);
		(restartTimer as any).unref?.();
	}

	function onExit(code: number | null) {
		child = null;
		lineBuf = "";
		flush();
		lastExit = { code, at: stamp() };
		log(`WATCH-EXIT code=${code} ${stderrTail.slice(-3).join(" | ")}`);
		if (stopping) { state = "已停"; refreshStatus(); return; }
		if (code === 4) { state = "被接管"; notify(`agent-mailbox：${settings.me} 的监视被另一进程接管，本会话不再自动重挂（/mailbox start 可重新开始）`, "warning"); }
		else if (code === 0) { state = "已结束"; notify("agent-mailbox：监视已结束（可能本方已不在任），/mailbox status 查看", "warning"); }
		else if (code === 2) { state = "配置错误"; notify(`agent-mailbox 无法启动：${stderrTail.slice(-2).join(" ")}`, "error"); }
		else if (code === 5) { scheduleRestart(60000, "同名监视在别处运行"); return; }
		else { restarts++; scheduleRestart(Math.min(5000 * 2 ** Math.min(restarts, 6), 300000), `退出码 ${code}`); return; }
		refreshStatus();
	}

	function startChild() {
		if (child) return;
		stopping = false;
		const p = problem(ctx);
		if (p) { state = "未启动"; notify(`agent-mailbox 未启动：${p}`, "warning"); log(`NOT-STARTED ${p}`); refreshStatus(); return; }
		stderrTail = [];
		const c = spawn(settings.python, [...baseArgs(settings), ...watchArgs(settings)], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
		child = c;
		c.stdout!.setEncoding("utf8");
		c.stderr!.setEncoding("utf8");
		c.stdout!.on("data", (d: string) => {
			lineBuf += d;
			let i: number;
			while ((i = lineBuf.indexOf("\n")) >= 0) {
				const line = lineBuf.slice(0, i).trim();
				lineBuf = lineBuf.slice(i + 1);
				if (line) onLine(line);
			}
		});
		c.stderr!.on("data", (d: string) => { stderrTail.push(...String(d).split(/\r?\n/).filter(Boolean)); stderrTail = stderrTail.slice(-20); });
		c.on("exit", code => { if (child === c) onExit(code); });
		c.on("error", (e: any) => { log(`SPAWN-ERROR ${e?.message}`); if (child === c) onExit(-1); });
		state = "监视中";
		log(`WATCH-START pid=${c.pid} me=${settings.me} mailbox=${settings.mailbox}`);
		refreshStatus();
	}

	function stopChild(reason: string) {
		stopping = true;
		if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }
		flush();
		const c = child;
		child = null;
		if (c) { try { c.kill(); } catch { /* 已退出 */ } log(`WATCH-STOP ${reason}`); }
		state = "已停";
		refreshStatus();
	}

	function statusView() {
		let who: any = null;
		try { who = JSON.parse(cliOk(settings, ["who", "--json"])); } catch (e: any) { who = { error: e?.message }; }
		return { running: Boolean(child), pid: child?.pid ?? null, state, restarts, lastExit, stderrTail: stderrTail.slice(-5),
			mailbox: settings.mailbox, me: settings.me, script: settings.script, settingsFile: SETTINGS_FILE, log: LOG_FILE, who };
	}

	pi.on("session_start", async (_event, c) => { ctx = c; settings = loadSettings(); restarts = 0; startChild(); });
	pi.on("session_shutdown", async () => { stopChild("session_shutdown"); ctx = null; });

	const text = (s: string, details: any = undefined) => ({ content: [{ type: "text" as const, text: s }], details });

	pi.registerTool({
		name: "mailbox_send",
		label: "Mailbox Send",
		description: "经 agent-mailbox 信箱投递一封信：排他编号（绝不覆盖）、信头现取时间、写后读回返回 SHA-256。助手不写 to 时默认写给主控。content 以「# 致 <收件人>：<主题>」开头时作完整草稿；否则须给 subject。信里不得出现凭据。",
		promptSnippet: "经信箱写信给主控或其他参与方（排他编号、读回 SHA）",
		parameters: Type.Object({
			to: Type.Optional(Type.String({ description: "收件人：参与方名或别名、controller、all、assistants，可逗号分隔" })),
			subject: Type.Optional(Type.String({ description: "主题（content 没有标题行时必填）" })),
			content: Type.Optional(Type.String({ description: "信件正文或完整草稿（Markdown）" })),
			draftPath: Type.Optional(Type.String({ description: "或：草稿文件的绝对路径" })),
			urgent: Type.Optional(Type.Boolean({ description: "紧急：文件名带 -URGENT" })),
			replyTo: Type.Optional(Type.String({ description: "回复哪封（941、claude-941 或文件名）" })),
			corrects: Type.Optional(Type.String({ description: "更正哪封（原件不改）" })),
			attach: Type.Optional(Type.Array(Type.String(), { description: "附件文件路径：投递时实算字节数与 SHA-256" })),
			dryRun: Type.Optional(Type.Boolean({ description: "只预览不投递" })),
		}),
		async execute(_id, p: any) {
			let file = p.draftPath as string | undefined, tmp = "";
			if (!file) {
				if (!p.content) throw new Error("需要 content 或 draftPath");
				tmp = path.join(os.tmpdir(), `agent-mailbox-draft-${process.pid}-${Date.now()}.md`);
				fs.writeFileSync(tmp, p.content, "utf8");
				file = tmp;
			}
			try {
				const titled = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "").trimStart().startsWith("#");
				const args = ["send", "--json", titled ? "--draft" : "--body-file", file];
				if (p.to) args.push("--to", p.to);
				if (p.subject) args.push("--subject", p.subject);
				if (p.urgent) args.push("--urgent");
				if (p.replyTo) args.push("--reply-to", String(p.replyTo));
				if (p.corrects) args.push("--corrects", String(p.corrects));
				for (const x of p.attach ?? []) args.push("--attach", x);
				if (p.dryRun) args.push("--dry-run");
				const out = cliOk(settings, args);
				if (p.dryRun) return text(out);
				const details = JSON.parse(out);
				log(`MAIL-SENT ${details.sent.map((r: any) => `${r.file} ${r.sha256}`).join(" ; ")}`);
				return text(out, details);
			} finally {
				if (tmp) fs.rmSync(tmp, { force: true });
			}
		},
	});

	pi.registerTool({
		name: "mailbox_inbox",
		label: "Mailbox Inbox",
		description: "列出本方在 agent-mailbox 信箱里的未读信（all=true 列全部，others=true 连同共享箱里点名给别人的）。",
		promptSnippet: "查看信箱未读",
		parameters: Type.Object({
			all: Type.Optional(Type.Boolean()),
			others: Type.Optional(Type.Boolean()),
			limit: Type.Optional(Type.Number()),
		}),
		async execute(_id, p: any) {
			const args = ["inbox", "--json"];
			if (p.all) args.push("--all");
			if (p.others) args.push("--others");
			if (p.limit) args.push("--limit", String(Math.max(1, Math.floor(p.limit))));
			const out = cliOk(settings, args);
			return text(out, JSON.parse(out));
		},
	});

	pi.registerTool({
		name: "mailbox_read",
		label: "Mailbox Read",
		description: "读 agent-mailbox 信箱里的信并标为已读。引用可写 941、claude-941、codex-338-URGENT 或完整文件名。",
		promptSnippet: "读信并标已读",
		parameters: Type.Object({
			refs: Type.Array(Type.String(), { description: "要读的信" }),
			peek: Type.Optional(Type.Boolean({ description: "只看不标已读" })),
		}),
		async execute(_id, p: any) {
			if (!Array.isArray(p.refs) || !p.refs.length) throw new Error("refs 不能为空");
			return text(cliOk(settings, ["read", ...p.refs.map(String), ...(p.peek ? ["--peek"] : [])]));
		},
	});

	pi.registerTool({
		name: "mailbox_monitor",
		label: "Mailbox Monitor",
		description: "agent-mailbox 后台监视与本方状态：status（监视进程与各方在线、未读）、start/stop/restart（监视进程）、tasks（整表替换任务清单）、waiting_on（在等谁；空串清除）、wait_add/wait_remove（等某个文件出现）、notes_append/notes_show（续接备忘）。",
		promptSnippet: "管理信箱监视、任务清单、等待对象与续接备忘",
		promptGuidelines: [
			"mailbox 事件到达后先 mailbox_read 原信再行动。每轮结束前用 mailbox_monitor 的 tasks / waiting_on 更新任务与等待对象，并用 notes_append 写续接要点。",
			"在等外部（主控回信、执行结果）时，用 mailbox_monitor 的 waiting_on 或 wait_add 登记后直接结束本轮，不要轮询空转。",
		],
		parameters: Type.Object({
			action: StringEnum(["status", "start", "stop", "restart", "tasks", "waiting_on", "wait_add", "wait_remove", "notes_append", "notes_show"] as const),
			tasks: Type.Optional(Type.Array(Type.Object({
				id: Type.String(),
				status: StringEnum(["open", "waiting", "blocked", "done"] as const),
				next: Type.Optional(Type.String()),
			}), { description: "tasks 用：整表替换" })),
			what: Type.Optional(Type.String({ description: "waiting_on 用：在等谁、等什么；空串清除" })),
			path: Type.Optional(Type.String({ description: "wait_add / wait_remove 用：文件或目录的绝对路径" })),
			note: Type.Optional(Type.String({ description: "wait_add 用：等的是什么" })),
			text: Type.Optional(Type.String({ description: "notes_append 用：要追加的续接要点" })),
		}),
		async execute(_id, p: any) {
			const a = p.action as string;
			if (a === "status") { const v = statusView(); return text(JSON.stringify(v, null, 2), v); }
			if (a === "start") { settings = loadSettings(); restarts = 0; startChild(); return text(state); }
			if (a === "stop") { stopChild("tool"); return text(state); }
			if (a === "restart") { stopChild("restart"); settings = loadSettings(); restarts = 0; startChild(); return text(state); }
			if (a === "tasks") {
				if (!Array.isArray(p.tasks)) throw new Error("tasks 需要任务数组");
				return text(cliOk(settings, ["tasks", "replace"], JSON.stringify(p.tasks)));
			}
			if (a === "waiting_on") return text(cliOk(settings, p.what ? ["tasks", "waiting-on", "--what", String(p.what)] : ["tasks", "waiting-on", "--clear"]));
			if (a === "wait_add" || a === "wait_remove") {
				if (!p.path || !path.isAbsolute(p.path)) throw new Error(`${a} 需要绝对路径 path`);
				const args = ["tasks", a === "wait_add" ? "wait-add" : "wait-remove", "--path", p.path];
				if (a === "wait_add" && p.note) args.push("--note", String(p.note));
				return text(cliOk(settings, args)); // 监视进程每轮重读登记的等待文件，不必重启
			}
			if (a === "notes_append") {
				if (!p.text) throw new Error("notes_append 需要 text");
				return text(cliOk(settings, ["notes", "append"], String(p.text)));
			}
			if (a === "notes_show") return text(cliOk(settings, ["notes", "show"]));
			throw new Error(`未知动作 ${a}`);
		},
	});

	pi.registerCommand("mailbox", {
		description: "agent-mailbox 信箱监视：status | start | stop | restart | who",
		handler: async (args, c) => {
			ctx = c;
			const cmd = (args ?? "").trim() || "status";
			if (cmd === "start") { settings = loadSettings(); restarts = 0; startChild(); c.ui.notify(`agent-mailbox：${state}`, "info"); return; }
			if (cmd === "stop") { stopChild("command"); c.ui.notify("agent-mailbox：已停", "info"); return; }
			if (cmd === "restart") { stopChild("command-restart"); settings = loadSettings(); restarts = 0; startChild(); c.ui.notify(`agent-mailbox：${state}`, "info"); return; }
			if (cmd === "who") {
				let out = "";
				try { out = cliOk(settings, ["who"]); } catch (e: any) { out = e?.message ?? String(e); }
				c.ui.notify(out.trim(), "info");
				return;
			}
			c.ui.notify(`agent-mailbox ${settings.me || "?"}｜${state}｜pid ${child?.pid ?? "-"}｜重启 ${restarts} 次｜信箱 ${settings.mailbox || "未配置"}`, "info");
		},
	});
}
