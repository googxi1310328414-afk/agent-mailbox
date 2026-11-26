"""Python 包（agent-mailbox-mcp）的构建测试：不联网、不装东西，只检查打出来的 wheel 与 sdist。
用法：python -I -B tests/test_packaging.py      （在虚拟环境里真正装一遍并运行，见 tools/verify_wheel.py）
"""
import base64
import hashlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_backend(root, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(root, "tools", "build_backend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = load_backend(ROOT, "amb_build_backend")


def slurp(path):
    with open(path, "rb") as f:
        return f.read()


class PackagingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="amb-pkg-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def build(self, backend, sub):
        out = os.path.join(self.dir, sub)
        return os.path.join(out, backend.build_wheel(out)), os.path.join(out, backend.build_sdist(out))

    def test_reproducible_and_complete(self):
        ver = B.version()
        mailbox_src = slurp(os.path.join(ROOT, "agent-mailbox", "scripts", "mailbox.py")).decode("utf-8")
        self.assertIn('VERSION = "%s"' % ver, mailbox_src)
        w1, s1 = self.build(B, "a")
        w2, s2 = self.build(B, "b")
        self.assertEqual(os.path.basename(w1), "agent_mailbox_mcp-%s-py3-none-any.whl" % ver)
        self.assertEqual(os.path.basename(s1), "agent_mailbox_mcp-%s.tar.gz" % ver)
        self.assertEqual(slurp(w1), slurp(w2))  # 同一份源码，逐字节相同
        self.assertEqual(slurp(s1), slurp(s2))
        with zipfile.ZipFile(w1) as z:
            names = z.namelist()
            di = "agent_mailbox_mcp-%s.dist-info/" % ver
            for need in ("agent_mailbox/__init__.py", "agent_mailbox/__main__.py", "agent_mailbox/SKILL.md",
                         "agent_mailbox/scripts/mailbox.py", "agent_mailbox/scripts/mcp_server.py",
                         "agent_mailbox/templates/registration.md", "agent_mailbox/references/protocol.md",
                         di + "METADATA", di + "WHEEL", di + "entry_points.txt", di + "RECORD"):
                self.assertIn(need, names)
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc")])
            self.assertEqual(names[-1], di + "RECORD")
            rows = [l.rsplit(",", 2) for l in z.read(di + "RECORD").decode("utf-8").splitlines()]
            self.assertEqual(sorted(r[0] for r in rows), sorted(names))
            for name, digest, size in rows:
                if name == di + "RECORD":
                    self.assertEqual((digest, size), ("", ""))
                    continue
                data = z.read(name)
                want = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
                self.assertEqual((digest, int(size)), ("sha256=" + want, len(data)), name)
            self.assertEqual(z.read("agent_mailbox/scripts/mailbox.py").decode("utf-8"), mailbox_src)
            meta = z.read(di + "METADATA").decode("utf-8")
            entry = z.read(di + "entry_points.txt").decode("utf-8")
        head, _, desc = meta.partition("\n\n")
        self.assertIn("Name: agent-mailbox-mcp", head)
        self.assertIn("Version: " + ver, head)
        self.assertIn("Requires-Python: >=3.8", head)
        self.assertNotIn("Requires-Dist", head)  # 没有任何运行依赖
        self.assertIn("# agent-mailbox", desc)
        self.assertEqual(re.findall(r"\]\((?!https?://|#|mailto:)[^)]+\)", desc), [])  # 相对链接都已改成绝对链接
        self.assertIn("agent-mailbox = agent_mailbox.scripts.mailbox:main", entry)
        self.assertIn("agent-mailbox-mcp = agent_mailbox.scripts.mcp_server:main", entry)
        meta_dir = os.path.join(self.dir, "meta")
        os.makedirs(meta_dir)
        name = B.prepare_metadata_for_build_wheel(meta_dir)
        self.assertEqual(name, di.rstrip("/"))
        self.assertEqual(slurp(os.path.join(meta_dir, name, "METADATA")).decode("utf-8"), meta)

    def test_wheel_built_from_sdist_is_identical(self):
        wheel, sdist = self.build(B, "tree")
        with tarfile.open(sdist) as t:
            members = t.getnames()
            base = members[0].split("/")[0]
            self.assertTrue(all(m.startswith(base + "/") for m in members))
            for need in ("PKG-INFO", "pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md",
                         "tools/build_backend.py", "agent-mailbox/SKILL.md", "agent-mailbox/scripts/mailbox.py"):
                self.assertIn(base + "/" + need, members)
            self.assertTrue(all(m.mtime == B.EPOCH and m.uid == 0 and m.gid == 0 for m in t.getmembers()))
            dest = os.path.join(self.dir, "unpacked")
            for m in t.getmembers():  # 逐个写出，不用 extractall（3.12 起它对路径的处理有新的告警）
                path = os.path.join(dest, *m.name.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(t.extractfile(m).read())
        from_sdist = load_backend(os.path.join(dest, base), "amb_build_backend_sdist")
        out = os.path.join(self.dir, "from-sdist")
        self.assertEqual(slurp(os.path.join(out, from_sdist.build_wheel(out))), slurp(wheel))
        self.assertEqual(B.get_requires_for_build_wheel(), [])
        self.assertEqual(B.get_requires_for_build_sdist(), [])


if __name__ == "__main__":
    unittest.main()
