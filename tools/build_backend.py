"""agent-mailbox 的打包后端（PEP 517，只用标准库）：把 agent-mailbox/ 目录打成 Python 包 agent_mailbox。

- 发行名 agent-mailbox-mcp，导入名 agent_mailbox；命令 agent-mailbox-mcp（MCP 服务）与 agent-mailbox（命令行）。
- 包里的布局与 skill 目录一致（scripts/、references/、templates/、SKILL.md……），装好后模板、文档、install 命令照常可用。
- 版本号取自 scripts/mailbox.py 的 VERSION；长说明取自 README.md，相对链接改成指向 GitHub 的绝对链接。
- 条目按路径排序、时间戳固定，同一份源码每次打出的 wheel 与 sdist 逐字节相同；从 sdist 再打的 wheel 也相同。

pyproject.toml 用 backend-path 指向本目录，pip、uv、build 都能直接用它构建。
也可以直接运行：python -I -B tools/build_backend.py [输出目录]      默认输出到 ../dist-py/
"""
import base64
import calendar
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "agent-mailbox")
DIST_NAME = "agent-mailbox-mcp"
NORM_NAME = "agent_mailbox_mcp"
PACKAGE = "agent_mailbox"
REPO = "https://github.com/googxi1310328414-afk/agent-mailbox"
STAMP = (2026, 10, 26, 0, 0, 0)
EPOCH = calendar.timegm(STAMP + (0, 0, 0))
SDIST_EXTRA = ("pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md", "tools/build_backend.py")
SUMMARY = ("多模型协作 MCP 服务：通知、队列等待、多模型自由通话、主控分配权限 · "
           "Multi-agent collaboration MCP server, zero dependencies")
CLASSIFIERS = [
    "Development Status :: 5 - Production/Stable",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
] + ["Programming Language :: Python :: 3.%d" % v for v in range(8, 14)] + [
    "Topic :: Communications",
    "Topic :: Software Development :: Libraries",
]


def read(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def version():
    s = read("agent-mailbox/scripts/mailbox.py").decode("utf-8")
    return re.search(r'^VERSION = "([^"]+)"', s, re.M).group(1)


def skill_files():
    """skill 目录里要发出去的文件（与 tools/package.py 同一规则），按相对路径排序。"""
    out = []
    for d, dirs, fs in os.walk(SRC):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(fs):
            if f.endswith(".pyc") or f == "INSTALL.json":
                continue
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out.append((os.path.relpath(p, SRC).replace(os.sep, "/"), fh.read()))
    return sorted(out)


def readme_for_index():
    """README 里的相对链接在 PyPI 上会失效，换成指向 GitHub 的绝对链接。"""
    text = read("README.md").decode("utf-8")

    def fix(m):
        target = m.group(1)
        if target.startswith("../../"):
            return "](%s/%s)" % (REPO, target[len("../../"):])
        return "](%s/blob/main/%s)" % (REPO, target)
    return re.sub(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", fix, text)


def metadata(ver):
    lines = ["Metadata-Version: 2.1", "Name: " + DIST_NAME, "Version: " + ver, "Summary: " + SUMMARY,
             "Keywords: mcp,model-context-protocol,multi-agent,llm,agents,mailbox", "License: MIT",
             "Project-URL: Homepage, " + REPO, "Project-URL: Source, " + REPO,
             "Project-URL: Changelog, %s/blob/main/CHANGELOG.md" % REPO,
             "Project-URL: Issues, %s/issues" % REPO]
    lines += ["Classifier: " + c for c in CLASSIFIERS]
    lines += ["Requires-Python: >=3.8", "Description-Content-Type: text/markdown", "", readme_for_index()]
    return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")


def dist_info(ver):
    return "%s-%s.dist-info" % (NORM_NAME, ver)


def metadata_files(ver):
    wheel = ("Wheel-Version: 1.0\nGenerator: agent-mailbox build_backend\nRoot-Is-Purelib: true\n"
             "Tag: py3-none-any\n").encode("utf-8")
    entry = ("[console_scripts]\n"
             "agent-mailbox = agent_mailbox.scripts.mailbox:main\n"
             "agent-mailbox-mcp = agent_mailbox.scripts.mcp_server:main\n").encode("utf-8")
    return [("METADATA", metadata(ver)), ("WHEEL", wheel), ("entry_points.txt", entry), ("LICENSE", read("LICENSE"))]


def generated_modules(ver):
    init = ('"""agent-mailbox：多模型协作 MCP 服务与信箱。命令：agent-mailbox-mcp（MCP 服务）、agent-mailbox（命令行）。"""\n'
            '__version__ = "%s"\n' % ver).encode("utf-8")
    main = ('"""python -m agent_mailbox：等同于 agent-mailbox 命令行。"""\n'
            "import sys\n\nfrom agent_mailbox.scripts.mailbox import main\n\nsys.exit(main())\n").encode("utf-8")
    return [("__init__.py", init), ("__main__.py", main)]


def record_line(path, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return "%s,sha256=%s,%d" % (path, digest, len(data))


def zip_info(name, mode=0o644):
    zi = zipfile.ZipInfo(name, STAMP)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = (0o100000 | mode) << 16
    return zi


def wheel_entries(ver):
    di = dist_info(ver)
    entries = [(PACKAGE + "/" + n, d) for n, d in generated_modules(ver)]
    entries += [(PACKAGE + "/" + rel, data) for rel, data in skill_files()]
    entries.sort()
    entries += [(di + "/" + n, d) for n, d in metadata_files(ver)]
    record = "\n".join([record_line(n, d) for n, d in entries] + [di + "/RECORD,,"]) + "\n"
    entries.append((di + "/RECORD", record.encode("utf-8")))
    return entries


# ---------------------------------------------------------------- PEP 517 钩子
def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    ver = version()
    target = os.path.join(metadata_directory, dist_info(ver))
    os.makedirs(target, exist_ok=True)
    for name, data in metadata_files(ver):
        with open(os.path.join(target, name), "wb") as f:
            f.write(data)
    return dist_info(ver)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    ver = version()
    name = "%s-%s-py3-none-any.whl" % (NORM_NAME, ver)
    os.makedirs(wheel_directory, exist_ok=True)
    path = os.path.join(wheel_directory, name)
    with zipfile.ZipFile(path + ".tmp", "w") as z:
        for arc, data in wheel_entries(ver):
            z.writestr(zip_info(arc), data)
    os.replace(path + ".tmp", path)
    return name


def build_sdist(sdist_directory, config_settings=None):
    ver = version()
    base = "%s-%s" % (NORM_NAME, ver)
    files = [("PKG-INFO", metadata(ver))] + [(rel, read(rel)) for rel in SDIST_EXTRA]
    files += [("agent-mailbox/" + rel, data) for rel, data in skill_files()]
    files.sort()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel, data in files:
            ti = tarfile.TarInfo(base + "/" + rel)
            ti.size, ti.mtime, ti.mode = len(data), EPOCH, 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tar.addfile(ti, io.BytesIO(data))
    name = base + ".tar.gz"
    os.makedirs(sdist_directory, exist_ok=True)
    path = os.path.join(sdist_directory, name)
    with open(path + ".tmp", "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as gz:
            gz.write(raw.getvalue())
    os.replace(path + ".tmp", path)
    return name


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    out = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "dist-py")
    for name in (build_wheel(out), build_sdist(out)):
        with open(os.path.join(out, name), "rb") as f:
            data = f.read()
        print("已构建 %s｜%d 字节｜sha256 %s" % (os.path.join(out, name), len(data), hashlib.sha256(data).hexdigest()))


if __name__ == "__main__":
    main()
