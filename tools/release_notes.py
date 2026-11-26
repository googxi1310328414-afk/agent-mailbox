"""从 CHANGELOG.md 取出某一版的说明，附上压缩包的 SHA-256，输出 Release 说明（发版工作流用）。
用法：python -I -B release_notes.py <版本号> <压缩包的 .sha256 文件>
找不到这一版的说明、或 .sha256 文件不对时退出码 1，免得发出空说明或错的校验值。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def section(text, ver):
    """「## <版本号>」到下一个「## 」之间的内容。"""
    out, inside = [], False
    for line in text.split("\n"):
        m = re.match(r"^## (\S+)\s*$", line)
        if m:
            if inside:
                break
            inside = m.group(1) == ver
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip("\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 3:
        sys.stderr.write("用法：release_notes.py <版本号> <压缩包的 .sha256 文件>\n")
        return 2
    ver, sha_file = sys.argv[1], sys.argv[2]
    body = section(io.open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read(), ver)
    if not body:
        sys.stderr.write("CHANGELOG.md 里没有「## %s」这一节\n" % ver)
        return 1
    fields = io.open(sha_file, encoding="utf-8").read().split()
    if len(fields) != 2 or not re.match(r"^[0-9a-f]{64}$", fields[0]) or fields[1] != "agent-mailbox-%s.zip" % ver:
        sys.stderr.write("%s 不是 agent-mailbox-%s.zip 的校验文件\n" % (sha_file, ver))
        return 1
    print(body + "\n\n### 校验\n\n- 压缩包 sha256：`%s`（同见 `.sha256` 文件）。\n"
                 "- 包内的 `MANIFEST.json` 逐个列出文件的 SHA-256。" % fields[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
