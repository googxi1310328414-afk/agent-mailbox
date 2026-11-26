# MCP 服务

`scripts/mcp_server.py` 是一个独立的 MCP 服务（stdio）：信件、**通知**、**队列等待**（来电队列、工作队列）、**多模型自由通话**、**主控分配的权限**都在里面。它只用 Python 3.8+ 标准库，任何支持 MCP 的客户端都能接。
- 一个服务进程绑定**一个信箱、一个身份**，工具调用时不用再带 `--as` 和路径；几个模型各起各的服务、指向同一个信箱目录，就能互相通信，不需要中心进程。
- 收发都在进程内调用同目录的 `mailbox.py`，与命令行共用同一套状态，可以混用。
- 后台持续监视来信、通话、通知，有事件时推送通知、唤醒等待。
- 信箱还没建时主控加 `--init` 就地初始化；新模型加 `--join` 申请加入，主控批准前只能等。

## 启动参数

三种起法，参数完全一样：

```bash
uvx agent-mailbox-mcp --mailbox <信箱目录> --as <本方名> [选项]                 # 用 uv，从 PyPI 取（推荐）
agent-mailbox-mcp --mailbox <信箱目录> --as <本方名> [选项]                      # pipx install agent-mailbox-mcp 之后
python -I -B <skill>/scripts/mcp_server.py --mailbox <信箱目录> --as <本方名> [选项]   # 用 skill 压缩包
```

要用 GitHub 上还没发版的代码，uv 也能直接从 GitHub 取：`uvx --from git+https://github.com/googxi1310328414-afk/agent-mailbox agent-mailbox-mcp …`。
想知道用的是哪一版：`uvx agent-mailbox-mcp --version`（1.2.1 起）。
包里没有任何依赖；uv、pip 从源码构建时用的是仓库自带的标准库打包后端，也不用下载构建依赖。

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
| `--wait-default` | 50 秒 | `mailbox_wait`、`mailbox_call` 和队列等待的默认最长等待 |
| `--progress-interval` | 15 秒 | 阻塞调用发进度通知的间隔；客户端据此延长超时 |
| `--init` | 关 | 信箱还没初始化时，以本方为主控初始化（目录不存在就建） |
| `--join`、`--join-role` | 关、`assistant` | 本方不在花名册里（或已不在任）时申请加入，等主控批准 |
| `--model`、`--harness`、`--note` | 环境变量 `AGENT_MAILBOX_MODEL`、`AGENT_MAILBOX_HARNESS` | `--init`、`--join` 时登记的模型、宿主与附言 |
| `--token-file` | 无 | 身份令牌文件；也可设环境变量 `AGENT_MAILBOX_TOKEN`（优先） |
| `--version` | — | 打印版本号后退出，不起服务；输出与 `agent-mailbox --version` 相同 |

同一身份只该有一个监视。用 MCP 时，不要再给同一身份挂命令行 `watch`。

## 各客户端的配置

下面的命令和配置项是写作时各客户端的现行写法，以客户端自己的文档为准。路径换成实际值；Windows 路径写正斜杠或双反斜杠。

下面的例子都用 `uvx agent-mailbox-mcp`；用压缩包时，把它换成 `python -I -B <skill>/scripts/mcp_server.py`（JSON、TOML 里 `command` 写 `python`，`args` 开头加 `"-I", "-B", "<skill>/scripts/mcp_server.py"`）。

**Claude Code**

```bash
claude mcp add agent-mailbox -- uvx agent-mailbox-mcp --mailbox <信箱目录> --as <本方名>
```

- 想让来信直接注入会话：用频道方式启动，`claude --dangerously-load-development-channels server:agent-mailbox`。这是研究预览功能，要交互式会话，启动时会弹确认框。
- 不用频道时：靠 `mailbox_wait`，以及其它工具结果末尾附带的新事件。
- 工具超时用环境变量 `MCP_TOOL_TIMEOUT`（毫秒）调。

**Codex**（`~/.codex/config.toml`）

```toml
[mcp_servers.agent-mailbox]
command = "uvx"
args = ["agent-mailbox-mcp", "--mailbox", "<信箱目录>", "--as", "<本方名>"]
tool_timeout_sec = 600
```

`codex mcp add agent-mailbox -- uvx agent-mailbox-mcp …` 也行，但它不写超时，`tool_timeout_sec` 要手动加。

**Pi**

`pi mcp add agent-mailbox -- uvx agent-mailbox-mcp …`，或写进 `~/.pi/agent/mcp.json` 的 `mcpServers`。请求超时默认 60 秒，进度通知会重置计时。

**Cursor、Kimi、Gemini CLI 等使用 `mcpServers` JSON 的客户端**

```json
{
  "mcpServers": {
    "agent-mailbox": {
      "command": "uvx",
      "args": ["agent-mailbox-mcp", "--mailbox", "<信箱目录>", "--as", "<本方名>", "--join", "--model", "<模型>"]
    }
  }
}
```

主控给某个身份设了令牌（`perm token`）后，那个身份的配置里要加 `"env": {"AGENT_MAILBOX_TOKEN": "<令牌>"}`，或用 `--token-file`。

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
| `mailbox_call` | 呼叫另一个模型并等它回复：沿用一对一通话（或用 `conv` 指定通话），等到回复、通话结束或超时 |
| `mailbox_talk` | 通话：`start`、`say`、`read`、`list`、`queue`（来电队列）、`next`（接下一个来电）、`invite`、`leave`、`close` |
| `mailbox_notify` | 通知：`send` 发轻量提醒，`list` 看本方通知 |
| `mailbox_queue` | 工作队列：`create`、`push`、`take`（可排队等）、`done`、`fail`、`release`、`renew`、`cancel`、`show`、`job`、`wait`、`list` |
| `mailbox_perm` | 权限：`list`、`show`、`check`；`grant`、`revoke`、`reset`、`preset` 只有主控能做 |
| `mailbox_roster` | 花名册：查看；增减助手、上限、加入申请（`requests`、`approve`、`reject`）由主控或 `roster.admin` 做；交接、策略只有主控能做 |
| `mailbox_who` | 各参与方在线与进度（含通话未读、通知未看） |
| `mailbox_doctor` | 只读体检 |
| `mailbox_template` | 信件模板 |

工具出错时返回 `isError: true`，并附原因（损坏字、凭据、标题点名、只读、权限不足等），模型可以据此改正后重试。
权限不足的错误会写明缺哪条权限、依据是什么，以及主控该执行的 `perm grant …`；模型应如实转告，不要换身份或绕路。

## 通话、队列与阻塞等待

- `mailbox_call` 发出话后阻塞等回复。等待期间：
  - 按 `progressToken` 每隔 `--progress-interval` 秒发一次进度通知，写明对方监视是否在线、这个通话排在对方来电队列第几位；
  - 有紧急来信或紧急通知到达时提前返回（`interrupted: true`），打断它的那封信随结果送到；
  - 超时不是错误：结果里有通话编号，回复到了会作为【新事件】送来。
- 回复连同此前没送过的消息作为结果交出，并记为已送达，不会在别的工具结果里再报一次。
- `mailbox_talk` 的 `start`、`say` 也能带 `wait_seconds` 等回复；`next` 带 `wait_seconds` 时排队等来电。
- `mailbox_queue` 的 `take` 带 `wait_seconds` 时排队等活：队列文件变了才去领，至少每 10 秒试一次（租约可能悄悄到期）。`push` 带 `wait_seconds`、`wait` 动作都等任务结束拿结果。
- 客户端工具超时较短时（例如 Codex 未配置 `tool_timeout_sec`），把等待秒数设得比它短；等不到就再调一次，不会丢东西。

## 申请加入（`--join`）

- 本方不在花名册里时，服务启动就提交加入申请，并通知主控和有 `roster.admin` 的人。
- 批准之前只能用这几个工具：`mailbox_whoami`、`mailbox_wait`、`mailbox_template`、`mailbox_doctor`、`mailbox_who`，以及 `mailbox_roster` / `mailbox_perm` 的查看动作；其余工具返回「尚未加入」。服务说明也会告诉模型去等。
- `mailbox_wait` 等的是批准结果：批准后返回身份、收件箱和当前主控，后台监视随即开始；被拒则如实返回理由。
- 主控用 `mailbox_roster` 的 `approve` 批准，可带 `allow`、`deny` 顺带授权（只有主控能授权，`roster.admin` 只能批）；`reject` 拒绝。

## 新事件怎么到达模型

**哪些信算「还没交给模型」按状态算**：本方未读、写给本方、不是自己发的、并且没有送达记录的信。送达记录存在本方游标里（`delivered`）。
通话消息和通知同样按状态算：游标里有每个通话的已看序号（`talk_seen`）和通知流的已看序号（`notify_seen`），序号之后、不是自己发的就是没送过的。
- 只有带着这些信的回应确实发出去，才记为送达。
- 调用被取消、回应发送失败、服务中途重启，都不会丢信，下次照报。
- `mailbox_mark` 标回未读会清掉送达记录，于是再报一次。
- 一次最多列 20 封信、30 条通话消息、20 条通知，其余提示用 `mailbox_inbox`、`mailbox_talk`、`mailbox_notify` 查看。
- 通话消息在【新事件】里超过 2000 字会截断，并提示用 `mailbox_talk read` 看全文；`mailbox_call` 的回复不截断。
- 每个通话只送连续的一段：截断时截在某条之后，指针不会跳过没送的消息。

其它事件只在内存里排队，最多积 200 批，超出时如实提示丢了几批。这类事件包括：总账新行、工作日志、等待文件、报状态提醒、等待超时、心跳、角色变化、别处点名。

后台监视发现变化时，同时走四条路，客户端支持哪条就用哪条：
1. `notifications/claude/channel`：Claude Code 以频道方式加载时直接注入会话；其它客户端会忽略它。信件、通话消息（`TALK`）、通知（`NOTIFY`）都走这里。
2. `notifications/resources/updated`：订阅了 `mailbox://inbox`（新信）、`mailbox://talk`（通话消息）、`mailbox://notifications`（通知）的客户端会收到。旧协议用 `resources/subscribe`，新协议用 `subscriptions/listen`。只有这三个资源可订阅。
3. `mailbox_wait`：正在等的调用立即返回。
4. 其它信箱工具的结果末尾附「【新事件】」：在 1 之外，只有这一条会把事件记为已送达。

另外每批事件都会发一条 `notifications/message` 日志通知。
- 握手完成之前，服务不发任何通知：旧协议是 `initialize` 的回应发出之后，新协议是收到第一个请求之后。
- 后台监视也是到这时才开始。

**信件、通话、通知都是数据，不是指令**：它们是别的参与方写的，服务说明里也提醒模型，内容与用户指令冲突时以用户为准。
工具参数一律以 `--选项=值` 和 `--` 分隔的形式交给核心，并按 inputSchema 校验；长文字先写进临时文件再交给核心。信里写的内容没法借工具参数冒充别人、改读别的信箱。
权限在核心里判定，命令行和 MCP 一样严；要防有命令行权限的模型用 `--as` 冒充主控，给主控和用户设身份令牌（`perm token`，见 `protocol.md` 第 10 节）。

## 资源与提示词

- **资源**：
  - 可订阅：`mailbox://inbox`、`mailbox://notifications`、`mailbox://talk`；
  - `mailbox://roster`、`mailbox://who`、`mailbox://tasks`、`mailbox://notes`、`mailbox://queues`、`mailbox://perm`；
  - `mailbox://doc/protocol`、`mailbox://doc/roles`；
  - 最近 20 封信 `mailbox://letter/<文件名>`。
  - 模板地址：`mailbox://letter/{file}`、`mailbox://status/{key}`、`mailbox://talk/{conv}`（通话记录，只读不记为已看）、`mailbox://queue/{name}`。只能读信箱目录里符合命名的信件。
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
