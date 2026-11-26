"""复验 Python 包：在全新的虚拟环境里离线装上 wheel，再用装好的命令走一遍。
用法：python -I -B verify_wheel.py <wheel 文件>

- 核对 RECORD 里每个文件的 SHA-256 与大小；
- agent-mailbox --version、python -m agent_mailbox --version、agent-mailbox-mcp --version 三者一致；
- agent-mailbox template（模板随包分发）与 agent-mailbox selftest（全流程自检）；
- agent-mailbox-mcp --init 起一个 MCP 服务：握手、19 个工具、10 个提示词、读 mailbox://doc/protocol。
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile


def check_record(path):
    problems = []
    with zipfile.ZipFile(path) as z:
        rec = [n for n in z.namelist() if n.endswith(".dist-info/RECORD")]
        if len(rec) != 1:
            return ["wheel 里应恰有一个 RECORD"]
        listed = set()
        for line in z.read(rec[0]).decode("utf-8").splitlines():
            name, digest, size = line.rsplit(",", 2)
            listed.add(name)
            if name == rec[0]:
                continue
            data = z.read(name)
            want = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            if digest != want or int(size) != len(data):
                problems.append("RECORD 与内容不符：" + name)
        extra = set(z.namelist()) - listed
        if extra:
            problems.append("RECORD 漏列：%s" % sorted(extra))
    return problems


def exe(env_dir, name):
    for sub in ("bin", "Scripts"):
        for suffix in ("", ".exe"):
            p = os.path.join(env_dir, sub, name + suffix)
            if os.path.isfile(p):
                return p
    raise SystemExit("虚拟环境里找不到 %s" % name)


def mcp_check(server, mailbox):
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "verify-wheel", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "mailbox://doc/protocol"}}]
    p = subprocess.run([server, "--mailbox", mailbox, "--as", "lead", "--init", "--no-watch"],
                       input="".join(json.dumps(r) + "\n" for r in reqs).encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    byid = {}
    for line in p.stdout.decode("utf-8").splitlines():
        m = json.loads(line)  # 不是 JSON 就说明 stdout 混进了别的内容，直接报错
        byid[m.get("id")] = m
    tools = len(byid[2]["result"]["tools"])
    prompts = len(byid[3]["result"]["prompts"])
    doc = byid[4]["result"]["contents"][0]["text"]
    ok = byid[1]["result"]["serverInfo"]["name"] == "agent-mailbox" and tools == 19 and prompts == 10 and "投递算法" in doc
    return ok, "工具 %d 个、提示词 %d 个、协议文档 %d 字" % (tools, prompts, len(doc))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    wheel = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="amb-wheel-")
    fails = []

    def step(label, ok, detail=""):
        print("%s：%s%s" % (label, "通过" if ok else "失败", ("｜" + detail) if detail else ""))
        if not ok:
            fails.append(label)
    try:
        problems = check_record(wheel)
        step("RECORD 核对", not problems, "；".join(problems))
        env_dir = os.path.join(work, "env")
        venv.create(env_dir, with_pip=True)
        py = exe(env_dir, "python")
        p = subprocess.run([py, "-m", "pip", "install", "--no-index", "--no-deps", "-q", wheel],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        step("离线安装 wheel", p.returncode == 0, p.stdout.decode("utf-8", "replace").strip()[-300:])
        if p.returncode != 0:
            return 1
        cli, server = exe(env_dir, "agent-mailbox"), exe(env_dir, "agent-mailbox-mcp")
        out = subprocess.run([cli, "--version"], stdout=subprocess.PIPE, timeout=60).stdout.decode("utf-8").strip()
        out2 = subprocess.run([py, "-m", "agent_mailbox", "--version"], stdout=subprocess.PIPE, timeout=60).stdout.decode("utf-8").strip()
        out3 = subprocess.run([server, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              timeout=60).stdout.decode("utf-8").strip()
        step("agent-mailbox --version、python -m agent_mailbox 与 agent-mailbox-mcp --version",
             out.startswith("agent-mailbox ") and out == out2 == out3, out)
        out = subprocess.run([cli, "template"], stdout=subprocess.PIPE, timeout=60).stdout.decode("utf-8")
        step("模板随包分发", "registration" in out and "handover" in out, out.strip()[:120])
        p = subprocess.run([cli, "selftest"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        lines = p.stdout.decode("utf-8", "replace").strip().splitlines()
        step("装好后跑 selftest", p.returncode == 0, lines[-1] if lines else "")
        ok, detail = mcp_check(server, os.path.join(work, "mailbox"))
        step("agent-mailbox-mcp --init 起服务并握手", ok, detail)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("wheel 复验%s" % ("通过" if not fails else "未通过：" + "；".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
