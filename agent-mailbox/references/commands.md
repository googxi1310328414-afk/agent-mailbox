# 命令参考

本文件由 `tools/gen_commands.py` 从 `mailbox.py --help` 机械生成，参数以脚本为准。

通用选项（`--mailbox`、`--config`、`--state-dir`、`--as`、`--read-only`、`--json`）既可写在子命令前，也可写在子命令后。

退出码：0 成功；1 失败；2 用法或花名册问题；3 `watch` 超时无事；4 `watch` 被另一进程接管；5 已有同名监视且加了 `--no-takeover`。

## 总览与通用选项

```text
usage: mailbox.py [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME] [--read-only]
                  [--json] [--version]
                  <命令> ...

agent-mailbox 1.0.0：多模型协作信箱（主控唯一，助手可增减）

positional arguments:
  <命令>
    init                初始化信箱：建控制目录与花名册（指定唯一主控）
    use                 设定或查看本机默认信箱
    roster              花名册：查看、增减助手、交接主控
    send                投递一封信（排他编号、现取时间、读回 SHA）
    inbox               列出本方未读（--all 列全部）
    read                读信并标为已读
    mark                标为已读（--unread 反过来标为未读，下次 watch 会再报）
    search              按正则搜信
    watch               监视来信与事件；--once 时有事件即退出（配合后台任务唤醒）
    tasks               本方任务清单、等待对象、等待文件
    notes               本方续接备忘（会话重启后从这里续）
    status              状态板：长任务的实时快照（原子替换的 JSON）
    who                 在线与进度总览：谁在监视、未读、上次发信、任务
    doctor              只读体检：花名册、命名、撞号、卫生、锁、投递后改动
    next                查看下一个空号（不占号）
    template            打印信件模板
    install             把本 skill 复制到各宿主的 skills 目录
    selftest            在临时目录里跑一遍全流程自检（不碰真实信箱）

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## init：初始化信箱

```text
usage: mailbox.py init [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--read-only] [--json] [--controller CONTROLLER] [--model MODEL] [--harness HARNESS]
                       [--session SESSION] [--display DISPLAY] [--box BOX] [--alias ALIAS] [--thread THREAD]
                       [--controller-box CONTROLLER_BOX] [--broadcast-box BROADCAST_BOX] [--tz TZ]
                       [--tz-label TZ_LABEL] [--max-assistants MAX_ASSISTANTS] [--from-config FROM_CONFIG]

初始化信箱：建控制目录与花名册（指定唯一主控）

options:
  -h, --help            show this help message and exit
  --controller CONTROLLER
                        主控名
  --model MODEL
  --harness HARNESS
  --session SESSION
  --display DISPLAY
  --box BOX             主控的个人收件箱（可选）
  --alias ALIAS
  --thread THREAD       8 位十六进制线程号（默认随机）
  --controller-box CONTROLLER_BOX
  --broadcast-box BROADCAST_BOX
  --tz TZ
  --tz-label TZ_LABEL
  --max-assistants MAX_ASSISTANTS
  --from-config FROM_CONFIG
                        用现成的花名册 JSON 初始化

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## use：本机默认信箱

```text
usage: mailbox.py use [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                      [--read-only] [--json] [--clear]

设定或查看本机默认信箱

options:
  -h, --help            show this help message and exit
  --clear

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster：花名册

```text
usage: mailbox.py roster [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--read-only] [--json]
                         <动作> ...

花名册：查看、增减助手、交接主控

positional arguments:
  <动作>
    show                查看花名册
    add                 加入参与方（主控或用户）
    update              改参与方信息（本人可改 model/harness/session/note）
    remove              移除助手（改为前任，历史保留）
    handover            交接主控（主控唯一）
    limit               设在任助手上限
    policy              查看或改通信策略

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster add：加入参与方

```text
usage: mailbox.py roster add [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--read-only] [--json] [--role {assistant,observer}] [--model MODEL]
                             [--harness HARNESS] [--session SESSION] [--display DISPLAY] [--box BOX]
                             [--alias ALIAS] [--note NOTE]
                             name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --role {assistant,observer}
  --model MODEL
  --harness HARNESS
  --session SESSION
  --display DISPLAY
  --box BOX             收件箱（默认用名字；多方可共用一箱，靠标题点名区分）
  --alias ALIAS
  --note NOTE

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster update：改参与方信息

```text
usage: mailbox.py roster update [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--read-only] [--json] [--display DISPLAY] [--model MODEL]
                                [--harness HARNESS] [--session SESSION] [--box BOX] [--note NOTE]
                                [--alias-add ALIAS_ADD] [--alias-remove ALIAS_REMOVE]
                                [--activate {assistant,observer}]
                                name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --display DISPLAY
  --model MODEL
  --harness HARNESS
  --session SESSION
  --box BOX
  --note NOTE
  --alias-add ALIAS_ADD
  --alias-remove ALIAS_REMOVE
  --activate {assistant,observer}
                        让前任重新在任

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster remove：移除助手

```text
usage: mailbox.py roster remove [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--read-only] [--json] [--note NOTE]
                                name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --note NOTE

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster handover：交接主控

```text
usage: mailbox.py roster handover [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR]
                                  [--as ME] [--read-only] [--json] [--old-role {former,assistant,observer}]
                                  [--old-box OLD_BOX] [--no-inherit]
                                  name

positional arguments:
  name                  新主控（须已在花名册）

options:
  -h, --help            show this help message and exit
  --old-role {former,assistant,observer}
  --old-box OLD_BOX     旧主控转助手时用的收件箱
  --no-inherit          新主控不继承旧主控的主控箱已读记录

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster limit：在任助手上限

```text
usage: mailbox.py roster limit [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                               [--read-only] [--json]
                               n

positional arguments:
  n

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster policy：通信策略

```text
usage: mailbox.py roster policy [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--read-only] [--json] [--assistant-to-assistant {allow,cc-controller,deny}]
                                [--broadcast {controller,anyone}] [--inactive-send {deny,allow}]

options:
  -h, --help            show this help message and exit
  --assistant-to-assistant {allow,cc-controller,deny}
  --broadcast {controller,anyone}
  --inactive-send {deny,allow}

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## send：投递

```text
usage: mailbox.py send [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--read-only] [--json] [--to TO] [--subject SUBJECT] [--draft DRAFT |
                       --body-file BODY_FILE | --stdin | --body BODY] [--urgent] [--reply-to REPLY_TO]
                       [--corrects CORRECTS] [--attach ATTACH] [--start START] [--normalize] [--keep-header]
                       [--force-title] [--allow-secret] [--allow-inactive] [--dry-run]

投递一封信（排他编号、现取时间、读回 SHA）

options:
  -h, --help            show this help message and exit
  --to TO               收件人：参与方名/别名、controller、all、assistants，可逗号分隔；助手默认写给主控
  --subject SUBJECT     主题（与 --draft 自带标题二选一）
  --draft DRAFT         完整草稿文件（首行可为「# 致 X：主题」）
  --body-file BODY_FILE
                        正文文件
  --stdin               正文从标准输入读
  --body BODY           短正文（长正文请用文件，避免 shell 转义）
  --urgent              紧急：文件名带 -URGENT
  --reply-to REPLY_TO   回复哪封（941、claude-941 或文件名）
  --corrects CORRECTS   更正哪封（原件不改，另函更正）
  --attach ATTACH       附件文件：投递时实算字节数与 SHA-256 列在信末
  --start START         起始编号：取不小于它的第一个空号（兼容按起始号投递的旧脚本）
  --normalize           自动去 BOM、CRLF 换成 LF
  --keep-header         保留草稿自带的「发件：」信头
  --force-title         不检查标题是否点名收件人
  --allow-secret        确认不是凭据时放行疑似凭据
  --allow-inactive      允许投给不在任者的旧收件箱
  --dry-run             只渲染预览，不投递

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## inbox：未读与列表

```text
usage: mailbox.py inbox [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--read-only] [--json] [--all] [--others] [--paths] [--box BOX] [--since SINCE]
                        [--limit LIMIT]

列出本方未读（--all 列全部）

options:
  -h, --help            show this help message and exit
  --all
  --others              也列共享箱里点名给别人的信
  --paths               每行附完整路径
  --box BOX             只看某个收件箱
  --since SINCE         只看编号大于它的信
  --limit LIMIT

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## read：读信

```text
usage: mailbox.py read [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--read-only] [--json] [--peek]
                       refs [refs ...]

读信并标为已读

positional arguments:
  refs                  941、claude-941、codex-338-URGENT 或完整文件名

options:
  -h, --help            show this help message and exit
  --peek                只看不标已读

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## mark：标已读或未读

```text
usage: mailbox.py mark [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--read-only] [--json] [--all] [--from-num FROM_NUM] [--box BOX] [--unread]
                       [refs ...]

标为已读（--unread 反过来标为未读，下次 watch 会再报）

positional arguments:
  refs

options:
  -h, --help            show this help message and exit
  --all                 本方各箱全部
  --from-num FROM_NUM   本方各箱里编号不小于它的全部（可配 --box）
  --box BOX
  --unread              标为未读

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## search：搜信

```text
usage: mailbox.py search [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--read-only] [--json] [--box BOX] [--from SENDER] [--since SINCE] [--title-only]
                         [-i] [--limit LIMIT]
                         pattern

按正则搜信

positional arguments:
  pattern

options:
  -h, --help            show this help message and exit
  --box BOX
  --from SENDER         只看某参与方发的信
  --since SINCE
  --title-only
  -i, --ignore-case
  --limit LIMIT

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## watch：监视

```text
usage: mailbox.py watch [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--read-only] [--json] [--once] [--seconds SECONDS] [--interval INTERVAL]
                        [--coalesce COALESCE] [--others {digest,trigger,off}] [--mentions] [--ledger LEDGER]
                        [--marker MARKER] [--journal JOURNAL] [--journal-types JOURNAL_TYPES]
                        [--wait-file WAIT_FILE] [--wait-from WAIT_FROM] [--remind-status REMIND_STATUS]
                        [--remind-wait REMIND_WAIT] [--nudge NUDGE] [--no-takeover] [--no-rules]

监视来信与事件；--once 时有事件即退出（配合后台任务唤醒）

options:
  -h, --help            show this help message and exit
  --once
  --seconds SECONDS     最长监视秒数（--once 默认 1790，循环模式默认不限）
  --interval INTERVAL   轮询间隔秒
  --coalesce COALESCE   首个事件后再等几秒合并同批事件（最长 15 秒）
  --others {digest,trigger,off}
                        共享箱里写给别人的信：附带摘要 / 也唤醒 / 忽略
  --mentions            别的箱里点名本方的信也唤醒
  --ledger LEDGER       监视总账等文件里含标记的新行
  --marker MARKER       总账标记（默认本方名与别名）
  --journal JOURNAL     监视 JSONL 工作流日志（glob）
  --journal-types JOURNAL_TYPES
  --wait-file WAIT_FILE
                        等这个文件出现
  --wait-from WAIT_FROM
                        从文件读要等的路径（每行一个，避免路径出现在命令行）
  --remind-status REMIND_STATUS
                        多少分钟没发信且有未完成任务就提醒报状态
  --remind-wait REMIND_WAIT
                        waiting-on 等多少分钟算超时（指数退避，最多 3 次）
  --nudge NUDGE         有未完成任务时多少分钟无事件发心跳（指数退避，最多 4 次）
  --no-takeover         已有同名监视在跑时拒绝启动（默认接管）
  --no-rules            事件输出不附处理规则

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## tasks：任务、等待对象、等待文件

```text
usage: mailbox.py tasks [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--read-only] [--json] [--status {open,waiting,blocked,done}] [--next NEXT]
                        [--what WHAT] [--clear] [--path PATH] [--note NOTE] [--file FILE]
                        [{show,set,remove,replace,waiting-on,wait-add,wait-remove}] [id]

本方任务清单、等待对象、等待文件

positional arguments:
  {show,set,remove,replace,waiting-on,wait-add,wait-remove}
  id

options:
  -h, --help            show this help message and exit
  --status {open,waiting,blocked,done}
  --next NEXT
  --what WHAT
  --clear
  --path PATH
  --note NOTE
  --file FILE           replace 用：JSON 数组 [{id, status, next}]（不给则读标准输入）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## notes：续接备忘

```text
usage: mailbox.py notes [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--read-only] [--json] [--text TEXT] [--file FILE]
                        [{show,append,replace,path}]

本方续接备忘（会话重启后从这里续）

positional arguments:
  {show,append,replace,path}

options:
  -h, --help            show this help message and exit
  --text TEXT
  --file FILE

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## status：状态板

```text
usage: mailbox.py status [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--read-only] [--json] [--to TO] [--box BOX] [--file FILE]
                         [{list,get,put}] [key]

状态板：长任务的实时快照（原子替换的 JSON）

positional arguments:
  {list,get,put}
  key

options:
  -h, --help            show this help message and exit
  --to TO               写给谁的箱（默认主控箱）
  --box BOX
  --file FILE

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## who：在线与进度

```text
usage: mailbox.py who [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                      [--read-only] [--json]

在线与进度总览：谁在监视、未读、上次发信、任务

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## doctor：体检

```text
usage: mailbox.py doctor [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--read-only] [--json] [--all-threads]

只读体检：花名册、命名、撞号、卫生、锁、投递后改动

options:
  -h, --help            show this help message and exit
  --all-threads

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## next：下一个空号

```text
usage: mailbox.py next [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--read-only] [--json] [--to TO] [--box BOX] [--urgent]

查看下一个空号（不占号）

options:
  -h, --help            show this help message and exit
  --to TO
  --box BOX
  --urgent

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## template：信件模板

```text
usage: mailbox.py template [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                           [--read-only] [--json]
                           [name]

打印信件模板

positional arguments:
  name

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## install：安装到各宿主

```text
usage: mailbox.py install [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                          [--read-only] [--json] [--force] [--dry-run]
                          target [target ...]

把本 skill 复制到各宿主的 skills 目录

positional arguments:
  target                agents / claude / codex / pi / dir:<目录>

options:
  -h, --help            show this help message and exit
  --force
  --dry-run

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## selftest：自检

```text
usage: mailbox.py selftest [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                           [--read-only] [--json] [--keep]

在临时目录里跑一遍全流程自检（不碰真实信箱）

options:
  -h, --help            show this help message and exit
  --keep

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```
