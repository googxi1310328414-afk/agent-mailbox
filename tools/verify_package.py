"""复验压缩包：解到全新临时目录，核 MANIFEST 逐文件 SHA-256，再用解出来的那份跑自检、单元测试和（可选）Pi 扩展测试。
用法：python -I -B verify_package.py <压缩包> [--skip-pi]
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    zpath = os.path.abspath(sys.argv[1])
    skip_pi = "--skip-pi" in sys.argv
    work = tempfile.mkdtemp(prefix="amb-verify-")
    fails = []
    try:
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            assert all(n.startswith("agent-mailbox/") for n in names), "压缩包根下只能是 agent-mailbox/"
            z.extractall(work)
        skill = os.path.join(work, "agent-mailbox")
        man = json.load(open(os.path.join(skill, "MANIFEST.json"), encoding="utf-8"))
        listed = set()
        for f in man["files"]:
            data = open(os.path.join(skill, f["path"]), "rb").read()
            listed.add(f["path"])
            if hashlib.sha256(data).hexdigest() != f["sha256"] or len(data) != f["bytes"]:
                fails.append("清单不符：" + f["path"])
        actual = set()
        for d, _, fs in os.walk(skill):
            for fn in fs:
                actual.add(os.path.relpath(os.path.join(d, fn), skill).replace(os.sep, "/"))
        extra = actual - listed - {"MANIFEST.json"}
        if extra:
            fails.append("清单外的文件：%s" % sorted(extra))
        print("解包 %d 个文件，清单核对%s" % (len(actual), "通过" if not fails else "失败"))
        script = os.path.join(skill, "scripts", "mailbox.py")
        env = dict(os.environ, AMB_SCRIPT=script)
        steps = [
            ("自检 selftest", [sys.executable, "-I", "-B", script, "selftest"], None),
            ("单元与集成测试", [sys.executable, "-I", "-B", os.path.join(ROOT, "tests", "test_mailbox.py")], env),
            ("MCP 协议测试", [sys.executable, "-I", "-B", os.path.join(ROOT, "tests", "test_mcp.py")], env),
        ]
        node = shutil.which("node")
        if node and os.environ.get("MCP_SDK_DIR"):
            steps.append(("官方 MCP SDK 一致性", [node, os.path.join(ROOT, "tests", "sdk_conformance.cjs")], env))
        if node and not skip_pi:
            steps.append(("Pi 扩展测试", [node, os.path.join(skill, "adapters", "pi", "test-harness.cjs")], None))
        for label, cmd, env in steps:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=work, timeout=900)
            out = p.stdout.decode("utf-8", "replace").strip().splitlines()
            tail = [l for l in out if l.strip()][-1:] if out else [""]
            print("%s：%s｜%s" % (label, "通过" if p.returncode == 0 else "失败（退出码 %d）" % p.returncode, tail[0][:160]))
            if p.returncode != 0:
                fails.append(label)
                print("\n".join(out[-30:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("复验%s" % ("通过" if not fails else "未通过：" + "；".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
