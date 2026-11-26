# agent-mailbox

独立的多模型协作 MCP 服务：**通知**、**队列等待**、**多模型自由通话**、**主控分配权限**，外加不会覆盖的信件。
一名**主控**（任何时刻唯一）带若干**助手**（数量可增减）。所有参与方共用一个目录，不需要常驻的中心进程，也不依赖数据库或任何第三方包。
只依赖 Python 3.8+ 标准库，Claude Code、Codex、Pi、Cursor、Kimi、Gemini CLI 等客户端，以及 Claude、GPT、Kimi、GLM、Grok 等模型都能接入。
同一套能力也能当 Agent Skill 或命令行用。

> **English.** A standalone MCP server for multi-agent collaboration: push notifications, waiting queues (a per-agent call queue and work queues with leases), free-form conversations between models (`mailbox_call` blocks until the other model answers), and permissions the controller assigns per participant, plus append-only, never-overwritten letters. One controller, any number of assistants; new models can ask to join and wait for approval. Zero dependencies (Python 3.8+ standard library), stdio transport, compatible with both the `initialize` handshake and the stateless 2026-07-28 protocol, verified against the official MCP TypeScript SDK. Docs are in Chinese.

## 能做什么

- **通知**：
  - 每个参与方一条持久的通知流。`mailbox_notify` 发轻量提醒，不占信件编号。
  - 派活、任务结果、权限变更、加入申请也会自动发通知。
  - 推送渠道有四条，客户端支持哪条就用哪条：Claude Code 频道注入、资源更新通知（可订阅 `mailbox://notifications`）、`mailbox_wait` 立即返回、附在其它工具结果末尾。
  - 只有回应确实发出才记为已送达；服务重启也不丢。
- **队列等待**：
  - **来电队列**：别人打来的通话按「紧急优先、先来后到」排队，`mailbox_talk next` 逐个接；呼叫方能看到自己排在第几位。
  - **工作队列**：`mailbox_queue` 投任务，可设优先级、指派给某人。领取方用 `take` 排队等活；投放方用 `wait` 等结果。
  - 领到的任务有租约，到期没完成就放回排队，用完尝试次数就判失败并通知投放者。几个模型同时领，也绝不会领到同一个。
  - 申请加入的新模型也在队列里等主控批准。
- **多模型自由通话**：
  - 一对一或多方通话，任何模型之间都能直接对话。
  - `mailbox_call` 发出后同步等对方回复，等待期间发进度通知；有紧急来信时提前返回。
  - 回复连同全文送达，能邀请、退出、结束；主控与旁听者可以旁听。
- **主控分配权限**：
  - 权限有发信、广播、通知、通话、旁听、队列投放、领取、管理、状态板、代管花名册。
  - 角色有默认预设，由原来的通信策略推出，行为与 1.0 一致。
  - 主控可以按人授予或收回，范围可以细到某个人（`talk:kimi-1`）、某个角色组（`send:@assistants`）或某个队列（`queue.take:review`）。
  - 改动即时生效，本人会收到通知。
  - 可选的身份令牌防止用 `--as` 冒充主控来绕开权限。
- **独立运行**：
  - 主控用 `--init` 直接建好信箱。新模型用 `--join` 申请加入，主控审批时可以顺带授权。
  - 全程不用离开 MCP。
- **信件**（1.0 的全部能力保留）：
  - 编号锁加排他创建，绝不覆盖；写后读回核 SHA-256；信头时间现取。
  - 拦截损坏字和凭据。
  - 支持广播、回复、更正、附件实算 SHA；主控交接时继承已读；任务清单、续接备忘、状态板。

## 快速开始（只用 MCP）

1. 下载 [Releases](../../releases) 里的 `agent-mailbox-<版本>.zip` 并解压，下文 `<skill>` 指解出的 `agent-mailbox/` 目录。也可以直接克隆本仓库。
2. 主控接上 MCP，信箱不存在时 `--init` 会以它为主控建好。以 Claude Code 为例：

   ```bash
   claude mcp add agent-mailbox -- python -I -B <skill>/scripts/mcp_server.py --mailbox /path/to/mailbox --as lead --init --model gpt-5.5
   ```

3. 其它模型用自己的名字接入，并加 `--join` 申请加入：

   ```bash
   claude mcp add agent-mailbox -- python -I -B <skill>/scripts/mcp_server.py --mailbox /path/to/mailbox --as kimi-1 --join --model kimi-k2
   ```

   - 主控会收到 `NOTIFY [join]`，用 `mailbox_roster` 的 `approve` 批准，可以顺带 `allow` / `deny` 授权。
   - 新模型的 `mailbox_wait` 随即返回「已获批准」。
   - 也可以由主控先 `mailbox_roster add` 直接登记，这时不用 `--join`。
   - Codex、Pi、Cursor 等客户端的写法见 [`references/mcp.md`](agent-mailbox/references/mcp.md)。
4. 开始协作：

   | 想做的事 | 工具 |
   |---|---|
   | 问另一个模型并等它回答 | `mailbox_call`，参数 `{"to": "kimi-1", "text": "…"}` |
   | 接下一个来电、回话 | `mailbox_talk`：先 `next`，再 `say` |
   | 派一件谁都能做的活 | `mailbox_queue`：主控 `push`，助手 `take` 后 `done` |
   | 发个提醒 | `mailbox_notify send` |
   | 正式登记、回执、交付 | `mailbox_send`（信件） |
   | 等任何新动静 | `mailbox_wait` |
   | 授权或收回 | `mailbox_perm`：`grant` / `revoke`（只有主控） |

不接 MCP 也行，同样的能力都有命令行：

```bash
python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox init --controller lead --as user
python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox --as lead roster add kimi-1 --model kimi-k2
python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox --as kimi-1 talk call lead --text "T-3 要不要连带改接口？" --wait 120
python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox --as lead perm revoke kimi-1 broadcast
```

## 工具一览（19 个）

- **信件**：`mailbox_send`、`mailbox_inbox`、`mailbox_read`、`mailbox_mark`、`mailbox_search`。
- **通话**：`mailbox_call`（呼叫并等回复）、`mailbox_talk`（发起、发言、读、来电队列、接下一个、邀请、退出、结束）。
- **通知**：`mailbox_notify`。
- **队列**：`mailbox_queue`（建、投、领、完成、失败、放回、续租、取消、查看、等结果）。
- **权限**：`mailbox_perm`（目录、查看、判定、授予、收回、重置、角色预设）。
- **等待**：`mailbox_wait`。
- **身份与花名册**：`mailbox_whoami`、`mailbox_roster`（含加入申请的审批）、`mailbox_who`。
- **续接**：`mailbox_tasks`、`mailbox_notes`、`mailbox_status_board`。
- **其它**：`mailbox_doctor`、`mailbox_template`。

另有资源（`mailbox://inbox`、`mailbox://notifications`、`mailbox://talk` 可订阅）和 10 个信件模板提示词。

## 文档

- [`references/mcp.md`](agent-mailbox/references/mcp.md)：启动参数、各客户端配置、全部工具、资源与提示词、推送与送达、阻塞等待。
- [`SKILL.md`](agent-mailbox/SKILL.md)：给模型看的用法总览。
- [`references/protocol.md`](agent-mailbox/references/protocol.md)：目录、命名、编号、信头、路由、投递算法、通话与队列格式、权限判定、不变量。
- [`references/roles.md`](agent-mailbox/references/roles.md)：主控与助手的职责、写信与通话纪律。
- [`references/commands.md`](agent-mailbox/references/commands.md)：全部命令和参数。
- [`references/harnesses.md`](agent-mailbox/references/harnesses.md)：各客户端的等信方式。

## 测试与打包

```bash
python -I -B agent-mailbox/scripts/mailbox.py selftest   # 临时目录里走全流程（含权限、通话、队列、加入申请）
python -I -B tests/test_mailbox.py                       # 单元、并发与回归测试
python -I -B tests/test_mcp.py                           # MCP 协议测试（真实子进程，经 stdio 通信）
MCP_SDK_DIR=<目录>/node_modules/@modelcontextprotocol/sdk node tests/sdk_conformance.cjs   # 用官方 MCP SDK 客户端做一致性检查
python -I -B tools/package.py                            # 打包到 dist/，同一份源码每次打出的包逐字节相同
```

GitHub Actions 会在 Linux、Windows、macOS 上跑以上测试。

## 许可

MIT，完整条款见 [LICENSE](LICENSE)。
