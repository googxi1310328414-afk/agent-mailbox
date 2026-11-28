# 更新记录

每一版的说明写在「## 版本号」下面。发版时（推 `v<版本号>` 标签，或在 Actions 页面手动运行 `release` 工作流），发版工作流会把这一节连同压缩包的 SHA-256 作为 Release 说明。

## 1.3.0

通话更快、更省上下文，也更可控；任务不必指定人，按技能自动分派。

### 新增

- **通话即时送达（门铃）**：
  - 发言、发通知、投信之后，写入方按响收件方的门铃。
  - 等待中的 `mailbox_wait`、`mailbox_call`、队列等待，以及命令行的 `watch`、`talk … --wait`、`queue … --wait`，都在 0.1 秒内醒来。
  - 默认参数下，一轮呼叫往返的等待（不含模型思考）从约 2 秒降到约 0.2 秒。
  - 门铃只是提示，收件仍以状态为准；对方还是旧版本时，退回原来的轮询。
- **自由通话的安全阀**：
  - 新建的通话最多 40 条发言（用户说的不算），到了自动暂停。
  - 只有两方、又没有用户参与时，300 秒内来回 20 条也自动暂停。
  - 暂停后只有主控和用户能发言，另开同一批人的通话也会被拒。主控收到通知，用 `talk resume`（可带 `--max-turns` 改上限）继续，或 `talk close` 结束；主控也可以随时 `talk pause`。
  - 默认值用 `roster policy --talk-max-turns`、`--talk-burst` 改，只影响之后新建的通话。
- **配额**（`perm quota`，MCP：`mailbox_perm quota`）：
  - 主控按人或按角色组（`@assistants`、`@observers`），限制一段时间内的发言、新开通话、发信、发通知、投任务次数，写法如 `60/h`、`10/m`、`500/d`。
  - 用完时操作被拒，主控收到通知；`perm show` 显示生效的配额与已用次数。主控与用户不受限。
- **任务按技能分派**：
  - 投任务时写所需技能（`queue push --needs python,review`）和加分技能（`--prefers docs`），不必指定人。
  - 只有具备全部所需技能、没拒绝过、手上在做的没到上限的人领得到；顺序是优先级、指派给自己的、加分技能命中多的、先来先到。
  - 新任务只通知能接的人（先通知手上有空的）；没人能接时主控收到通知，投放结果里也写明能接的人。
  - `queue take --job <号>` 领指定的任务；领不到时说明各个排队任务领不了的原因（缺技能、手上已满、拒绝过……）。
- **技能由助手申报、主控批准**：
  - 申请加入时 `join --skills` 申报（MCP 启动加 `--skills`），主控 `roster approve` 时确认或用 `--skills` 改写。
  - 已在任的用 `roster skills <名> --set …` 申报，主控 `--approve` 或 `--reject --reason …`；主控也可以直接设。
- **同时在做的任务上限**：每人默认同时最多领 1 个；`roster policy --max-jobs` 改默认（0 表示不限），`roster update <名> --max-jobs` 单独设。主控不受技能和上限限制。
- **任务做的过程中**：
  - `queue progress`：报进度（百分比或文字），顺带续租；投放者收到通知，同一任务 5 分钟内最多一次。
  - `queue block` / `unblock`：卡住时写明缺什么，任务转为「卡住」、租约暂停，主控和投放者收到通知；处理好后由主控、投放者或领取方解除。
  - `queue decline`：不适合自己就拒绝并写明原因，任务放回排队，以后不再分给他；能接的人都拒绝了，主控收到通知。
- **自动接单**（MCP）：`roster update <你> --auto-take review`（`*` 表示全部队列）。之后 `mailbox_wait` 空闲等待、手上又没满时，服务直接领最合适的任务，作为【自动接单】交给模型；这次回应没送到就自动放回排队。
- **工具清单按身份裁剪**：
  - `tools/list` 只列本方权限够得着的工具、动作和参数；权限或角色变化时推送 `tools/list_changed`。
  - 新参数 `--tools`：`auto`（默认）、`all`（固定列出全部）、`core`（8 个常用工具），或逗号分隔的工具名。
  - 实测上下文开销（粗估）：助手约 4300 tokens，旁听者约 3100，`--tools core` 约 1900；1.2 时每人约 5400。主控多了暂停、配额、任务领取等动作，与原来持平（约 5400）。

### 改进

- 工具说明精简，详细用法放进新资源 `mailbox://doc/mcp`。
- 通话被暂停时，`mailbox_call` 立即返回，不再白等到超时。
- 正在 `mailbox_wait` 的模型已从等待结果拿到的消息，后台监视不再重复推送。
- 刚到的信一过 1 秒的送达门槛，`mailbox_wait` 就交出，不再等下一轮轮询。
- 没有能领的任务时，`queue take` 不再重写队列文件。

### 兼容性

- 花名册 schema 不变。新字段 `policy.talk_max_turns`、`policy.talk_burst`、`policy.max_jobs`、`quotas`，以及参与者的 `skills`、`skills_proposed`、`max_jobs`、`auto_take` 都是可选的；旧版工具会原样保留并忽略它们，也就不执行上限、配额和技能筛选。
- **每人同时在做的任务默认最多 1 个**（原来不限）。要一人同时领多个，用 `roster policy --as <主控> --max-jobs 0` 恢复不限，或给某人单独设 `--max-jobs`。
- 没写所需技能的任务谁都能领，与原来一样。任务新增「卡住」（`blocked`）状态：旧版工具不会领它，也不会当作租约到期放回，但不能对它完成或放回。
- 1.3 之前建的通话不受发言上限和快速来回限制。
- 工具的完整参数定义只增不减，旧客户端照常能调。不认识 `tools/list_changed` 的客户端，可以用 `--tools all`。

### 安装

- `uvx agent-mailbox-mcp --mailbox <信箱目录> --as <本方名>`，或 `pipx install agent-mailbox-mcp`。
- 已经用 uvx 跑过旧版的机器，先跑一次 `uvx agent-mailbox-mcp@latest --version`，换成新版。
- 也可以下载 `agent-mailbox-1.3.0.zip`，当 Agent Skill 用。

## 1.2.1

MCP 服务能直接查版本号了。

### 改进

- `agent-mailbox-mcp --version` 打印版本号后退出，不起服务；用压缩包时是 `mcp_server.py --version`。输出与 `agent-mailbox --version` 相同，也就是握手时报的版本。原来会报参数错误。
- 已上架 PyPI，1.2.0 也已补传：`uvx agent-mailbox-mcp …`、`pipx install agent-mailbox-mcp` 可以直接用。文档里「发布到 PyPI 之前」的写法改为：要用 GitHub 上还没发版的代码，再用 `uvx --from git+https://github.com/googxi1310328414-afk/agent-mailbox …`。

### 安装

- `uvx agent-mailbox-mcp --mailbox <信箱目录> --as <本方名> [--init | --join]`，或 `pipx install agent-mailbox-mcp`。
- 也可以照旧下载 `agent-mailbox-1.2.1.zip`，当 Agent Skill 用，或用 `python -I -B agent-mailbox/scripts/mcp_server.py …` 运行。

## 1.2.0

一行接入，长期使用也不变慢。

### 新增

- **Python 包 `agent-mailbox-mcp`**：
  - 不用下载解压、也不用写脚本路径：`uvx agent-mailbox-mcp --mailbox <信箱目录> --as <本方名>` 就能起 MCP 服务。
  - `pipx install agent-mailbox-mcp` 装好后有 `agent-mailbox-mcp`（MCP 服务）和 `agent-mailbox`（命令行）两个命令，也可以用 `python -m agent_mailbox`。
  - 发布到 PyPI 之前，也能直接从 GitHub 运行：`uvx --from git+https://github.com/googxi1310328414-afk/agent-mailbox agent-mailbox-mcp …`。
  - 包里的布局与 skill 目录一致，模板、文档、`install` 命令照常可用；仍然没有任何依赖。
- **自带打包后端**（`tools/build_backend.py`，只用标准库）：同一份源码每次打出的 wheel 与 sdist 逐字节相同，从 sdist 再打的 wheel 也相同；`tools/verify_wheel.py` 会在全新的虚拟环境里装好后，把命令行、自检和 MCP 握手各跑一遍。
- **发版**：Release 附件加上 wheel 与 sdist；在 PyPI 配好可信发布、并把仓库变量 `PYPI_PUBLISH` 设为 `true` 后，发版时同时上传到 PyPI。

### 改进

- 通知流与通话记录改为增量读取：文件只是变长了，就只读新增的部分。在 2 万条（2 MB）的通知流上，来一条新通知后再读，从 55 ms 降到约 1 ms。
- 通知流超过 512 KB 时，把本人看过的旧条目移到 `notify/<名>.archive.jsonl`：没看过的、以及最近 500 条留在原处，序号不变。一直不看的人，最多为他留 1 万条。

### 安装

- `uvx agent-mailbox-mcp --mailbox <信箱目录> --as <本方名> [--init | --join]`，或 `pipx install agent-mailbox-mcp`。
- 也可以照旧下载 `agent-mailbox-1.2.0.zip`，当 Agent Skill 用，或用 `python -I -B agent-mailbox/scripts/mcp_server.py …` 运行。

## 1.1.0

把项目完善成一个**独立的 MCP 服务**：

- 新增通知、队列等待、多模型自由通话、主控分配权限四项能力。
- 全部落在共享信箱目录里，不需要中心进程，仍只依赖 Python 3.8+ 标准库。
- 1.0 的信件功能和行为保持不变，旧信箱直接可用。

### 新增

- **主控分配权限**（`perm`、`mailbox_perm`）：
  - 主控可以按人授予、收回、重置权限，也能改角色预设。
  - 权限有发信、广播、通知、通话、旁听、队列投放、领取、管理、状态板、代管花名册，范围可以细到某个人、某个角色组或某个队列。
  - 角色预设由原来的通信策略推出，所以行为与 1.0 一致；改动即时生效，本人会收到通知。
  - 可选的身份令牌（`perm token`）防止用 `--as` 冒充主控或用户来绕开权限。
- **通知**（`notify`、`mailbox_notify`）：
  - 每人一条持久的通知流，可以手动发轻量提醒。
  - 派活、任务结果、权限变更、加入申请会自动发通知。
- **多模型自由通话**（`talk`、`mailbox_call`、`mailbox_talk`）：
  - 一对一或多方通话，可以邀请、退出、结束。
  - `mailbox_call` 发出后同步等对方回复，等待期间发进度通知，有紧急来信时提前返回。
  - 主控能加入任何通话，旁听者能读任何通话。
- **队列等待**：
  - 来电队列：紧急优先、先来后到，被叫方用 `talk next` 逐个接。
  - 工作队列（`queue`、`mailbox_queue`）：支持优先级、指派、领取名单。领到的任务有租约，到期没完成就放回排队；尝试次数用尽就判失败并通知投放者。领取方可以排队等活，投放方可以等结果；几个模型同时领，也不会领到同一个。
  - 新模型申请加入（`join`）后排队等主控批准（`roster approve` / `reject`），批准时可以顺带授权。
- **MCP 服务**：
  - 新增 `mailbox_call`、`mailbox_talk`、`mailbox_notify`、`mailbox_queue`、`mailbox_perm`，共 19 个工具。
  - 通话消息与通知按状态送达，服务重启不丢。
  - 新增可订阅资源 `mailbox://notifications`、`mailbox://talk`。
  - `--init` 就地初始化信箱；`--join` 申请加入，批准前只开放少数工具。
- **命令行**：`watch` 会报 `TALK`、`NOTIFY` 事件；`who`、`doctor` 覆盖新状态，并在缺身份令牌时提醒。

### 修复

- 等待文件刚建好、还没写完时，监视原来会立刻报 `WAITFILE`，报出的内容是空的或残缺的，并且消耗掉这条等待，真正的内容之后不再报。现在要文件大小一轮不变才报，与信件同一规则。

### 兼容性

- 花名册 schema 仍是 `agent-mailbox/1`，新字段都是可选的。旧版工具会忽略这些字段，也就不会执行按人权限。
- 旁听者原来是写死「不发信」，现在由角色预设表达，主控可以按人授予 `send`。

### 安装

1. 下载 `agent-mailbox-1.1.0.zip` 并解压。当 skill 用时，把解出的目录放进客户端的 skills 目录，例如 `~/.agents/skills/`。
2. MCP 怎么配，见 `agent-mailbox/references/mcp.md`。主控启动时加 `--init`，其它模型加 `--join` 申请加入。
3. 先跑 `python -I -B agent-mailbox/scripts/mailbox.py selftest`，确认环境能用。

## 1.0.0

多模型协作信箱的第一个版本：一名主控（任何时刻唯一）带若干助手（数量可增减），经共享目录里的 Markdown 信件通信。

### 包含什么

- **MCP 服务**（`scripts/mcp_server.py`）：
  - 14 个工具、可订阅的资源、10 个信件模板提示词。
  - 新来信经三条路送到模型：Claude Code 频道推送、资源更新通知、`mailbox_wait` 长等待。
  - 送达按状态记录，调用被取消或服务重启都不丢信。
  - 兼容旧握手（2024-11-05 至 2025-11-25）和 2026-07-28 的无状态协议。
- **Agent Skill**：`SKILL.md`，以及协议、角色与写信纪律、命令参考、各客户端接法。
- **命令行**（`scripts/mailbox.py`）：
  - 投递：排他投递、绝不覆盖；信头时间现取；拦截损坏字和凭据。
  - 花名册：主控唯一、助手可增减、主控交接。
  - 监视与提醒；任务清单、续接备忘、状态板；体检与自检。
- **可选的 Pi 扩展**：`adapters/pi/`。

只依赖 Python 3.8+ 标准库。
