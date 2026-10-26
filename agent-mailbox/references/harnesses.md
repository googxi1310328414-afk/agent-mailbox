# 各宿主的接入与等信方式（可选阅读）

信箱本身只是文件，任何能跑 Python 3.8+ 的宿主都能收发，不依赖下面任何一个客户端。本文只是常见客户端的接法备忘。差别只在「怎么在来信时被叫醒」。下文 `mailbox.py` 指本 skill 的 `scripts/mailbox.py`，`<信箱>`、`<名>` 换成实际值。

## 首选：接 MCP 服务

支持 MCP 的客户端，包括 Claude Code、Codex、Pi、Cursor、Kimi、Gemini CLI 等，最省事的做法是接 `scripts/mcp_server.py`（配置见 `mcp.md`）：
- 身份在启动时绑定一次；
- 等回信用 `mailbox_wait`；
- 来信会附在工具结果里，或经通知推送；
- Claude Code 以频道方式加载时直接注入会话。

下文是不接 MCP 时的做法。

## 通用做法（所有宿主）

1. 开工或会话重启后：`roster` 确认身份与角色 → `inbox` 看未读 → `notes show`、`tasks` 续上进度。
2. 在这些时刻查一次 `inbox`：每次写共享账本之前；每次派发或验收一批子代理之后；长时间等待时至少每 5 分钟。
3. 实在需要干等回信时，前台跑 `watch --once --seconds 600`：来信即返回，超时返回码 3。
4. 安装：`python -I -B mailbox.py install agents pi`（`agents` 即 `~/.agents/skills`，Claude Code、Codex、Kimi 等都读；Pi 若在设置里排除了它，就单独装 `pi`）。

## Claude Code（以及有后台任务、任务结束会唤醒会话的宿主）

用 Bash 工具的后台任务（`run_in_background: true`）挂一条 `watch --once`。它有事件就退出，退出会唤醒会话；超时也退出，照样重挂。

```bash
python -I -B "<skill>/scripts/mailbox.py" --mailbox "<信箱>" --as <名> watch --once \
  --remind-status 75 --remind-wait 45 --nudge 20
```

- 唤醒后：读输出里的 `MAIL` 行 → `read <编号>` 读原信 → 处理 → **立刻重挂**。重挂间隙到的信不会丢，下次照报。
- 不要写 `sleep` 轮询循环，也不要用 `codex queue` 一类的推送去「提醒」别的会话；只写信，等对方自己的监视。
- 想在自己派出的 Workflow 子代理开始或结束时也被叫醒：加
  `--journal "C:/Users/<用户>/.claude/projects/<项目>/<会话ID>/subagents/workflows/*/journal.jsonl"`。
- 想盯总账里登记给自己的新行：加 `--ledger <总账路径> --marker <本方在总账里的标记>`。
- 要等某个结果文件但又不想让路径出现在命令行里（执行器按命令行计数时）：把路径写进一个文件，用 `--wait-from <该文件>`。
- 输出里出现 `SUPERSEDED` 且你没有重挂过：有别的会话在用你的身份，停下问用户。
- 上下文压缩或会话重启后，先 `who` 看自己的监视是否还在（`●` 为在线），不在就重挂。

## Codex（桌面版、CLI）

- Codex 没有「后台任务结束即唤醒」的机制，桌面版的外部队列消息还要人手点「引导」才送进会话。所以对 Codex 一律**只写信、不推送**，由它在检查点自己查信箱（见「通用做法」第 2 条）。
- Codex 当主控时，建议在它的 `AGENTS.md` 或任务说明里写明查信时机和本方名，例如「每次写总账前运行 `mailbox.py --as lead inbox`」。
- 沙箱：`sandbox_mode = "workspace-write"` 时，信箱目录不在工作区里就写不进去。把它加到 `config.toml`：

  ```toml
  [sandbox_workspace_write]
  writable_roots = ["<信箱目录>"]
  ```

## Pi

两种用法，选一种：

1. **扩展（推荐，能自动唤醒）**：把 `adapters/pi/agent-mailbox-monitor.ts` 装成 Pi 扩展，详见 `adapters/pi/README.md`。它在后台跑一个 `watch` 进程，把事件注入会话（会话忙时排到当前轮次之后，不打断），并提供 `mailbox_send`、`mailbox_inbox`、`mailbox_read`、`mailbox_monitor` 工具和 `/mailbox` 命令。
   - 能力：信件、总账标记、等待文件、心跳、等待超时提醒、任务清单、续接备忘。
   - 不要与其他同样会把信箱事件注入会话的扩展同时开，否则同一封信会注入两次。
2. **不装扩展**：同 Codex，在检查点用 bash 工具跑 `inbox`。

## Kimi Code、GLM（Z Code 等）、Grok、OpenCode、Cursor 等

- 宿主有后台任务、结束能唤醒会话：照 Claude Code 的做法。
- 没有：照 Codex 的做法（检查点查信，干等时前台 `watch --once`）。
- 这些宿主多数读 `~/.agents/skills`；装到那里一处即可。

## WSL / Linux 上的参与方

- 同一个信箱经 `/mnt/<盘>/...` 访问，例如 Windows 的 `D:/shared/mailbox` 在 WSL 里是 `/mnt/d/shared/mailbox`；文件名、编号、锁都通用。
- 跨 Windows 与 WSL 时进程号互相看不见：锁和在线状态改按时间判断是否陈旧（锁 30 秒、监视心跳 90 秒），`who` 会显示主机名。
- Python 用 `python3`；同样只要标准库。

## 子代理

子代理不以任何参与方的身份发信，也不挂 `watch`；需要沟通时把结果交回派它的一方。派子代理时把 `references/roles.md` 第 4 节写进提示词。
