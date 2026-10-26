"""把 agent-mailbox/ 打成通用 skill 压缩包（Agent Skills 格式：压缩包根下是含 SKILL.md 的同名目录）。

- 只收 skill 目录；排除 __pycache__、*.pyc、INSTALL.json。
- 条目按路径排序、时间戳固定，同一份源码每次打出的包逐字节相同。
- 包内附 MANIFEST.json（逐文件 SHA-256）；包外另写 .sha256。
- 打包前校验：SKILL.md 头部字段、全部文本文件为 UTF-8 且无 BOM/CR/U+FFFD/不可见字符。

用法：python -I -B package.py [输出目录]      默认输出到 ../dist/
"""
import hashlib
import io
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "agent-mailbox")
NAME = "agent-mailbox"
STAMP = (2026, 10, 26, 0, 0, 0)
TEXT_EXT = (".md", ".py", ".ts", ".cjs", ".js", ".json", ".txt")
BAD = [chr(c) for c in (0xFFFD, 0xFEFF, 0x200B, 0x200C, 0x200D, 0x2028, 0x2029)]


def version():
    s = io.open(os.path.join(SRC, "scripts", "mailbox.py"), encoding="utf-8").read()
    return re.search(r'^VERSION = "([^"]+)"', s, re.M).group(1)


def check_frontmatter():
    s = io.open(os.path.join(SRC, "SKILL.md"), encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", s, re.S)
    if not m:
        raise SystemExit("SKILL.md 缺少 --- 头部")
    fields = dict(re.findall(r"^([a-z-]+):\s*(.*)$", m.group(1), re.M))
    name, desc = fields.get("name", ""), fields.get("description", "")
    problems = []
    if name != NAME or not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", name) or len(name) > 64:
        problems.append("name 须为 %s（小写字母数字连字符，≤64）：%r" % (NAME, name))
    if not desc or len(desc) > 1024:
        problems.append("description 须非空且不超过 1024 字符（现 %d）" % len(desc))
    if "<" in desc or ">" in desc:
        problems.append("description 里不要有尖括号")
    extra = set(fields) - {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    if extra:
        problems.append("头部有非标准字段：%s" % sorted(extra))
    if problems:
        raise SystemExit("SKILL.md 头部不合规：" + "；".join(problems))
    return name, len(desc)


def collect():
    files = []
    for d, dirs, fs in os.walk(SRC):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(fs):
            if f.endswith(".pyc") or f == "INSTALL.json":
                continue
            p = os.path.join(d, f)
            rel = os.path.relpath(p, SRC).replace(os.sep, "/")
            data = open(p, "rb").read()
            if f.endswith(TEXT_EXT):
                if data.startswith(b"\xef\xbb\xbf") or b"\r" in data:
                    raise SystemExit("%s 带 BOM 或 CR" % rel)
                text = data.decode("utf-8")
                for ch in BAD:
                    if ch in text:
                        raise SystemExit("%s 含 U+%04X" % (rel, ord(ch)))
            files.append((rel, data))
    return sorted(files)


def info(path, mode):
    zi = zipfile.ZipInfo(path, STAMP)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3  # 按 Unix 记权限位，解压到 Linux/macOS 时脚本可执行
    zi.external_attr = (mode & 0xFFFF) << 16
    return zi


def main():
    out_dir = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    name, dlen = check_frontmatter()
    ver = version()
    files = collect()
    manifest = {"skill": NAME, "version": ver, "files": [
        {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for rel, data in files]}
    mdata = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    zpath = os.path.join(out_dir, "%s-%s.zip" % (NAME, ver))
    tmp = zpath + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(info(NAME + "/", 0o40755), b"")
        dirs = sorted(set("/".join(rel.split("/")[:i]) for rel, _ in files for i in range(1, rel.count("/") + 1)))
        for d in dirs:
            z.writestr(info("%s/%s/" % (NAME, d), 0o40755), b"")
        for rel, data in files + [("MANIFEST.json", mdata)]:
            mode = 0o100755 if rel == "scripts/mailbox.py" else 0o100644
            z.writestr(info("%s/%s" % (NAME, rel), mode), data)
    os.replace(tmp, zpath)
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        if bad:
            raise SystemExit("压缩包自检失败：%s" % bad)
        n = len([x for x in z.namelist() if not x.endswith("/")])
    digest = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    with open(zpath + ".sha256", "w", encoding="utf-8", newline="\n") as f:
        f.write("%s  %s\n" % (digest, os.path.basename(zpath)))
    sys.stdout.reconfigure(encoding="utf-8")
    print("已打包 %s｜%d 个文件（含 MANIFEST.json）｜%d 字节｜sha256 %s｜SKILL.md name=%s，description %d 字符" % (
        zpath, n, os.path.getsize(zpath), digest, name, dlen))


if __name__ == "__main__":
    main()
