"""由 mailbox.py 的 --help 机械生成 references/commands.md，避免手抄参数出错。
用法：python -I -B gen_commands.py
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "agent-mailbox", "scripts", "mailbox.py")
OUT = os.path.join(ROOT, "agent-mailbox", "references", "commands.md")

COMMANDS = [
    ([], "总览与通用选项"),
    (["init"], "init：初始化信箱"),
    (["use"], "use：本机默认信箱"),
    (["roster"], "roster：花名册"),
    (["roster", "add"], "roster add：加入参与方"),
    (["roster", "update"], "roster update：改参与方信息"),
    (["roster", "skills"], "roster skills：技能申报与批准"),
    (["roster", "remove"], "roster remove：移除助手"),
    (["roster", "handover"], "roster handover：交接主控"),
    (["roster", "limit"], "roster limit：在任助手上限"),
    (["roster", "policy"], "roster policy：通信策略"),
    (["roster", "requests"], "roster requests：加入申请"),
    (["roster", "approve"], "roster approve：批准加入"),
    (["roster", "reject"], "roster reject：拒绝加入"),
    (["perm"], "perm：权限"),
    (["perm", "list"], "perm list：权限目录与角色预设"),
    (["perm", "show"], "perm show：有效权限"),
    (["perm", "check"], "perm check：判定"),
    (["perm", "grant"], "perm grant：授予"),
    (["perm", "revoke"], "perm revoke：收回"),
    (["perm", "reset"], "perm reset：回到角色预设"),
    (["perm", "preset"], "perm preset：角色预设"),
    (["perm", "quota"], "perm quota：配额"),
    (["perm", "token"], "perm token：身份令牌"),
    (["send"], "send：投递"),
    (["inbox"], "inbox：未读与列表"),
    (["read"], "read：读信"),
    (["mark"], "mark：标已读或未读"),
    (["search"], "search：搜信"),
    (["watch"], "watch：监视"),
    (["tasks"], "tasks：任务、等待对象、等待文件"),
    (["notes"], "notes：续接备忘"),
    (["status"], "status：状态板"),
    (["notify"], "notify：通知"),
    (["notify", "send"], "notify send：发通知"),
    (["notify", "list"], "notify list：看通知"),
    (["talk"], "talk：通话"),
    (["talk", "start"], "talk start：发起通话"),
    (["talk", "say"], "talk say：发言"),
    (["talk", "call"], "talk call：呼叫并等回复"),
    (["talk", "read"], "talk read：读通话"),
    (["talk", "list"], "talk list：本方通话"),
    (["talk", "queue"], "talk queue：来电队列"),
    (["talk", "next"], "talk next：接下一个来电"),
    (["talk", "wait"], "talk wait：等新消息"),
    (["talk", "invite"], "talk invite：邀请"),
    (["talk", "leave"], "talk leave：退出"),
    (["talk", "close"], "talk close：结束"),
    (["talk", "pause"], "talk pause：暂停"),
    (["talk", "resume"], "talk resume：继续或改上限"),
    (["queue"], "queue：工作队列"),
    (["queue", "list"], "queue list：全部队列"),
    (["queue", "create"], "queue create：建队列、改设置"),
    (["queue", "push"], "queue push：投任务"),
    (["queue", "take"], "queue take：领任务"),
    (["queue", "progress"], "queue progress：报进度"),
    (["queue", "done"], "queue done：完成"),
    (["queue", "block"], "queue block：卡住"),
    (["queue", "unblock"], "queue unblock：解除卡住"),
    (["queue", "decline"], "queue decline：拒绝"),
    (["queue", "fail"], "queue fail：失败或重试"),
    (["queue", "release"], "queue release：放回排队"),
    (["queue", "renew"], "queue renew：续租"),
    (["queue", "cancel"], "queue cancel：取消"),
    (["queue", "show"], "queue show：队列里的任务"),
    (["queue", "job"], "queue job：任务详情"),
    (["queue", "wait"], "queue wait：等任务结束"),
    (["join"], "join：申请加入"),
    (["who"], "who：在线与进度"),
    (["doctor"], "doctor：体检"),
    (["next"], "next：下一个空号"),
    (["template"], "template：信件模板"),
    (["install"], "install：安装到各宿主"),
    (["selftest"], "selftest：自检"),
]

HEAD = """# 命令参考

本文件由 `tools/gen_commands.py` 从 `mailbox.py --help` 机械生成，参数以脚本为准。

通用选项（`--mailbox`、`--config`、`--state-dir`、`--as`、`--token-file`、`--read-only`、`--json`）既可写在子命令前，也可写在子命令后。

退出码：0 成功；1 失败（`join --wait` 时为申请被拒）；2 用法、花名册或权限问题；3 等待超时或没有可取的东西（`watch` 超时无事、`talk call/say/start --wait` 没等到回复、`talk next/wait` 没有来电或新消息、`queue take` 没有可领的任务、`queue push --wait` / `queue wait` 任务没结束、`join --wait` 没等到批准）；4 `watch` 被另一进程接管；5 已有同名监视且加了 `--no-takeover`。
"""


def main():
    env = dict(os.environ, COLUMNS="110")
    parts = [HEAD]
    for args, title in COMMANDS:
        p = subprocess.run([sys.executable, "-I", "-B", SCRIPT] + args + ["--help"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env)
        if p.returncode != 0:
            raise SystemExit("生成失败：%s %s" % (args, p.stderr.decode("utf-8", "replace")))
        text = p.stdout.decode("utf-8").replace("\r\n", "\n").rstrip("\n")
        parts.append("\n## %s\n\n```text\n%s\n```\n" % (title, text))
    data = "".join(parts)
    assert chr(0xFFFD) not in data and "\r" not in data
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(data)
    print("已生成 %s（%d 字符，%d 节）" % (OUT, len(data), len(COMMANDS)))


if __name__ == "__main__":
    main()
