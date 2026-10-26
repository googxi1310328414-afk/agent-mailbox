# agent-mailbox

多模型协作信箱：一名**主控**（任何时刻唯一）带若干**助手**（数量可增减），经共享目录里的 Markdown 信件通信。
同一套能力有三种用法：**MCP 服务**、**Agent Skill**、**命令行**。只依赖 Python 3.8+ 标准库，Claude Code、Codex、Pi、Cursor、Kimi、Gemini CLI 等客户端，以及 Claude、GPT、Kimi、GLM、Grok 等模型都能用。

> **English.** A file-based mailbox for coordinating multiple AI agents: exactly one controller, any number of assistants. Every letter is created exclusively and never overwritten. It ships as an MCP server (stdio, compatible with both the `initialize` handshake and the stateless 2026-07-28 protocol), an Agent Skill, and a CLI, all pure Python standard library. Docs are in Chinese.

## 能做什么

- **投递**：
  - 编号锁加排他创建，被占就顺延，绝不覆盖；写后读回核 SHA-256；
  - 信头时间在投递那一刻现取；
  - 拦截 U+FFFD 损坏字、CR、BOM、超长信和疑似凭据。
- **角色**：
  - 主控唯一，违反这条的改动会被拒绝；
  - 助手可增减，也能调上限；
  - 交接主控时，新主控接手主控箱并继承已读；
  - 可设助手互通策略。
- **路由**：
  - 每人一个收件箱，也可以几个助手共用一个，靠标题和信头点名区分；
  - 支持广播、回复、更正（原件不改）、附件实算 SHA。
- **等信**：
  - 来信、广播、点名、总账新行、工作流日志、等待文件；
  - 报状态提醒、等待超时提醒、心跳；
  - 同批事件合并，重挂间隙不漏报。
- **续接**：任务清单、等待对象、续接备忘、状态板，会话重启后从这里接着干。
- **MCP**：
  - 14 个工具、可订阅的资源、10 个信件模板提示词；
  - 新事件同时走 Claude Code 频道推送、资源更新通知、`mailbox_wait` 长等待，并附在工具结果里。
- **运维**：`doctor` 只读体检、`who` 在线总览、`selftest` 自检。

## 快速开始

1. 下载 [Releases](../../releases) 里的 `agent-mailbox-<版本>.zip`，解压。
   - 当 skill 用时，放进客户端的 skills 目录，例如 `~/.agents/skills/`。
   - 下文 `<skill>` 指解出的 `agent-mailbox/` 目录。
2. 用户或主控初始化信箱（只做一次），再加助手：

   ```bash
   python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox init --controller lead --model gpt-5.5 --harness any --as user
   python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox roster add claude-a --as lead --model claude-opus-5-5 --harness claude-code
   ```

3. 每个参与方用自己的身份接上 MCP 服务，以 Claude Code 为例：

   ```bash
   claude mcp add agent-mailbox -- python -I -B <skill>/scripts/mcp_server.py --mailbox /path/to/mailbox --as claude-a
   ```

   Codex、Pi、Cursor 等客户端的写法见 [`references/mcp.md`](agent-mailbox/references/mcp.md)。

4. 不接 MCP 也行，直接用命令行：

   ```bash
   python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox --as claude-a send --subject "开工回执" --body "已读登记，开工。"
   python -I -B <skill>/scripts/mailbox.py --mailbox /path/to/mailbox --as lead inbox
   ```

## 文档

- [`SKILL.md`](agent-mailbox/SKILL.md)：给模型看的用法总览。
- [`references/mcp.md`](agent-mailbox/references/mcp.md)：MCP 服务的启动参数、各客户端配置、工具、资源、提示词与推送方式。
- [`references/protocol.md`](agent-mailbox/references/protocol.md)：目录、命名、编号、信头、路由、投递算法、不变量。
- [`references/roles.md`](agent-mailbox/references/roles.md)：主控与助手的职责和写信纪律。
- [`references/commands.md`](agent-mailbox/references/commands.md)：全部命令和参数。
- [`references/harnesses.md`](agent-mailbox/references/harnesses.md)：各客户端的等信方式。

## 测试与打包

```bash
python -I -B agent-mailbox/scripts/mailbox.py selftest   # 临时目录里走全流程
python -I -B tests/test_mailbox.py                       # 单元、并发与回归测试
python -I -B tests/test_mcp.py                           # MCP 协议测试（真实子进程，经 stdio 通信）
MCP_SDK_DIR=<目录>/node_modules/@modelcontextprotocol/sdk node tests/sdk_conformance.cjs   # 用官方 MCP SDK 客户端做一致性检查
python -I -B tools/package.py                            # 打包到 dist/，同一份源码每次打出的包逐字节相同
```

GitHub Actions 会在 Linux、Windows、macOS 上跑以上测试。

## 许可

MIT，完整条款见 [LICENSE](LICENSE)。
