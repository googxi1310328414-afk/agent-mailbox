---
name: agent-mailbox
license: MIT（完整条款见 LICENSE.txt）
description: 多模型协作信箱（agent mailbox）。一名主控（controller，始终唯一）带若干助手（assistant，数量可增减），经共享目录里的 Markdown 信件通信；附 MCP 服务（mailbox_send、mailbox_wait 等工具，来信推送），也可直接用命令行；不依赖特定客户端，任何能运行 Python 3.8+ 的大模型或智能体都能用。用于给主控或助手写信、收信与等待来信、开工回执、状态汇报、紧急信、更正、主控交接、增减助手、状态板、任务清单与续接备忘。触发词：信箱、写信、回信、来信、主控、助手、登记、回执、交接、mailbox、letter、inbox、controller、assistant、handover、MCP。
---

# agent-mailbox：多模型协作信箱

几个模型（或同一模型的几个会话）共用一个**信箱目录**，靠排他创建的 Markdown 文件互相写信。
主线只有一条：**主控唯一，助手可增减**。主控登记任务、裁定、验收；助手只做登记给自己的事，按时回执和汇报。

**有 MCP 就用 MCP。** 客户端里若已接上 `agent-mailbox` MCP 服务（工具名形如 `mailbox_send`、`mailbox_inbox`、`mailbox_wait`），
直接用这些工具：身份和信箱已在服务启动时绑定；等回信调 `mailbox_wait`，不要自己轮询；工具结果末尾的「【新事件】」也要先读原信再处理。
下文的命令与这些工具一一对应（`send` 对 `mailbox_send`，`inbox` 对 `mailbox_inbox`，依此类推）。接法见 `references/mcp.md`。

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
- 花名册不存在说明信箱还没初始化：由用户或主控执行 `init`（见第 6 节）。
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

## 4. 等信

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
- `STATUS-DUE`：该报状态了；`WAIT-STALE`：等太久；`HEARTBEAT`：有未完成任务却长时间无事。

已报过的不会重报。各客户端的具体接法见 `references/harnesses.md`（可选阅读）。

## 5. 任务、备忘、状态板（会话重启后从这里续）

```bash
mailbox.py ... --as <你> tasks set T-123 --status open --next "等 r2 交件后独审"
mailbox.py ... --as <你> tasks waiting-on --what "主控对 Q1–Q7 的裁定"     # 在等谁；--clear 清除
mailbox.py ... --as <你> tasks wait-add --path D:/run/RESULT.json --note "执行结果"   # 文件出现即唤醒
mailbox.py ... --as <你> notes append --text "已交 T-123；下一步做判读"    # 续接备忘（notes show 查看）
mailbox.py ... --as <你> status put RUN-W1 --file status.json            # 长任务实时快照，整文件原子替换；status get 查看
```

## 6. 初始化与花名册：主控唯一，助手可增减（只有主控或用户能改）

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

## 7. 体检与出错

- `doctor`：只读体检（花名册、命名、同号多信、CR 和损坏字、残留锁、投递后被改动的信）。
- 退出码：0 成功；1 失败；2 用法或花名册问题；3 `watch` 超时无事；4 `watch` 被另一进程接管；5 已有同名监视且加了 `--no-takeover`。
- 发信报错时不要换途径硬写文件：按提示修好草稿（损坏字、CR、凭据、标题点名）再重投。

## 参考

- `references/mcp.md`：MCP 服务：启动参数、各客户端配置、14 个工具、资源与提示词、来信推送方式。
- `references/protocol.md`：目录与文件命名、编号、信头格式、路由、广播、状态板、控制目录、与旧脚本共存。
- `references/roles.md`：主控与助手的职责和写信纪律。
- `references/commands.md`：全部命令和参数（由 `--help` 生成）。
- `references/harnesses.md`：可选，各常见客户端的等信方式。
- `templates/`：登记、回执、交付、状态、紧急、更正、交接、阻断、助手调整、取舍提问等信件模板（`mailbox.py template <名>` 打印）。
- `examples/config.example.json`：一主控三助手的花名册示例。
- `adapters/`：可选的客户端外壳（目前有 Pi 扩展），只是调用同一脚本，不装也不影响任何功能。
