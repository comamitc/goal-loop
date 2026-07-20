"""Golden parity between adapters and cross-engine resume in both directions."""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import FIXTURE, PREFLIGHT_EV, make_run, state, state_json, acquire


class TestGoldenParity(unittest.TestCase):
    def test_both_adapters_compile_equivalent_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = {}
            for adapter in ("claude", "codex"):
                path = Path(tmp) / f"{adapter}.json"
                proc = state(["compile-contract", "--discovery", str(FIXTURE),
                              "--adapter", adapter, "--run-id", "golden",
                              "--out", str(path)], tmp)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out[adapter] = json.loads(path.read_text())
            a, b = out["claude"], out["codex"]
            self.assertEqual(a["canonical_hash"], b["canonical_hash"])
            a.pop("adapter"), b.pop("adapter")
            self.assertEqual(a, b)


class TestCrossEngineResume(unittest.TestCase):
    def resume_roundtrip(self, first, second):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run_id = make_run(home, run_id=f"{first}-then-{second}",
                              adapter=first)
            # engine 1 starts the run and advances an item
            t1 = acquire(home, run_id, first)
            state_json(["transition", "--run", run_id, "--item", "issue-101",
                        "--to", "in_progress", "--token", t1,
                        "--evidence", PREFLIGHT_EV], home)
            state_json(["lock", "release", "--run", run_id, "--token", t1],
                       home)
            # engine 2 resumes purely from disk: status, reconcile, advance
            t2 = acquire(home, run_id, second)
            status = state_json(["status", "--run", run_id], home)
            self.assertEqual(status["items"]["issue-101"], "in_progress")
            truth = home / "truth.json"
            truth.write_text(json.dumps(
                {"base_sha": "live-sha",
                 "items": {"issue-101": {"state": "in_progress"}}}))
            out = state_json(["reconcile", "--run", run_id, "--token", t2,
                              "--input", str(truth)], home)
            self.assertEqual(out["mismatches"], [])
            state_json(["transition", "--run", run_id, "--item", "issue-101",
                        "--to", "implemented", "--token", t2], home)
            ledger = json.loads(
                (home / "runs" / run_id / "ledger.json").read_text())
            engines = [h["engine"]
                       for h in ledger["items"]["issue-101"]["history"]]
            self.assertEqual(engines, [first, second])

    def test_claude_starts_codex_resumes(self):
        self.resume_roundtrip("claude", "codex")

    def test_codex_starts_claude_resumes(self):
        self.resume_roundtrip("codex", "claude")

    def test_second_engine_cannot_advance_while_first_holds_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run_id = make_run(home)
            t1 = acquire(home, run_id, "claude")
            proc = state(["transition", "--run", run_id, "--item",
                          "issue-101", "--to", "in_progress",
                          "--token", "codex-guess",
                          "--evidence", PREFLIGHT_EV], home)
            self.assertEqual(proc.returncode, 3)
            # legitimate holder still works
            proc = state(["transition", "--run", run_id, "--item",
                          "issue-101", "--to", "in_progress", "--token", t1,
                          "--evidence", PREFLIGHT_EV],
                         home)
            self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
