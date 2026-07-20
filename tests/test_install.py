"""Installer: temp-prefix installs, idempotency, upgrade, unmanaged refusal."""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import INSTALL, run_cli

MANIFEST = ".goal-loop-manifest.json"


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def inst(self, *args):
        return run_cli(INSTALL, list(args) + ["--prefix", str(self.prefix)])

    def target(self, engine):
        return self.prefix / f".{engine}" / "skills" / "goal-loop"

    def test_fresh_install_both_engines(self):
        proc = self.inst("install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for engine in ("claude", "codex"):
            t = self.target(engine)
            self.assertTrue((t / "SKILL.md").is_file())
            self.assertTrue((t / "state.py").is_file())
            self.assertTrue((t / "references" / "LOOP.md").is_file())
            self.assertTrue((t / MANIFEST).is_file())
        self.assertTrue((self.target("codex") / "agents" / "openai.yaml").is_file())
        self.assertFalse((self.target("claude") / "agents").exists())
        proc = self.inst("verify")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_idempotent_reinstall(self):
        self.inst("install")
        before = (self.target("claude") / MANIFEST).read_text()
        proc = self.inst("install")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already up to date", proc.stdout)
        self.assertEqual((self.target("claude") / MANIFEST).read_text(), before)

    def test_unmanaged_target_refused(self):
        t = self.target("claude")
        t.mkdir(parents=True)
        (t / "SKILL.md").write_text("someone else's skill\n")
        proc = self.inst("install", "--engine", "claude")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unmanaged", proc.stderr)
        self.assertEqual((t / "SKILL.md").read_text(), "someone else's skill\n")

    def test_modified_managed_install_needs_upgrade(self):
        self.inst("install", "--engine", "claude")
        skill = self.target("claude") / "SKILL.md"
        skill.write_text("locally drifted\n")
        proc = self.inst("install", "--engine", "claude")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--upgrade", proc.stderr)
        proc = self.inst("install", "--engine", "claude", "--upgrade")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotEqual(skill.read_text(), "locally drifted\n")
        self.assertEqual(self.inst("verify", "--engine", "claude").returncode, 0)

    def test_upgrade_never_overwrites_foreign_file(self):
        self.inst("install", "--engine", "claude")
        t = self.target("claude")
        manifest = json.loads((t / MANIFEST).read_text())
        del manifest["files"]["state.py"]  # simulate: state.py not owned by us
        (t / MANIFEST).write_text(json.dumps(manifest))
        (t / "state.py").write_text("foreign content\n")
        proc = self.inst("install", "--engine", "claude", "--upgrade")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not owned", proc.stderr)
        self.assertEqual((t / "state.py").read_text(), "foreign content\n")

    def test_uninstall_removes_only_owned_and_preserves_foreign(self):
        self.inst("install")
        foreign = self.target("codex") / "notes.txt"
        foreign.write_text("keep me\n")
        proc = self.inst("uninstall")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.target("claude").exists())
        self.assertTrue(foreign.exists())
        self.assertFalse((self.target("codex") / "SKILL.md").exists())

    def test_uninstall_refuses_modified_file_without_force(self):
        self.inst("install", "--engine", "claude")
        (self.target("claude") / "SKILL.md").write_text("modified\n")
        proc = self.inst("uninstall", "--engine", "claude")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("checksum mismatch", proc.stderr)
        proc = self.inst("uninstall", "--engine", "claude", "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_env_prefix_override(self):
        proc = run_cli(INSTALL, ["install", "--engine", "claude"],
                       extra_env={"GOAL_LOOP_INSTALL_PREFIX": str(self.prefix)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.target("claude") / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
