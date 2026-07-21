"""Ledger transitions, atomic writes, event append, authority, recovery."""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import (FIXTURE, IN_PROGRESS_EV, PREFLIGHT_EV, READY_EV, make_run,
                     state, state_json, acquire)


class StateBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_id = make_run(self.home)
        self.token = acquire(self.home, self.run_id, "claude")

    def transition(self, item, to, **kw):
        args = ["transition", "--run", self.run_id, "--item", item,
                "--to", to, "--token", self.token]
        for k, v in kw.items():
            args += [f"--{k}", v]
        return state(args, self.home)

    def start(self, item):
        """Enter in_progress with mandatory pipeline preflight evidence and a
        fresh native-goal self-attestation."""
        return self.transition(item, "in_progress",
                               evidence=IN_PROGRESS_EV("claude", self.run_id))

    def run_dir(self):
        return self.home / "runs" / self.run_id


class TestTransitions(StateBase):
    def test_valid_chain(self):
        proc = self.start("issue-101")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.transition("issue-101", "implemented")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ledger = json.loads((self.run_dir() / "ledger.json").read_text())
        self.assertEqual(ledger["items"]["issue-101"]["state"], "implemented")
        self.assertEqual(len(ledger["items"]["issue-101"]["history"]), 2)

    def test_invalid_transition_rejected(self):
        proc = self.transition("issue-101", "merged")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid transition", proc.stderr)
        ledger = json.loads((self.run_dir() / "ledger.json").read_text())
        self.assertEqual(ledger["items"]["issue-101"]["state"], "pending")

    def test_unknown_item_rejected(self):
        proc = self.transition("nope", "in_progress")
        self.assertEqual(proc.returncode, 2)

    def test_blocked_requires_theme(self):
        self.start("issue-101")
        proc = self.transition("issue-101", "blocked")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--theme", proc.stderr)

    def test_dependency_order_in_contract(self):
        contract = json.loads((self.run_dir() / "contract.json").read_text())
        ids = [it["id"] for it in contract["items"]]
        self.assertLess(ids.index("issue-101"), ids.index("issue-102"))


class TestAtomicAndEvents(StateBase):
    def test_no_temp_files_left_and_valid_json(self):
        self.start("issue-101")
        leftovers = list(self.run_dir().glob("*.tmp"))
        self.assertEqual(leftovers, [])
        json.loads((self.run_dir() / "ledger.json").read_text())

    def test_events_append_only_with_sequence(self):
        self.start("issue-101")
        self.transition("issue-101", "blocked", theme="flaky-test")
        lines = (self.run_dir() / "events.jsonl").read_text().splitlines()
        events = [json.loads(l) for l in lines]
        self.assertEqual([e["seq"] for e in events], list(range(len(events))))
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds[0], "run_initialized")
        self.assertEqual(kinds.count("transition"), 2)

    def test_decision_appends(self):
        state_json(["decision", "--run", self.run_id, "--token", self.token,
                    "--text", "chose plan A", "--item", "issue-101"], self.home)
        recs = (self.run_dir() / "decisions.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(recs[0])["decision"], "chose plan A")


class TestAuthority(StateBase):
    """Fixture grants only push_pr; broad objective must not widen it."""

    def to_ready(self, item="issue-103"):
        self.start(item)
        self.transition(item, "implemented")
        proc = self.transition(item, "pr_opened",
                               evidence='{"pr": 7, "head_sha": "abc"}')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.transition(item, "ready", evidence=READY_EV)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_gate_requires_evidence(self):
        self.start("issue-103")
        self.transition("issue-103", "implemented")
        proc = self.transition("issue-103", "pr_opened")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("evidence", proc.stderr)

    def test_broad_objective_does_not_grant_merge(self):
        # Objective says "ship everything", but merge was never granted.
        self.to_ready()
        proc = self.transition("issue-103", "merged",
                               evidence='{"merged_sha": "def"}')
        self.assertEqual(proc.returncode, 4)
        self.assertIn("authority gate 'merge' not granted", proc.stderr)

    def test_granted_gate_with_evidence_passes(self):
        self.to_ready()
        contract = json.loads((self.run_dir() / "contract.json").read_text())
        self.assertTrue(contract["authority"]["push_pr"])
        self.assertFalse(contract["authority"]["merge"])


class TestRecovery(StateBase):
    def test_exhaustion_stops_run_and_blocks_continuation(self):
        # environment budget is 1 in the fixture
        self.start("issue-101")
        self.transition("issue-101", "blocked", theme="environment")
        proc = self.start("issue-101")  # charges 1 -> 0
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.transition("issue-101", "blocked", theme="environment")
        proc = self.start("issue-101")  # exhausted
        self.assertEqual(proc.returncode, 5)
        self.assertIn("exhausted", proc.stderr)
        # run is terminally stopped: nothing may advance, even other items
        proc = self.start("issue-103")
        self.assertEqual(proc.returncode, 5)
        self.assertIn("stopped", proc.stderr)
        status = state_json(["status", "--run", self.run_id], self.home)
        self.assertEqual(status["stop"]["reason"], "recovery_exhausted")

    def test_max_consecutive_blocked_stops(self):
        # fixture: max_consecutive_blocked = 2
        self.start("issue-101")
        self.transition("issue-101", "blocked", theme="flaky-test")
        self.start("issue-101")
        self.transition("issue-101", "blocked", theme="flaky-test")
        self.start("issue-101")
        self.transition("issue-101", "blocked", theme="flaky-test")
        status = state_json(["status", "--run", self.run_id], self.home)
        self.assertEqual(status["stop"]["reason"], "max_consecutive_blocked")


class TestReconcile(StateBase):
    def test_reconcile_records_truth_and_mismatches(self):
        self.start("issue-101")
        truth = self.home / "truth.json"
        truth.write_text(json.dumps({
            "base_sha": "abc123",
            "items": {"issue-101": {"state": "implemented"},
                      "issue-103": {"state": "pending"}},
        }))
        out = state_json(["reconcile", "--run", self.run_id,
                          "--token", self.token, "--input", str(truth)],
                         self.home)
        self.assertEqual(out["mismatches"],
                         [{"item": "issue-101", "ledger": "in_progress",
                           "observed": "implemented"}])
        status = state_json(["status", "--run", self.run_id], self.home)
        self.assertIsNotNone(status["last_reconcile"])


if __name__ == "__main__":
    unittest.main()
