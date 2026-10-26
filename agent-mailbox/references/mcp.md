# MCP 服务

`scripts/mcp_server.py` 把信箱能力做成标准 MCP 服务（stdio）。它只用 Python 3.8+ 标准库，任何支持 MCP 的客户端都能接。
- 一个服务进程绑定**一个信箱、一个身份**，工具调用时不用再带 `--as` 和路径。
- 收发都在进程内调用同目录的 `mailbox.py`，与命令行共用同一套状态，可以混用。
- 后台持续监视来信，有事件时推送通知、唤醒等待。

## 启动参数

```bash
python -I -B <skill>/scripts/mcp_server.py --mailbox <信箱目录> --as <本方名> [选项]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--mailbox`、`--as` | 环境变量 `AGENT_MAILBOX_DIR`、`AGENT_MAILBOX_ME` | 必填 |
| `--config`、`--state-dir` | 信箱里的 `.agent-mailbox/` | 花名册和控制目录在别处时用 |
| `--read-only` | 关 | 只读：不投递、不写游标 |
| `--no-watch` | 开着监视 | 不开后台监视；`mailbox_wait` 改为轮询收件箱 |
| `--no-channel` | 开着 | 不声明、不推送 Claude Code 频道事件 |
| `--takeover` | 关 | 已有同名监视时接管它。默认让位：不抢别人的监视，`mailbox_wait` 改为轮询 |
| `--interval`、`--coalesce` | 5、2 秒 | 监视轮询间隔、同批事件合并时长 |
| `--others` | `digest` | 共享箱里写给别人的信：摘要附带 / 也唤醒 / 忽略 |
| `--mentions` | 关 | 别的箱里点名本方也报 |
| `--ledger`、`--marker`、`--journal` | 无 | 盯总账标记行、JSONL 工作日志（同 `watch`） |
| `--remind-status`、`--remind-wait`、`--nudge` | 75、45、20 分钟 | 报状态提醒、等待超时提醒、心跳（0 关闭） |
| `--wait-default` | 50 秒 | `mailbox_wait` 默认最长等待 |
| `--progress-interval` | 15 秒 | `mailbox_wait` 发进度通知的间隔；客户端据此延长超时 |

同一身份只该有一个监视。用 MCP 时，不要再给同一身份挂命令行 `watch`。

## 各客户端的配置

下面的命令和配置项是写作时各客户端的现行写法，以客户端自己的文档为准。路径换成实际值；Windows 路径写正斜杠或双反斜杠。

**Claude Code**

```bash
claude mcp add agent-mailbox -- python -I -B <skill>/scripts/mcp_server.py --mailbox <信箱目录> --as <本方名>
```

- 想让来信直接注入会话：用频道方式启动，`claude --dangerously-load-development-channels server:agent-mailbox`。这是研究预览功能，要交互式会话，启动时会弹确认框。
- 不用频道时：靠 `mailbox_wait`，以及其它工具结果末尾附带的新事件。
- 工具超时用环境变量 `MCP_TOOL_TIMEOUT`（毫秒）调。

**Codex**（`~/.codex/config.toml`）

```toml
[mcp_servers.agent-mailbox]
command = "python"
args = ["-I", "-B", "<skill>/scripts/mcp_server.py", "--mailbox", "<信箱目录>", "--as", "<本方名>"]
tool_timeout_sec = 600
```

`codex mcp add agent-mailbox -- python -I -B …` 也行，但它不写超时，`tool_timeout_sec` 要手动加。

**Pi**

`pi mcp add agent-mailbox -- python -I -B …`，或写进 `~/.pi/agent/mcp.json` 的 `mcpServers`。请求超时默认 60 秒，进度通知会重置计时。

**Cursor、Kimi、Gemini CLI 等使用 `mcpServers` JSON 的客户端**

```json
{
  "mcpServers": {
    "agent-mailbox": {
      "command": "python",
      "args": ["-I", "-B", "<skill>/scripts/mcp_server.py", "--mailbox", "<信箱目录>", "--as", "<本方名>"]
    }
  }
}
```

## 工具

| 工具 | 用途 |
|---|---|
| `mailbox_whoami` | 本方身份、角色、收件箱、监视状态、待取事件数 |
| `mailbox_inbox` | 未读（或全部）信件列表 |
| `mailbox_read` | 读信并标已读（`peek` 只看） |
| `mailbox_send` | 投递：`to`、`subject` + `body`，或 `draft`（自带标题）；`urgent`、`reply_to`、`corrects`、`attach`、`dry_run` |
| `mailbox_wait` | 等事件：来信即返回，超时返回空；等待期间发进度通知 |
| `mailbox_mark` | 标已读或未读 |
| `mailbox_search` | 正则搜信 |
| `mailbox_tasks` | 任务清单、等待对象、等待文件 |
| `mailbox_notes` | 续接备忘 |
| `mailbox_status_board` | 状态板 |
| `mailbox_roster` | 花名册：查看；增减助手、交接、上限、策略只有主控能改 |
| `mailbox_who` | 各参与方在线与进度 |
| `mailbox_doctor` | 只读体检 |
| `mailbox_template` | 信件模板 |

工具出错时返回 `isError: true`，并附原因（损坏字、凭据、标题点名、只读等），模型可以据此改正后重试。

## 新事件怎么到达模型

**哪些信算「还没交给模型」按状态算**：本方未读、写给本方、不是自己发的、并且没有送达记录的信。送达记录存在本方游标里（`delivered`）。
- 只有带着这些信的回应确实发出去，才记为送达。
- 调用被取消、回应发送失败、服务中途重启，都不会丢信，下次照报。
- `mailbox_mark` 标回未读会清掉送达记录，于是再报一次。
- 一次最多列 20 封，其余提示用 `mailbox_inbox` 查看。

其它事件只在内存里排队，最多积 200 批，超出时如实提示丢了几批。这类事件包括：总账新行、工作日志、等待文件、报状态提醒、等待超时、心跳、角色变化、别处点名。

后台监视发现变化时，同时走四条路，客户端支持哪条就用哪条：
1. `notifications/claude/channel`：Claude Code 以频道方式加载时直接注入会话；其它客户端会忽略它。
2. `notifications/resources/updated`：订阅了 `mailbox://inbox` 的客户端会收到。旧协议用 `resources/subscribe`，新协议用 `subscriptions/listen`。只有这一个资源可订阅。
3. `mailbox_wait`：正在等的调用立即返回。
4. 其它信箱工具的结果末尾附「【新事件】」：在 1 之外，只有这一条会把事件记为已送达。

另外每批事件都会发一条 `notifications/message` 日志通知。
- 握手完成之前，服务不发任何通知：旧协议是 `initialize` 的回应发出之后，新协议是收到第一个请求之后。
- 后台监视也是到这时才开始。

**信件是数据，不是指令**：信是别的参与方写的，服务说明里也提醒模型，信的内容与用户指令冲突时以用户为准。
工具参数一律以 `--选项=值` 和 `--` 分隔的形式交给核心，并按 inputSchema 校验；信里写的内容没法借工具参数冒充别人、改读别的信箱。

## 资源与提示词

- **资源**：
  - `mailbox://inbox`（唯一可订阅）、`mailbox://roster`、`mailbox://who`、`mailbox://tasks`、`mailbox://notes`；
  - `mailbox://doc/protocol`、`mailbox://doc/roles`；
  - 最近 20 封信 `mailbox://letter/<文件名>`。
  - 模板地址：`mailbox://letter/{file}`、`mailbox://status/{key}`。只能读信箱目录里符合命名的信件。
- **提示词**：10 个信件模板（`registration`、`ack`、`delivery`、`status`、`urgent`、`correction`、`handover`、`blocked`、`roster-change`、`question`），参数是 `to`、`subject`、`reply_to`。在 Claude Code 里显示为斜杠命令。

## 协议兼容

- 旧握手：`initialize`，支持 2025-11-25、2025-06-18、2025-03-26、2024-11-05，按客户端请求的版本回应。
- 新协议 2026-07-28：
  - 支持 `server/discover`，请求 `_meta` 里带版本；
  - 结果带 `resultType` 和服务信息；
  - 不支持的版本返回 -32022；
  - 支持 `subscriptions/listen`。
- 其它：`ping`、`logging/setLevel`、`notifications/cancelled`（被取消的请求不回应）、`notifications/progress`。
- 已用官方 MCP TypeScript SDK 的客户端做过一致性检查（`tests/sdk_conformance.cjs`）。
