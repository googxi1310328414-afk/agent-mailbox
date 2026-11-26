# 命令参考

本文件由 `tools/gen_commands.py` 从 `mailbox.py --help` 机械生成，参数以脚本为准。

通用选项（`--mailbox`、`--config`、`--state-dir`、`--as`、`--token-file`、`--read-only`、`--json`）既可写在子命令前，也可写在子命令后。

退出码：0 成功；1 失败（`join --wait` 时为申请被拒）；2 用法、花名册或权限问题；3 等待超时或没有可取的东西（`watch` 超时无事、`talk call/say/start --wait` 没等到回复、`talk next/wait` 没有来电或新消息、`queue take` 没有可领的任务、`queue push --wait` / `queue wait` 任务没结束、`join --wait` 没等到批准）；4 `watch` 被另一进程接管；5 已有同名监视且加了 `--no-takeover`。

## 总览与通用选项

```text
usage: mailbox.py [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                  [--token-file TOKEN_FILE] [--read-only] [--json] [--version]
                  <命令> ...

agent-mailbox 1.2.1：多模型协作信箱（主控唯一，助手可增减）

positional arguments:
  <命令>
    init                初始化信箱：建控制目录与花名册（指定唯一主控）
    use                 设定或查看本机默认信箱
    roster              花名册：查看、增减助手、交接主控
    perm                权限：查看；授予、收回、重置、改角色预设、发身份令牌（只有主控或用户能改）
    send                投递一封信（排他编号、现取时间、读回 SHA）
    inbox               列出本方未读（--all 列全部）
    read                读信并标为已读
    mark                标为已读（--unread 反过来标为未读，下次 watch 会再报）
    search              按正则搜信
    watch               监视来信与事件；--once 时有事件即退出（配合后台任务唤醒）
    tasks               本方任务清单、等待对象、等待文件
    notes               本方续接备忘（会话重启后从这里续）
    status              状态板：长任务的实时快照（原子替换的 JSON）
    notify              通知：给别人发一条轻量提醒（不占信件编号），或查看本方的通知
    talk                通话：多模型自由对话（一对一或多方）、同步呼叫、来电队列
    queue               工作队列：投任务、排队领取（租约、优先级、到期回收）、等结果
    join                申请加入信箱（--as 写想用的名字），排队等主控批准
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## init：初始化信箱

```text
usage: mailbox.py init [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json] [--controller CONTROLLER]
                       [--model MODEL] [--harness HARNESS] [--session SESSION] [--display DISPLAY]
                       [--box BOX] [--alias ALIAS] [--thread THREAD] [--controller-box CONTROLLER_BOX]
                       [--broadcast-box BROADCAST_BOX] [--tz TZ] [--tz-label TZ_LABEL]
                       [--max-assistants MAX_ASSISTANTS] [--from-config FROM_CONFIG]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## use：本机默认信箱

```text
usage: mailbox.py use [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                      [--token-file TOKEN_FILE] [--read-only] [--json] [--clear]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster：花名册

```text
usage: mailbox.py roster [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--token-file TOKEN_FILE] [--read-only] [--json]
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
    requests            加入申请（主控或 roster.admin）
    approve             批准加入申请（--allow/--deny 顺带授权只有主控能用）
    reject              拒绝加入申请
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster add：加入参与方

```text
usage: mailbox.py roster add [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--role {assistant,observer}]
                             [--model MODEL] [--harness HARNESS] [--session SESSION] [--display DISPLAY]
                             [--box BOX] [--alias ALIAS] [--note NOTE]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster update：改参与方信息

```text
usage: mailbox.py roster update [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--token-file TOKEN_FILE] [--read-only] [--json] [--display DISPLAY]
                                [--model MODEL] [--harness HARNESS] [--session SESSION] [--box BOX]
                                [--note NOTE] [--alias-add ALIAS_ADD] [--alias-remove ALIAS_REMOVE]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster remove：移除助手

```text
usage: mailbox.py roster remove [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--token-file TOKEN_FILE] [--read-only] [--json] [--note NOTE]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster handover：交接主控

```text
usage: mailbox.py roster handover [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR]
                                  [--as ME] [--token-file TOKEN_FILE] [--read-only] [--json]
                                  [--old-role {former,assistant,observer}] [--old-box OLD_BOX]
                                  [--no-inherit]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster limit：在任助手上限

```text
usage: mailbox.py roster limit [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                               [--token-file TOKEN_FILE] [--read-only] [--json]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster policy：通信策略

```text
usage: mailbox.py roster policy [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--token-file TOKEN_FILE] [--read-only] [--json]
                                [--assistant-to-assistant {allow,cc-controller,deny}]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster requests：加入申请

```text
usage: mailbox.py roster requests [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR]
                                  [--as ME] [--token-file TOKEN_FILE] [--read-only] [--json] [--all]

options:
  -h, --help            show this help message and exit
  --all                 连同已批、已拒的

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster approve：批准加入

```text
usage: mailbox.py roster approve [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR]
                                 [--as ME] [--token-file TOKEN_FILE] [--read-only] [--json]
                                 [--role {assistant,observer}] [--box BOX] [--allow ALLOW] [--deny DENY]
                                 [--note NOTE]
                                 name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --role {assistant,observer}
                        默认按申请
  --box BOX             收件箱（默认用名字）
  --allow ALLOW         额外授予的权限（可逗号分隔）
  --deny DENY           收回的权限（可逗号分隔）
  --note NOTE

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## roster reject：拒绝加入

```text
usage: mailbox.py roster reject [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--token-file TOKEN_FILE] [--read-only] [--json] [--reason REASON]
                                name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --reason REASON

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm：权限

```text
usage: mailbox.py perm [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json]
                       <动作> ...

权限：查看；授予、收回、重置、改角色预设、发身份令牌（只有主控或用户能改）

positional arguments:
  <动作>
    list                权限目录与各角色预设
    show                有效权限（给名字看某人；不给名字看本方，没有 --as 时列全体在任者）
    check               判定某人能否用某权限（附依据）
    grant               授予权限（同时去掉同名的收回条目）
    revoke              收回权限（显式收回，压过角色预设）
    reset               去掉显式条目，回到角色预设（不给权限则全部去掉）
    preset              查看或改角色预设
    token               给某人（或 user）设身份令牌，防止 --as 冒充

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm list：权限目录与角色预设

```text
usage: mailbox.py perm list [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json]

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm show：有效权限

```text
usage: mailbox.py perm show [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json]
                            [name]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm check：判定

```text
usage: mailbox.py perm check [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--target TARGET]
                             name perm

positional arguments:
  name
  perm                  权限键，也可写成 键:范围

options:
  -h, --help            show this help message and exit
  --target TARGET       对谁（参与方、角色组或队列名）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm grant：授予

```text
usage: mailbox.py perm grant [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json]
                             name perms [perms ...]

positional arguments:
  name
  perms                 权限，如 talk、talk:kimi-1、queue.take:review

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm revoke：收回

```text
usage: mailbox.py perm revoke [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                              [--token-file TOKEN_FILE] [--read-only] [--json]
                              name perms [perms ...]

positional arguments:
  name
  perms                 权限，如 talk、talk:kimi-1、queue.take:review

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm reset：回到角色预设

```text
usage: mailbox.py perm reset [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json]
                             name [perms ...]

positional arguments:
  name
  perms                 权限，如 talk、talk:kimi-1、queue.take:review

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm preset：角色预设

```text
usage: mailbox.py perm preset [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                              [--token-file TOKEN_FILE] [--read-only] [--json] [--set SET] [--add ADD]
                              [--remove REMOVE] [--reset]
                              {assistant,observer}

positional arguments:
  {assistant,observer}

options:
  -h, --help            show this help message and exit
  --set SET             整体改为这些权限（逗号分隔；空串表示无）
  --add ADD
  --remove REMOVE
  --reset               恢复为由通信策略推出的预设

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## perm token：身份令牌

```text
usage: mailbox.py perm token [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--clear] [--out OUT]
                             name

positional arguments:
  name                  参与方名或 user

options:
  -h, --help            show this help message and exit
  --clear               清除令牌
  --out OUT             把令牌写进这个文件（只有本人可读），而不是打印

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## send：投递

```text
usage: mailbox.py send [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json] [--to TO] [--subject SUBJECT]
                       [--draft DRAFT | --body-file BODY_FILE | --stdin | --body BODY] [--urgent]
                       [--reply-to REPLY_TO] [--corrects CORRECTS] [--attach ATTACH] [--start START]
                       [--normalize] [--keep-header] [--force-title] [--allow-secret] [--allow-inactive]
                       [--dry-run]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## inbox：未读与列表

```text
usage: mailbox.py inbox [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--token-file TOKEN_FILE] [--read-only] [--json] [--all] [--others] [--paths]
                        [--box BOX] [--since SINCE] [--limit LIMIT]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## read：读信

```text
usage: mailbox.py read [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json] [--peek]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## mark：标已读或未读

```text
usage: mailbox.py mark [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json] [--all] [--from-num FROM_NUM]
                       [--box BOX] [--unread]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## search：搜信

```text
usage: mailbox.py search [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--token-file TOKEN_FILE] [--read-only] [--json] [--box BOX] [--from SENDER]
                         [--since SINCE] [--title-only] [-i] [--limit LIMIT]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## watch：监视

```text
usage: mailbox.py watch [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--token-file TOKEN_FILE] [--read-only] [--json] [--once] [--seconds SECONDS]
                        [--interval INTERVAL] [--coalesce COALESCE] [--others {digest,trigger,off}]
                        [--mentions] [--ledger LEDGER] [--marker MARKER] [--journal JOURNAL]
                        [--journal-types JOURNAL_TYPES] [--wait-file WAIT_FILE] [--wait-from WAIT_FROM]
                        [--remind-status REMIND_STATUS] [--remind-wait REMIND_WAIT] [--nudge NUDGE]
                        [--no-takeover] [--no-rules]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## tasks：任务、等待对象、等待文件

```text
usage: mailbox.py tasks [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--token-file TOKEN_FILE] [--read-only] [--json]
                        [--status {open,waiting,blocked,done}] [--next NEXT] [--what WHAT] [--clear]
                        [--path PATH] [--note NOTE] [--file FILE]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## notes：续接备忘

```text
usage: mailbox.py notes [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--token-file TOKEN_FILE] [--read-only] [--json] [--text TEXT] [--file FILE]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## status：状态板

```text
usage: mailbox.py status [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--token-file TOKEN_FILE] [--read-only] [--json] [--to TO] [--box BOX]
                         [--file FILE]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## notify：通知

```text
usage: mailbox.py notify [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--token-file TOKEN_FILE] [--read-only] [--json]
                         <动作> ...

通知：给别人发一条轻量提醒（不占信件编号），或查看本方的通知

positional arguments:
  <动作>
    send                发通知（to 可写名字、controller、assistants、all）
    list                本方通知（默认只列没看过的，并记为已看）

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## notify send：发通知

```text
usage: mailbox.py notify send [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                              [--token-file TOKEN_FILE] [--read-only] [--json] --to TO (--text TEXT |
                              --file FILE) [--allow-secret] [--title TITLE] [--urgent]

options:
  -h, --help            show this help message and exit
  --to TO
  --text TEXT           通知内容（最多 4 KB）
  --file FILE           从文件读通知内容（最多 4 KB）（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据
  --title TITLE
  --urgent

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## notify list：看通知

```text
usage: mailbox.py notify list [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                              [--token-file TOKEN_FILE] [--read-only] [--json] [--all] [--limit LIMIT]
                              [--peek]

options:
  -h, --help            show this help message and exit
  --all                 列最近的全部（配 --limit）
  --limit LIMIT
  --peek                只看不记为已看

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk：通话

```text
usage: mailbox.py talk [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json]
                       <动作> ...

通话：多模型自由对话（一对一或多方）、同步呼叫、来电队列

positional arguments:
  <动作>
    start               发起通话（to 可写多人、assistants、all）并发第一句
    say                 在通话里发言
    call                呼叫某人：沿用与他的一对一通话（没有就新建），发言并等回复
    read                读通话记录（默认最近 50 条），并把读到的记为已看
    list                本方的通话与未读数
    queue               来电队列：等着回复的通话（紧急优先，先来后到）
    next                接下一个来电：取队首通话的未读消息并记为已看
    wait                等通话里的新消息
    invite              邀请别人加入通话
    leave               退出通话
    close               结束通话（发起人或主控）

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk start：发起通话

```text
usage: mailbox.py talk start [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] --to TO [--topic TOPIC]
                             (--text TEXT | --file FILE) [--allow-secret] [--urgent] [--wait WAIT]

options:
  -h, --help            show this help message and exit
  --to TO
  --topic TOPIC
  --text TEXT           内容（最多 16 KB）
  --file FILE           从文件读内容（最多 16 KB）（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据
  --urgent
  --wait WAIT           等对方回复的秒数（0 不等）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk say：发言

```text
usage: mailbox.py talk say [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                           [--token-file TOKEN_FILE] [--read-only] [--json] (--text TEXT | --file FILE)
                           [--allow-secret] [--reply-to REPLY_TO] [--urgent] [--wait WAIT]
                           conv

positional arguments:
  conv                  通话编号，如 c12（也可只写 12）

options:
  -h, --help            show this help message and exit
  --text TEXT           内容（最多 16 KB）
  --file FILE           从文件读内容（最多 16 KB）（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据
  --reply-to REPLY_TO   回复第几条
  --urgent
  --wait WAIT           等别人回复的秒数（0 不等）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk call：呼叫并等回复

```text
usage: mailbox.py talk call [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json] (--text TEXT | --file FILE)
                            [--allow-secret] [--topic TOPIC] [--new] [--urgent] [--wait WAIT]
                            who

positional arguments:
  who

options:
  -h, --help            show this help message and exit
  --text TEXT           内容（最多 16 KB）
  --file FILE           从文件读内容（最多 16 KB）（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据
  --topic TOPIC
  --new                 另起一个新通话
  --urgent              紧急：排到对方来电队列最前
  --wait WAIT           等回复的秒数（默认 120，0 不等）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk read：读通话

```text
usage: mailbox.py talk read [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json] [--since SINCE] [--unread]
                            [--limit LIMIT] [--peek]
                            conv

positional arguments:
  conv

options:
  -h, --help            show this help message and exit
  --since SINCE         只看第几条之后的
  --unread              只看未读
  --limit LIMIT
  --peek                只看不记为已看

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk list：本方通话

```text
usage: mailbox.py talk list [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json] [--all] [--everyone]

options:
  -h, --help            show this help message and exit
  --all                 连同已结束的
  --everyone            信箱里全部通话（要 talk.audit）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk queue：来电队列

```text
usage: mailbox.py talk queue [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--for WHO]

options:
  -h, --help            show this help message and exit
  --for WHO             看别人的（要 talk.audit）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk next：接下一个来电

```text
usage: mailbox.py talk next [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json] [--wait WAIT] [--peek]

options:
  -h, --help            show this help message and exit
  --wait WAIT           队列空时等多少秒
  --peek

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk wait：等新消息

```text
usage: mailbox.py talk wait [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json] [--timeout TIMEOUT]
                            [conv]

positional arguments:
  conv                  只等这个通话（默认本方全部通话）

options:
  -h, --help            show this help message and exit
  --timeout TIMEOUT

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk invite：邀请

```text
usage: mailbox.py talk invite [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                              [--token-file TOKEN_FILE] [--read-only] [--json] --to TO
                              conv

positional arguments:
  conv

options:
  -h, --help            show this help message and exit
  --to TO

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk leave：退出

```text
usage: mailbox.py talk leave [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json]
                             conv

positional arguments:
  conv

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## talk close：结束

```text
usage: mailbox.py talk close [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json]
                             conv

positional arguments:
  conv

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue：工作队列

```text
usage: mailbox.py queue [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                        [--token-file TOKEN_FILE] [--read-only] [--json]
                        <动作> ...

工作队列：投任务、排队领取（租约、优先级、到期回收）、等结果

positional arguments:
  <动作>
    list                全部队列与各状态任务数
    create              建队列或改设置（queue.admin）
    push                投任务（queue.push；队列不存在时有 queue.admin 会自动建）
    take                领下一个任务（queue.take）；--wait 时排队等
    done                完成（--text 写结果）
    fail                记为失败（--retry 还有次数就放回重试）
    release             做不了，放回排队（不算一次尝试）
    renew               续租
    cancel              取消（投放者或 queue.admin）
    show                队列里的任务
    job                 一个任务的详情（内容、结果、经过）
    wait                等任务结束，打印结果

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue list：全部队列

```text
usage: mailbox.py queue list [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json]

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue create：建队列、改设置

```text
usage: mailbox.py queue create [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                               [--token-file TOKEN_FILE] [--read-only] [--json] [--desc DESC]
                               [--lease LEASE] [--max-attempts MAX_ATTEMPTS] [--consumers CONSUMERS]
                               [--notify] [--no-notify]
                               name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --desc DESC
  --lease LEASE         领取后的租约秒数（默认 900，到期没完成就放回排队）
  --max-attempts MAX_ATTEMPTS
                        最多尝试几次（默认 3）
  --consumers CONSUMERS
                        领取名单（逗号分隔；空串表示不限）
  --notify              新任务通知可领的人（默认）
  --no-notify           新任务不发通知

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue push：投任务

```text
usage: mailbox.py queue push [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] --title TITLE [--text TEXT |
                             --file FILE] [--allow-secret] [--priority PRIORITY] [--to TO] [--wait WAIT]
                             name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --title TITLE
  --text TEXT           任务内容（最多 16 KB）
  --file FILE           从文件读任务内容（最多 16 KB）（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据
  --priority PRIORITY   越大越先领
  --to TO               指派给某人（只有他能领）
  --wait WAIT           等任务结束的秒数

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue take：领任务

```text
usage: mailbox.py queue take [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--wait WAIT]
                             name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --wait WAIT

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue done：完成

```text
usage: mailbox.py queue done [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--text TEXT | --file FILE]
                             [--allow-secret]
                             name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit
  --text TEXT           结果
  --file FILE           从文件读结果（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue fail：失败或重试

```text
usage: mailbox.py queue fail [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--text TEXT | --file FILE]
                             [--allow-secret] [--retry]
                             name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit
  --text TEXT           说明
  --file FILE           从文件读说明（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据
  --retry

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue release：放回排队

```text
usage: mailbox.py queue release [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                                [--token-file TOKEN_FILE] [--read-only] [--json] [--text TEXT | --file FILE]
                                [--allow-secret]
                                name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit
  --text TEXT           说明
  --file FILE           从文件读说明（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue renew：续租

```text
usage: mailbox.py queue renew [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                              [--token-file TOKEN_FILE] [--read-only] [--json] [--text TEXT | --file FILE]
                              [--allow-secret]
                              name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit
  --text TEXT           说明
  --file FILE           从文件读说明（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue cancel：取消

```text
usage: mailbox.py queue cancel [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                               [--token-file TOKEN_FILE] [--read-only] [--json] [--text TEXT | --file FILE]
                               [--allow-secret]
                               name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit
  --text TEXT           说明
  --file FILE           从文件读说明（长内容用它，免得 shell 转义）
  --allow-secret        确认不是凭据时放行疑似凭据

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue show：队列里的任务

```text
usage: mailbox.py queue show [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--all]
                             name

positional arguments:
  name

options:
  -h, --help            show this help message and exit
  --all                 列出全部已结束的（默认最近 10 个）

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue job：任务详情

```text
usage: mailbox.py queue job [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                            [--token-file TOKEN_FILE] [--read-only] [--json]
                            name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## queue wait：等任务结束

```text
usage: mailbox.py queue wait [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                             [--token-file TOKEN_FILE] [--read-only] [--json] [--timeout TIMEOUT]
                             name job

positional arguments:
  name
  job

options:
  -h, --help            show this help message and exit
  --timeout TIMEOUT

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## join：申请加入

```text
usage: mailbox.py join [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json] [--model MODEL] [--harness HARNESS]
                       [--display DISPLAY] [--note NOTE] [--role {assistant,observer}] [--wait WAIT]
                       [--status]

申请加入信箱（--as 写想用的名字），排队等主控批准

options:
  -h, --help            show this help message and exit
  --model MODEL
  --harness HARNESS
  --display DISPLAY
  --note NOTE           附言：你是谁、能做什么
  --role {assistant,observer}
  --wait WAIT           等批准的秒数
  --status              只查申请状态

通用选项:
  --mailbox, -m MAILBOX
                        信箱目录（默认取环境变量 AGENT_MAILBOX_DIR，再取 ~/.agent-mailbox/default.json）
  --config CONFIG       花名册文件（默认 <信箱>/.agent-mailbox/config.json）
  --state-dir STATE_DIR
                        控制目录（默认 <信箱>/.agent-mailbox）
  --as ME               本方参与方名（默认环境变量 AGENT_MAILBOX_ME）
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## who：在线与进度

```text
usage: mailbox.py who [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                      [--token-file TOKEN_FILE] [--read-only] [--json]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## doctor：体检

```text
usage: mailbox.py doctor [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                         [--token-file TOKEN_FILE] [--read-only] [--json] [--all-threads]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## next：下一个空号

```text
usage: mailbox.py next [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                       [--token-file TOKEN_FILE] [--read-only] [--json] [--to TO] [--box BOX] [--urgent]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## template：信件模板

```text
usage: mailbox.py template [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                           [--token-file TOKEN_FILE] [--read-only] [--json]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## install：安装到各宿主

```text
usage: mailbox.py install [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                          [--token-file TOKEN_FILE] [--read-only] [--json] [--force] [--dry-run]
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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```

## selftest：自检

```text
usage: mailbox.py selftest [-h] [--mailbox MAILBOX] [--config CONFIG] [--state-dir STATE_DIR] [--as ME]
                           [--token-file TOKEN_FILE] [--read-only] [--json] [--keep]

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
  --token-file TOKEN_FILE
                        身份令牌文件（花名册给本方设了令牌时用；也可用环境变量 AGENT_MAILBOX_TOKEN）
  --read-only           只读：不写游标、锁、在线状态，拒绝投递
  --json                输出 JSON
```
