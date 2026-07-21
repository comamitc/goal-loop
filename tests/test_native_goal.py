"""Native goal bootstrap mandate: before any item may enter in_progress the
caller must supply a fresh, self-attested claim that the engine's native
/goal primitive is active. state.py cannot detect this independently -- it
only validates SHAPE and FRESHNESS of the caller-supplied attestation."""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import (FIXTURE, IN_PROGRESS_EV, PREFLIGHT_EV, make_run,
                     native_goal_evidence, state, state_json, acquire)


def compile_contract(home, run_id="r1", adapter="claude"):
    disc = Path(home) / "discovery.json"
    disc.write_text(FIXTURE.read_text())
    contract_path = Path(home) / "contract.json"
    state_json(["compile-contract", "--discovery", str(disc),
                "--adapter", adapter, "--run-id", run_id,
                "--out", str(contract_path)], home)
    return contract_path


def raw_init(home, contract_path, engine, evidence):
    args = ["init", "--contract", str(contract_path), "--engine", engine]
    if evidence is not None:
        args += ["--native-goal-evidence", evidence]
    return state(args, home)


class TestInitGate(unittest.TestCase):
    """A rejected init must leave no run directory behind."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.contract = compile_contract(self.home)
        self.rdir = self.home / "runs" / "r1"

    def assert_rejected(self, evidence):
        proc = raw_init(self.home, self.contract, "claude", evidence)
        self.assertEqual(proc.returncode, 8, proc.stderr)
        self.assertIn("/goal", proc.stderr)
        self.assertIn("/goal-loop", proc.stderr)
        self.assertFalse(self.rdir.exists())

    def test_missing_evidence_rejected(self):
        self.assert_rejected(None)

    def test_stale_evidence_rejected(self):
        self.assert_rejected(native_goal_evidence("claude", "r1", stale=True))

    def test_wrong_run_id_rejected(self):
        self.assert_rejected(
            native_goal_evidence("claude", "r1", run_id_override="other-run"))

    def test_wrong_engine_rejected(self):
        self.assert_rejected(
            native_goal_evidence("claude", "r1", engine_override="codex"))

    def test_non_active_status_rejected(self):
        for status in ("paused", "cleared", "unknown"):
            self.assert_rejected(
                native_goal_evidence("claude", "r1", status=status))

    def test_valid_evidence_succeeds(self):
        proc = raw_init(self.home, self.contract, "claude",
                        native_goal_evidence("claude", "r1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.rdir.exists())
        ledger = json.loads((self.rdir / "ledger.json").read_text())
        self.assertEqual(ledger["last_native_goal_check"]["status"], "active")

    def test_codex_engine_uses_dollar_goal_loop_in_corrective_message(self):
        proc = raw_init(self.home, self.contract, "codex", None)
        self.assertEqual(proc.returncode, 8, proc.stderr)
        self.assertIn("$goal-loop", proc.stderr)


class TestTransitionGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_id = make_run(self.home)
        self.token = acquire(self.home, self.run_id, "claude")
        self.rdir = self.home / "runs" / self.run_id

    def ledger_bytes(self):
        return (self.rdir / "ledger.json").read_bytes()

    def events_lines(self):
        return (self.rdir / "events.jsonl").read_text().splitlines()

    def transition(self, item, to, evidence=None, **kw):
        args = ["transition", "--run", self.run_id, "--item", item,
                "--to", to, "--token", self.token]
        if evidence is not None:
            args += ["--evidence", evidence]
        for k, v in kw.items():
            args += [f"--{k}", v]
        return state(args, self.home)

    def combined(self, **kw):
        ng = json.loads(native_goal_evidence("claude", self.run_id, **kw))
        return json.dumps({"pipeline": {"preflight": "pass"}, **ng})

    def assert_rejected(self, evidence):
        before_ledger = self.ledger_bytes()
        before_events = self.events_lines()
        proc = self.transition("issue-101", "in_progress", evidence=evidence)
        self.assertEqual(proc.returncode, 8, proc.stderr)
        self.assertEqual(self.ledger_bytes(), before_ledger)
        self.assertEqual(self.events_lines(), before_events)

    def test_missing_native_goal_key_rejected(self):
        self.assert_rejected(json.dumps({"pipeline": {"preflight": "pass"}}))

    def test_stale_rejected(self):
        self.assert_rejected(self.combined(stale=True))

    def test_wrong_run_id_rejected(self):
        self.assert_rejected(self.combined(run_id_override="some-other-run"))

    def test_wrong_engine_rejected(self):
        self.assert_rejected(self.combined(engine_override="codex"))

    def test_non_active_status_rejected(self):
        for status in ("paused", "cleared", "unknown"):
            self.assert_rejected(self.combined(status=status))

    def test_valid_evidence_succeeds(self):
        proc = self.transition("issue-101", "in_progress",
                               evidence=self.combined())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ledger = json.loads(self.ledger_bytes())
        entry = ledger["items"]["issue-101"]["history"][-1]
        self.assertEqual(entry["native_goal_check"]["status"], "active")
        self.assertEqual(ledger["last_native_goal_check"]["status"], "active")

    def test_resume_from_blocked_requires_fresh_evidence(self):
        proc = self.transition("issue-101", "in_progress",
                               evidence=self.combined())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.transition("issue-101", "blocked", theme="environment")
        # stale evidence on resume is refused, no mutation
        before = self.ledger_bytes()
        proc = self.transition("issue-101", "in_progress",
                               evidence=self.combined(stale=True))
        self.assertEqual(proc.returncode, 8, proc.stderr)
        self.assertEqual(self.ledger_bytes(), before)
        # fresh evidence on resume succeeds
        proc = self.transition("issue-101", "in_progress",
                               evidence=self.combined())
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestClearedPausedNeverMutates(unittest.TestCase):
    """A cleared/paused-status attempt at in_progress must never mutate any
    item's state -- the whole items dict stays byte-identical."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_id = make_run(self.home)
        self.token = acquire(self.home, self.run_id, "claude")
        self.rdir = self.home / "runs" / self.run_id

    def test_cleared_and_paused_never_mutate_items(self):
        before = json.loads((self.rdir / "ledger.json").read_text())["items"]
        for status in ("cleared", "paused"):
            ng = json.loads(native_goal_evidence("claude", self.run_id,
                                                 status=status))
            ev = json.dumps({"pipeline": {"preflight": "pass"}, **ng})
            proc = state(["transition", "--run", self.run_id, "--item",
                         "issue-101", "--to", "in_progress",
                         "--token", self.token, "--evidence", ev], self.home)
            self.assertEqual(proc.returncode, 8, proc.stderr)
        after = json.loads((self.rdir / "ledger.json").read_text())["items"]
        self.assertEqual(before, after)


class TestReadOnlyBoundary(unittest.TestCase):
    """status/show/reconcile and non-in_progress transitions need zero
    native-goal evidence, including on a run whose items are all terminal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_id = make_run(self.home)
        self.token = acquire(self.home, self.run_id, "claude")

    def test_status_and_show_need_no_evidence(self):
        proc = state(["status", "--run", self.run_id], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = state(["show", "ledger", "--run", self.run_id], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_reconcile_needs_no_evidence(self):
        truth = self.home / "truth.json"
        truth.write_text(json.dumps({"items": {}}))
        proc = state(["reconcile", "--run", self.run_id, "--token", self.token,
                     "--input", str(truth)], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_transition_to_non_in_progress_needs_no_native_goal_evidence(self):
        proc = state(["transition", "--run", self.run_id, "--item",
                      "issue-103", "--to", "abandoned",
                      "--token", self.token], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_all_terminal_run_read_only_ops_need_no_evidence(self):
        for item in ("issue-101", "issue-102", "issue-103"):
            proc = state(["transition", "--run", self.run_id, "--item", item,
                         "--to", "abandoned", "--token", self.token],
                        self.home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        status = state_json(["status", "--run", self.run_id], self.home)
        self.assertTrue(all(s == "abandoned" for s in status["items"].values()))
        truth = self.home / "truth.json"
        truth.write_text(json.dumps({"items": {}}))
        proc = state(["reconcile", "--run", self.run_id, "--token", self.token,
                     "--input", str(truth)], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = state(["show", "events", "--run", self.run_id], self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestCrossEngineFreshEvidence(unittest.TestCase):
    def test_second_engine_supplies_own_evidence_not_matching_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run_id = make_run(home, run_id="cross1", adapter="claude")
            t1 = acquire(home, run_id, "claude")
            state_json(["transition", "--run", run_id, "--item", "issue-101",
                        "--to", "in_progress", "--token", t1,
                        "--evidence", IN_PROGRESS_EV("claude", run_id)], home)
            state_json(["transition", "--run", run_id, "--item", "issue-101",
                        "--to", "blocked", "--token", t1,
                        "--theme", "environment"], home)
            state_json(["lock", "release", "--run", run_id, "--token", t1],
                       home)
            t2 = acquire(home, run_id, "codex")
            # codex's own fresh evidence -- never needs to match claude's
            # prior claim.
            proc = state(["transition", "--run", run_id, "--item",
                         "issue-101", "--to", "in_progress", "--token", t2,
                         "--evidence", IN_PROGRESS_EV("codex", run_id)], home)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class TestDirectCliBypass(unittest.TestCase):
    """Bare CLI use with no evidence, or evidence missing native_goal
    entirely, must fail closed -- independent of what the docs/workflow
    describe as the intended call sequence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.run_id = make_run(self.home)
        self.token = acquire(self.home, self.run_id, "claude")

    def test_no_evidence_at_all_fails_closed(self):
        proc = state(["transition", "--run", self.run_id, "--item",
                     "issue-101", "--to", "in_progress",
                     "--token", self.token], self.home)
        # Wholly absent evidence trips the pre-existing pipeline mandate
        # (exit 7) before the native-goal check is ever reached -- still
        # fails closed, never enters in_progress.
        self.assertIn(proc.returncode, (7, 8))
        status = state_json(["status", "--run", self.run_id], self.home)
        self.assertEqual(status["items"]["issue-101"], "pending")

    def test_evidence_missing_native_goal_key_fails_closed(self):
        proc = state(["transition", "--run", self.run_id, "--item",
                     "issue-101", "--to", "in_progress", "--token", self.token,
                     "--evidence", PREFLIGHT_EV], self.home)
        self.assertEqual(proc.returncode, 8, proc.stderr)
        status = state_json(["status", "--run", self.run_id], self.home)
        self.assertEqual(status["items"]["issue-101"], "pending")


if __name__ == "__main__":
    unittest.main()
