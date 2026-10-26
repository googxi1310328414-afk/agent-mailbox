"""落盘自检：扫描目录下全部文本文件，报告 U+FFFD 损坏字、CR、BOM 所在行。
用法：python -I -B check_text.py <目录或文件>...    有问题时退出码 1。
"""
import os
import sys

BAD = chr(0xFFFD)
INVISIBLE = [chr(c) for c in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2028, 0x2029)]
EXTS = (".md", ".py", ".ts", ".js", ".cjs", ".json", ".txt", ".sh", ".ps1", ".yml", ".yaml", ".toml")


def files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for d, dirs, fs in os.walk(p):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", "node_modules", ".git")]
            for f in fs:
                if f.endswith(EXTS):
                    yield os.path.join(d, f)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    n = 0
    for p in files(sys.argv[1:]):
        n += 1
        raw = open(p, "rb").read()
        probs = []
        if raw.startswith(b"\xef\xbb\xbf"):
            probs.append("BOM")
        if b"\r" in raw:
            probs.append("CR x%d" % raw.count(b"\r"))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            probs.append("非 UTF-8：%s" % e)
            text = ""
        lines = [i + 1 for i, l in enumerate(text.split("\n")) if BAD in l]
        if lines:
            probs.append("U+FFFD 于第 %s 行" % ",".join(map(str, lines)))
        hidden = [i + 1 for i, l in enumerate(text.split("\n")) if any(c in l for c in INVISIBLE)]
        if hidden:
            probs.append("不可见字符（U+FEFF/U+200B 等）于第 %s 行" % ",".join(map(str, hidden)))
        if probs:
            bad += 1
            print("问题 %s：%s" % (p, "；".join(probs)))
    print("检查 %d 个文件，有问题 %d 个" % (n, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
