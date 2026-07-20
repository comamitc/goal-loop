"""Lock contention, stale-lock recovery, no silent stealing."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import make_run, state, state_json, acquire


def dead_pid():
    """Spawn and reap a child so its pid is verifiably dead."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_id = make_run(self.home)
        self.lock_file = self.home / "runs" / self.run_id / "lock.json"

    def test_acquire_release_roundtrip(self):
        token = acquire(self.home, self.run_id, "claude")
        self.assertTrue(self.lock_file.exists())
        out = state_json(["lock", "release", "--run", self.run_id,
                          "--token", token], self.home)
        self.assertTrue(out["released"])
        self.assertFalse(self.lock_file.exists())

    def test_contention_second_engine_refused(self):
        acquire(self.home, self.run_id, "claude")
        proc = state(["lock", "acquire", "--run", self.run_id,
                      "--engine", "codex", "--pid", str(os.getpid())],
                     self.home)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("already held by engine 'claude'", proc.stderr)

    def test_transition_without_lock_refused(self):
        proc = state(["transition", "--run", self.run_id, "--item",
                      "issue-101", "--to", "in_progress", "--token", "x"],
                     self.home)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("no lock held", proc.stderr)

    def test_wrong_token_refused(self):
        acquire(self.home, self.run_id, "claude")
        proc = state(["transition", "--run", self.run_id, "--item",
                      "issue-101", "--to", "in_progress", "--token", "bogus"],
                     self.home)
        self.assertEqual(proc.returncode, 3)

    def test_live_lock_not_recoverable_without_force(self):
        acquire(self.home, self.run_id, "claude", pid=os.getpid())
        proc = state(["lock", "recover", "--run", self.run_id], self.home)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("refusing to recover", proc.stderr)
        self.assertTrue(self.lock_file.exists())

    def test_stale_lock_inspectable_and_recoverable(self):
        acquire(self.home, self.run_id, "claude", pid=dead_pid())
        status = state_json(["lock", "status", "--run", self.run_id], self.home)
        self.assertTrue(status["held"])
        self.assertTrue(status["stale"])
        out = state_json(["lock", "recover", "--run", self.run_id], self.home)
        self.assertTrue(out["recovered"])
        self.assertTrue(out["was_stale"])
        self.assertFalse(self.lock_file.exists())
        # recovery is audited in the event log
        events = (self.home / "runs" / self.run_id / "events.jsonl").read_text()
        self.assertIn("lock_recovered_stale", events)

    def test_forced_break_is_loudly_audited(self):
        acquire(self.home, self.run_id, "codex", pid=os.getpid())
        out = state_json(["lock", "recover", "--run", self.run_id, "--force"],
                         self.home)
        self.assertTrue(out["recovered"])
        self.assertFalse(out["was_stale"])
        events = (self.home / "runs" / self.run_id / "events.jsonl").read_text()
        self.assertIn("lock_broken_forcibly", events)

    def test_other_host_lock_never_stale(self):
        acquire(self.home, self.run_id, "codex", pid=dead_pid())
        data = json.loads(self.lock_file.read_text())
        data["hostname"] = "some-other-host"
        self.lock_file.write_text(json.dumps(data))
        status = state_json(["lock", "status", "--run", self.run_id], self.home)
        self.assertFalse(status["stale"])
        proc = state(["lock", "recover", "--run", self.run_id], self.home)
        self.assertEqual(proc.returncode, 3)


if __name__ == "__main__":
    unittest.main()
