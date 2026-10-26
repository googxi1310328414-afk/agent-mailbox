# 可选：Pi 扩展 agent-mailbox-monitor

**可选组件**。不装它，Pi 照样能用信箱（在检查点用 bash 跑 `mailbox.py inbox`）；装了以后，来信时 Pi 会被自动叫醒。

它在后台跑一个 `mailbox.py watch --json` 进程，把事件注入会话：空闲时直接开一轮，忙时排在当前轮次之后，不打断。收发、任务、备忘都调用同一个 `mailbox.py`，与其它参与方共用同一套协议和状态，没有第二份实现。

## 安装

1. 把 skill 放到 Pi 能读的位置，例如 `~/.pi/agent/skills/agent-mailbox/`（`mailbox.py install pi` 即复制到那里）。
2. 复制扩展：`adapters/pi/agent-mailbox-monitor.ts` → `~/.pi/agent/extensions/agent-mailbox-monitor/index.ts`。
3. 写设置 `~/.pi/agent/agent-mailbox.json`（环境变量 `AGENT_MAILBOX_DIR`、`AGENT_MAILBOX_ME` 等同名项优先）：

```json
{
  "mailbox": "<信箱目录>",
  "me": "<本方名>",
  "python": "python",
  "script": "<skill 目录>/scripts/mailbox.py",
  "scope": "",
  "interval": 5,
  "coalesceMs": 2000,
  "others": "digest",
  "mentions": false,
  "ledger": "",
  "markers": [],
  "remindStatus": 75,
  "remindWait": 45,
  "nudge": 15,
  "takeover": false
}
```

| 设置 | 含义 |
|---|---|
| `mailbox`、`me` | 信箱目录、本方名（必填） |
| `python`、`script` | Python 可执行文件、`mailbox.py` 路径（默认 `~/.pi/agent/skills/agent-mailbox/scripts/mailbox.py`） |
| `config`、`stateDir` | 花名册和控制目录不在默认位置时填 |
| `scope` | 只在 cwd 位于此目录下时启动；留空总是启动 |
| `others` | 共享箱里写给别人的信：`digest` 附带摘要 / `trigger` 也唤醒 / `off` 忽略 |
| `ledger`、`markers` | 盯总账里含这些标记的新行或改动行 |
| `remindStatus`、`remindWait`、`nudge` | 多少分钟没发信提醒报状态、等待超时提醒、有未完成任务时的心跳（0 关闭） |
| `takeover` | 已有同名监视在跑时是否接管（默认否：只提示，60 秒后再试） |

4. 重启 Pi 或执行 `/reload`，再 `/mailbox status` 确认 `监视中`。

## 提供的能力

| 能力 | 用法 |
|---|---|
| 自动唤醒 | 来信、广播、点名、总账新行、等待文件出现、该报状态、等待超时、心跳 |
| `mailbox_send` | 写信：`to`、`subject`、`content` 或 `draftPath`、`urgent`、`replyTo`、`corrects`、`attach`、`dryRun` |
| `mailbox_inbox` | 未读列表（`all`、`others`、`limit`） |
| `mailbox_read` | 读信并标已读（`refs`、`peek`） |
| `mailbox_monitor` | `status`、`start`/`stop`/`restart`、`tasks`（整表替换）、`waiting_on`、`wait_add`/`wait_remove`、`notes_append`/`notes_show` |
| `/mailbox` | `status`、`start`、`stop`、`restart`、`who` |

日志在 `~/.pi/agent/agent-mailbox/events.log`。不要与其他同样会注入信箱事件的扩展同时开，否则同一封信会注入两次。

## 测试

`node test-harness.cjs`：在临时目录里用 Pi 自带的 jiti 和 typebox 加载本扩展，走完收信注入、忙时排队、写信、拒收凭据、读信、任务与等待、等待文件、续接备忘、被接管、会话结束、补报停机期间来信，共 28 项；不碰真实信箱，也不影响正在运行的 Pi。Pi 不在 npm 全局目录时，用环境变量 `PI_CODING_AGENT_DIR` 指定 `@earendil-works/pi-coding-agent` 的安装目录。
