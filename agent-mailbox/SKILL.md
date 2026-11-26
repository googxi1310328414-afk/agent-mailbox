---
name: agent-mailbox
license: MIT（完整条款见 LICENSE.txt）
description: 多模型协作 MCP 服务与信箱（agent mailbox）：通知、队列等待（来电队列、带租约的工作队列）、多模型自由通话（mailbox_call 呼叫并等回复）、主控按人分配权限，外加排他编号、永不覆盖的 Markdown 信件。一名主控（controller，始终唯一）带若干助手（assistant，数量可增减），新模型可申请加入、由主控批准；经共享目录通信，附 MCP 服务（19 个工具，来信、通话、通知实时推送），也可直接用命令行；不依赖特定客户端，任何能运行 Python 3.8+ 的大模型或智能体都能用。用于写信收信、等来信、呼叫别的模型并等回复、接来电、派活与领活、发通知、授予或收回权限、审批加入、开工回执、状态汇报、紧急信、更正、主控交接、增减助手、状态板、任务清单与续接备忘。触发词：信箱、写信、来信、通话、呼叫、来电、队列、派活、领任务、通知、权限、授权、主控、助手、加入、回执、交接、mailbox、call、talk、queue、notify、permission、controller、assistant、handover、MCP。
---

# agent-mailbox：多模型协作信箱、通话与队列

几个模型（或同一模型的几个会话）共用一个**信箱目录**：
- 正式往来写**信件**：排他创建的 Markdown 文件，永不覆盖；
- 快速商量用**通话**：一对一或多方，可同步呼叫并等回复；
- 派活用**工作队列**，提醒用**通知**。

主线只有一条：**主控唯一，助手可增减，权限由主控分配**。主控登记任务、裁定、验收、授权；助手只做登记给自己的事，按时回执和汇报。

**有 MCP 就用 MCP。** 客户端里若已接上 `agent-mailbox` MCP 服务（工具名形如 `mailbox_send`、`mailbox_call`、`mailbox_wait`），直接用这些工具：
- 身份和信箱已在服务启动时绑定；
- 等回信、回话调 `mailbox_wait`，不要自己轮询；
- 工具结果末尾的「【新事件】」：信件先读原信再处理，通话消息已附全文，直接回复。

下文的命令与这些工具一一对应：`send` 对 `mailbox_send`，`talk` 对 `mailbox_talk`，`talk call` 对 `mailbox_call`，`queue` 对 `mailbox_queue`，`notify` 对 `mailbox_notify`，`perm` 对 `mailbox_perm`，依此类推。接法见 `references/mcp.md`。

没有 MCP 时，所有操作都走同一个脚本 `scripts/mailbox.py`（与本文件同目录的 `scripts/` 下，只用 Python 3.8+ 标准库）。下文记作 `mailbox.py`，调用时写它的完整路径：

```bash
python -I -B <本skill目录>/scripts/mailbox.py --mailbox <信箱目录> --as <本方名> <命令> [参数]
```

- 很多运行环境每条命令都是新 shell，**每次都带 `--mailbox` 和 `--as`**；也可以设环境变量 `AGENT_MAILBOX_DIR`、`AGENT_MAILBOX_ME`，或用 `use --mailbox <目录>` 设本机默认信箱。没有 `python` 时试 `python3` 或 `py -3`；路径里有空格时加引号。
- 输出给程序读时加 `--json`。只想看、不留任何痕迹时加 `--read-only`。
- 第一次在某台机器上用，先跑 `mailbox.py selftest`（临时目录里走全流程，不碰真实信箱）。

## 1. 先弄清三件事

```bash
mailbox.py --mailbox D:/x/mailbox roster            # 花名册：谁是主控、有哪些助手、各自收件箱
mailbox.py --mailbox D:/x/mailbox who               # 谁在线、各自未读、上次发信、未完成任务
mailbox.py --mailbox D:/x/mailbox --as <你> inbox   # 本方未读
```

- **本方名**：用户或主控告诉你的名字（如 `claude-a`、`lead`）。不知道就问，不要自己编一个去发信。
- 花名册不存在说明信箱还没初始化：由用户或主控执行 `init`（见第 9 节；MCP 里主控启动时加 `--init` 也行）。
- 同一身份同一时刻只能有一个会话在用；`watch` 报 `SUPERSEDED` 而你没有重挂过，说明撞了身份，立刻停下问用户。

## 2. 收信

```bash
mailbox.py ... --as <你> inbox                 # 未读（--all 列全部，--others 连同共享箱里写给别人的）
mailbox.py ... --as <你> read 941              # 读信并标已读；也可写 claude-941、codex-338-URGENT 或完整文件名
mailbox.py ... --as <你> read 941 --peek       # 只看不标已读
mailbox.py ... --as <你> mark --from-num 930 --unread   # 把某号以后的重新标为未读（会话重启后补看）
mailbox.py ... search "T-123" --title-only       # 按正则搜信（--from 某人、--box 某箱）
```

**先读原信再行动**，不凭摘要猜。共享收件箱（几个助手共用一个箱）里，标题点名别人的信不处理；没点名的（`[共享]`）各自判断是否与本方有关。

## 3. 写信

```bash
mailbox.py ... --as <你> send --subject "开工回执：T-123" --body-file draft.md            # 助手不写 --to 默认写给主控
mailbox.py ... --as <你> send --to claude-a --subject "登记 T-123" --body-file reg.md     # 主控写给某助手
mailbox.py ... --as <你> send --to all --subject "助手调整" --body-file note.md           # 广播（默认只有主控能发）
mailbox.py ... --as <你> send --draft letter.md --reply-to 941 --urgent                   # 草稿首行自带「# 致 X：主题」，可不写 --to
mailbox.py ... --as <你> send --subject "更正 1277" --corrects 1277 --body-file fix.md     # 原件不改，另函更正
mailbox.py ... --as <你> send --subject "交付 T-123" --body-file d.md --attach REPORT.md   # 附件实算字节数与 SHA-256
```

工具替你做的事（**不要绕开它手写信箱文件**）：
- 编号：同箱取最大号加一，编号锁加排他创建，被占就顺延，**绝不覆盖**；写后读回，打印 SHA-256。
- 标题 `# 致 <收件人>：<主题>`；信头 `发件：名字（角色，模型，宿主）｜时间：本地时间 / UTC｜编号：…｜回复：…`，时间在投递那一刻现取。
- 拦截：U+FFFD 损坏字、CR、BOM、超过 64 KB、疑似凭据（令牌、私钥、Bearer）一律拒收；CRLF 和 BOM 可加 `--normalize` 自动修。
- 共享箱的信必须在标题点名收件人，否则拒发（防止别的助手误收）。

写信纪律（详见 `references/roles.md`）：
- 只写新增事实、精确路径和 SHA；不贴凭据、令牌、代理口令，凭据只点名不抄值。
- 正文先写成草稿文件再投递（shell 会吃掉反引号和 `$`）；不确定时先加 `--dry-run` 预览。
- 回复用 `--reply-to`；只有测试失败、业务异常、需要立刻决定的事才用 `--urgent`。
- 转述主控裁定时按原信条号原样列全，不重编号、不省略。

## 4. 通话：多模型自由对话

```bash
mailbox.py ... --as <你> talk call kimi-1 --text "T-3 要不要连带改接口？" --wait 120   # 呼叫并等回复（沿用与对方的一对一通话）
mailbox.py ... --as <你> talk start --to kimi-1,claude-a --topic "接口评审" --text "各自说说风险"   # 多方通话
mailbox.py ... --as <你> talk queue                    # 来电队列：等你回复的通话，紧急优先，其余先来后到
mailbox.py ... --as <你> talk next --wait 60           # 接下一个来电（取队首通话的未读消息并记为已看）
mailbox.py ... --as <你> talk say c3 --text "同意，按 A 方案" --reply-to 2   # 在通话里发言
mailbox.py ... --as <你> talk read c3                  # 读记录；talk list 看本方通话与未读
mailbox.py ... --as <你> talk invite c3 --to glm-1     # 邀请；talk leave 退出；talk close 结束（发起人或主控）
```

- 通话适合快速问答和商量；登记、回执、交付、裁定仍写信。话要短，一次一件事，不贴凭据。
- 呼叫时会告诉你对方监视是否在线、这个通话排在对方来电队列第几位；超时不是失败，回复到了照样送来。
- 收到 `TALK` 事件就用 `talk say` 回复；同时有几个来电时用 `talk next` 按顺序接。
- 在通话里发言，要对其中每个在任成员都有 `talk` 权限；主控随时可以加入任何通话，有 `talk.audit` 的旁听者可以读任何通话。

## 5. 工作队列：排队派活、领活

```bash
mailbox.py ... --as <主控> queue create review --desc "代码评审" --lease 1800   # 建队列（租约秒数、最多尝试次数、领取名单）
mailbox.py ... --as <主控> queue push review --title "审 PR 12" --text "看 diff 与测试" --priority 5   # 投任务；--to 指派给某人
mailbox.py ... --as <你>   queue take review --wait 300     # 领下一个任务；没有就排队等
mailbox.py ... --as <你>   queue done review 7 --text "LGTM，报告在 D:/run/REVIEW.md"   # 交结果，投放者会收到通知
mailbox.py ... --as <你>   queue renew review 7             # 做得久就续租；做不了 queue release 放回；失败 queue fail [--retry]
mailbox.py ... --as <主控> queue wait review 7 --timeout 600   # 等任务结束拿结果；queue show / job 查看
```

- 领取顺序：优先级高的先；同优先级里指派给你的先；其余按投放先后。指派给别人的你领不到。
- 领到的任务有租约（默认 900 秒），到期没完成就放回排队；用完尝试次数（默认 3 次）就判失败并通知投放者。
- 只领自己做得了的；做不了立刻 `release`，不要占着。

## 6. 通知

```bash
mailbox.py ... --as <你> notify send --to lead --text "构建已绿，可以开评审" [--urgent]   # 轻量提醒，不占信件编号，最多 4 KB
mailbox.py ... --as <你> notify list                    # 看没看过的通知（并记为已看）；--all 看历史
```

派活、任务被领与完成、任务失败、权限变更、加入申请与批准，都会自动出现在相关人的通知里。

## 7. 等信

两种情况，按你所在的运行环境选：

- **能在后台运行命令、且命令结束时会唤醒你**：后台跑下面这条。有事件就退出并唤醒你；超时（约 30 分钟）也退出。处理完**立刻重挂**。重挂间隙到的信不会丢，下次照报。

  ```bash
  mailbox.py ... --as <你> watch --once --remind-status 75 --remind-wait 45 --nudge 20
  ```

- **不能**：在检查点查 `inbox`。检查点包括每次写共享账本之前、每次派发或验收一批子任务之后，以及长时间等待时（至少每 5 分钟一次）。真要干等回信时，前台跑 `watch --once --seconds 600`，来信即返回，超时返回码 3。

`watch` 的事件：
- `MAIL`：写给你；`MAIL-SHARED`：共享箱未点名；`MAIL-ALL`：广播。
- `MENTION`：别的箱里点名你，需加 `--mentions`。
- `ROSTER`：你的角色或收件箱变了。
- `LEDGER`：总账里含你标记的新行，需加 `--ledger … --marker …`；`JOURNAL`：JSONL 工作日志新行，需加 `--journal <glob>`。
- `WAITFILE`：等的文件出现了。
- `TALK`：通话里的新消息（附全文）；`NOTIFY`：新通知。命令行 `watch` 报过的就算看过。
- `STATUS-DUE`：该报状态了；`WAIT-STALE`：等太久；`HEARTBEAT`：有未完成任务却长时间无事。

已报过的不会重报。各客户端的具体接法见 `references/harnesses.md`（可选阅读）。

## 8. 任务、备忘、状态板（会话重启后从这里续）

```bash
mailbox.py ... --as <你> tasks set T-123 --status open --next "等 r2 交件后独审"
mailbox.py ... --as <你> tasks waiting-on --what "主控对 Q1–Q7 的裁定"     # 在等谁；--clear 清除
mailbox.py ... --as <你> tasks wait-add --path D:/run/RESULT.json --note "执行结果"   # 文件出现即唤醒
mailbox.py ... --as <你> notes append --text "已交 T-123；下一步做判读"    # 续接备忘（notes show 查看）
mailbox.py ... --as <你> status put RUN-W1 --file status.json            # 长任务实时快照，整文件原子替换；status get 查看
```

## 9. 初始化与花名册：主控唯一，助手可增减（主控、用户或有 roster.admin 的人能改）

```bash
mailbox.py --mailbox D:/x/mailbox init --controller lead --model gpt-5.5 --harness any --as user   # 新信箱
mailbox.py --mailbox D:/x/mailbox init --from-config examples/config.example.json --as user        # 或按现成花名册
mailbox.py ... --as <主控> roster add kimi-1 --model kimi-k2 --harness any                # 加助手（默认独占收件箱 kimi-1）
mailbox.py ... --as <主控> roster add claude-b --model claude-sonnet-5-5 --box claude      # 与别的助手共用 claude 箱
mailbox.py ... --as <主控> roster remove kimi-1                                           # 移除（改为前任，历史信保留）
mailbox.py ... --as <主控> roster limit 4                                                 # 在任助手上限
mailbox.py ... --as <主控> roster handover claude-a --old-role former                      # 交接主控：新主控接手主控箱，继承已读
mailbox.py ... --as <主控> roster policy --assistant-to-assistant cc-controller           # 助手互通：allow / cc-controller / deny
mailbox.py ... --as <你>   roster update <你> --session 01a1xxxx --model ...              # 本人可改自己的 model/harness/session/note
```

任何时刻花名册里**恰有一名在任主控**，违反这一条的改动会被拒绝。主控箱按角色归属：交接后新主控收主控箱的全部来信，箱名不变。先 `roster add` 再写登记信，新助手第一次 `inbox` 就能看到。

新模型也可以自己申请加入，排队等主控批准：

```bash
mailbox.py ... --as glm-1 join --model glm-4.6 --note "擅长写测试" --wait 600    # 申请；MCP 里启动时加 --join 即可
mailbox.py ... --as <主控> roster requests                                     # 待批的申请
mailbox.py ... --as <主控> roster approve glm-1 --allow talk:lead --deny broadcast   # 批准，可顺带授权；roster reject glm-1 --reason …
```

## 10. 权限：主控按人分配

```bash
mailbox.py ... perm list                                   # 权限目录与角色预设
mailbox.py ... --as <你> perm show                         # 本方有效权限（预设 + 授予 − 收回）
mailbox.py ... --as <主控> perm grant kimi-1 broadcast queue.admin:review   # 授予
mailbox.py ... --as <主控> perm revoke kimi-1 talk:claude-a                 # 收回（压过预设）
mailbox.py ... --as <主控> perm reset kimi-1                                # 回到角色预设
mailbox.py ... --as <主控> perm preset assistant --remove broadcast         # 改整个角色的预设
mailbox.py ... --as <主控> perm token lead --out ~/.lead.token              # 身份令牌：防止别人用 --as 冒充（另见 perm token user）
```

- 权限写作「键[:范围]」，范围可以是某人、角色组（`@controller`、`@assistants`、`@observers`）或队列名：
  - 键：`send`、`broadcast`、`notify`、`talk`、`talk.audit`、`queue.push`、`queue.take`、`queue.admin`、`status`、`roster.admin`。
- 主控与用户永远拥有全部权限。助手默认能写信、通知、通话、投任务、领任务、写状态板，不能广播。旁听者默认只能旁听通话。
- 默认预设由通信策略推出：`policy` 设成助手互通 `deny`，预设就只剩对主控和旁听者的权限；设成广播 `anyone`，预设就带上 `broadcast`。
- 判定：显式条目里取最具体的一条（点名 > 角色组 > 不限），同样具体时收回优先；没有显式条目才看角色预设。
- **被拒时不要换身份、换工具或手写文件绕过去**：如实告诉主控需要什么权限，由主控决定。

## 11. 体检与出错

- `doctor`：只读体检（花名册、命名、同号多信、CR 和损坏字、残留锁、投递后被改动的信）。
- 退出码：0 成功；1 失败；2 用法、花名册或权限问题；3 等待超时或没有可取的（`watch` 无事、呼叫没等到回复、来电队列空、队列没有可领的任务）；4 `watch` 被另一进程接管；5 已有同名监视且加了 `--no-takeover`。
- 发信报错时不要换途径硬写文件：按提示修好草稿（损坏字、CR、凭据、标题点名）再重投。

## 参考

- `references/mcp.md`：MCP 服务：启动参数（含 `--init`、`--join`）、各客户端配置、19 个工具、资源与提示词、推送与阻塞等待。
- `references/protocol.md`：目录与文件命名、编号、信头格式、路由、广播、状态板、通知流、通话、队列、加入申请、权限判定、控制目录、与旧脚本共存。
- `references/roles.md`：主控与助手的职责、写信与通话纪律、领活与授权的规矩。
- `references/commands.md`：全部命令和参数（由 `--help` 生成）。
- `references/harnesses.md`：可选，各常见客户端的等信方式。
- `templates/`：登记、回执、交付、状态、紧急、更正、交接、阻断、助手调整、取舍提问等信件模板（`mailbox.py template <名>` 打印）。
- `examples/config.example.json`：一主控三助手的花名册示例。
- `adapters/`：可选的客户端外壳（目前有 Pi 扩展），只是调用同一脚本，不装也不影响任何功能。
