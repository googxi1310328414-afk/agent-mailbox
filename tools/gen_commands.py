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
    (["roster", "remove"], "roster remove：移除助手"),
    (["roster", "handover"], "roster handover：交接主控"),
    (["roster", "limit"], "roster limit：在任助手上限"),
    (["roster", "policy"], "roster policy：通信策略"),
    (["send"], "send：投递"),
    (["inbox"], "inbox：未读与列表"),
    (["read"], "read：读信"),
    (["mark"], "mark：标已读或未读"),
    (["search"], "search：搜信"),
    (["watch"], "watch：监视"),
    (["tasks"], "tasks：任务、等待对象、等待文件"),
    (["notes"], "notes：续接备忘"),
    (["status"], "status：状态板"),
    (["who"], "who：在线与进度"),
    (["doctor"], "doctor：体检"),
    (["next"], "next：下一个空号"),
    (["template"], "template：信件模板"),
    (["install"], "install：安装到各宿主"),
    (["selftest"], "selftest：自检"),
]

HEAD = """# 命令参考

本文件由 `tools/gen_commands.py` 从 `mailbox.py --help` 机械生成，参数以脚本为准。

通用选项（`--mailbox`、`--config`、`--state-dir`、`--as`、`--read-only`、`--json`）既可写在子命令前，也可写在子命令后。

退出码：0 成功；1 失败；2 用法或花名册问题；3 `watch` 超时无事；4 `watch` 被另一进程接管；5 已有同名监视且加了 `--no-takeover`。
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
