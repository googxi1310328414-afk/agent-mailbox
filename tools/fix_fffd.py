"""把文件里紧跟在指定前文后的一串 U+FFFD 换成正确的字；要求恰好命中一处，否则不改。
用法：python -I -B fix_fffd.py <文件> <前文> <正字>
"""
import io
import re
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    path, before, good = sys.argv[1], sys.argv[2], sys.argv[3]
    s = io.open(path, encoding="utf-8", newline="").read()
    bad = chr(0xFFFD)
    pat = re.compile(re.escape(before) + "(" + re.escape(bad) + "+)")
    hits = list(pat.finditer(s))
    if len(hits) != 1:
        print("命中 %d 处，未改：%s" % (len(hits), path))
        return 1
    m = hits[0]
    s = s[:m.start(1)] + good + s[m.end(1):]
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("已修 1 处（%d 个损坏字 → %s），文件里剩余 U+FFFD %d 个" % (len(m.group(1)), good, s.count(bad)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
