"""Mandatory agent-pipeline: preflight fail-closed, evidence envelope,
execution block parity, and serialized merge -> refresh -> next behavior."""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import (FIXTURE, MERGE_EV, PREFLIGHT_EV, READY_EV, STATE,
                     make_run, run_cli, state, state_json, acquire)


class PipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def make(self, run_id="r1", discovery=None):
        self.run_id = make_run(self.home, run_id=run_id, discovery=discovery)
        self.token = acquire(self.home, self.run_id, "claude")
        return self.run_id

    def transition(self, item, to, **kw):
        args = ["transition", "--run", self.run_id, "--item", item,
                "--to", to, "--token", self.token]
        for k, v in kw.items():
            args += [f"--{k}", v]
        return state(args, self.home)

    def reconcile(self, truth):
        path = self.home / "truth.json"
        path.write_text(json.dumps(truth))
        return state(["reconcile", "--run", self.run_id,
                      "--token", self.token, "--input", str(path)], self.home)

    def ledger(self):
        return json.loads(
            (self.home / "runs" / self.run_id / "ledger.json").read_text())


class TestExecutionContract(PipelineBase):
    def test_contract_carries_mandatory_execution_block(self):
        self.make()
        contract = json.loads(
            (self.home / "runs" / self.run_id / "contract.json").read_text())
        ex = contract["execution"]
        self.assertEqual(ex["mode"], "agent-pipeline")
        self.assertEqual(ex["handoff_stage"], "pipeline:ready-to-deploy")
        self.assertEqual(ex["engines"]["claude"]["invocation"], "/pipeline")
        self.assertEqual(ex["engines"]["codex"]["invocation"], "$pipeline")
        for eng in ("claude", "codex"):
            spec = ex["engines"][eng]
            self.assertIn(f".{eng}/skills/pipeline/scripts/pipeline.mjs",
                          spec["entrypoint"])
            self.assertIn("doctor --json", spec["preflight"])
            self.assertIn("merge", spec["merge_surface"])

    def test_discovery_cannot_bypass_pipeline(self):
        disc = json.loads(FIXTURE.read_text())
        disc["execution"] = {"mode": "direct"}
        path = self.home / "bypass.json"
        path.write_text(json.dumps(disc))
        proc = state(["compile-contract", "--discovery", str(path),
                      "--adapter", "claude", "--run-id", "x"], self.home)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("agent-pipeline", proc.stderr)


class TestPipelineEvidenceMandate(PipelineBase):
    def test_in_progress_requires_preflight_pass(self):
        self.make()
        proc = self.transition("issue-101", "in_progress")
        self.assertEqual(proc.returncode, 7)
        self.assertIn("agent-pipeline is mandatory", proc.stderr)
        proc = self.transition("issue-101", "in_progress",
                               evidence='{"pipeline": {"preflight": "fail"}}')
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(self.ledger()["items"]["issue-101"]["state"],
                         "pending")

    def test_blocked_reentry_also_requires_preflight(self):
        self.make()
        self.transition("issue-101", "in_progress", evidence=PREFLIGHT_EV)
        self.transition("issue-101", "blocked", theme="environment")
        proc = self.transition("issue-101", "in_progress")
        self.assertEqual(proc.returncode, 7)

    def test_ready_requires_ready_to_deploy_stage_evidence(self):
        self.make()
        self.transition("issue-103", "in_progress", evidence=PREFLIGHT_EV)
        self.transition("issue-103", "implemented")
        self.transition("issue-103", "pr_opened",
                        evidence='{"pr": 7, "head_sha": "abc"}')
        proc = self.transition("issue-103", "ready",
                               evidence='{"checks": "green"}')
        self.assertEqual(proc.returncode, 7)
        self.assertIn("pipeline:ready-to-deploy", proc.stderr)
        proc = self.transition("issue-103", "ready", evidence=READY_EV)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestMergeSerialization(PipelineBase):
    """With explicit merge authority: pipeline merge surface only, then a
    barrier until reconcile proves the merged sha on a refreshed base."""

    def setUp(self):
        super().setUp()
        disc = json.loads(FIXTURE.read_text())
        disc["authority_grants"] = ["push_pr", "merge"]
        self.make(discovery=disc)

    def to_ready(self, item):
        self.transition(item, "in_progress", evidence=PREFLIGHT_EV)
        self.transition(item, "implemented")
        self.transition(item, "pr_opened", evidence='{"pr": 7, "head_sha": "abc"}')
        proc = self.transition(item, "ready", evidence=READY_EV)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_merge_requires_pipeline_merge_surface_evidence(self):
        self.to_ready("issue-103")
        proc = self.transition("issue-103", "merged",
                               evidence='{"merged_sha": "def456"}')
        self.assertEqual(proc.returncode, 7)
        self.assertIn("pipeline merge surface", proc.stderr)
        proc = self.transition("issue-103", "merged", evidence=MERGE_EV)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_merge_sets_barrier_and_next_item_is_refused(self):
        self.to_ready("issue-103")
        self.transition("issue-103", "merged", evidence=MERGE_EV)
        barrier = self.ledger()["merge_barrier"]
        self.assertEqual(barrier["item"], "issue-103")
        self.assertEqual(barrier["merged_sha"], "def456")
        proc = self.transition("issue-101", "in_progress",
                               evidence=PREFLIGHT_EV)
        self.assertEqual(proc.returncode, 6)
        self.assertIn("merge barrier", proc.stderr)

    def test_reconcile_without_merged_sha_keeps_barrier(self):
        self.to_ready("issue-103")
        self.transition("issue-103", "merged", evidence=MERGE_EV)
        proc = self.reconcile({"base_sha": "stale", "items": {}})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertFalse(out["merge_barrier_cleared"])
        proc = self.transition("issue-101", "in_progress",
                               evidence=PREFLIGHT_EV)
        self.assertEqual(proc.returncode, 6)

    def test_reconcile_with_refreshed_base_clears_barrier_then_next_starts(self):
        self.to_ready("issue-103")
        self.transition("issue-103", "merged", evidence=MERGE_EV)
        proc = self.reconcile({"base_sha": "def456",
                               "merged_shas": ["def456"], "items": {}})
        out = json.loads(proc.stdout)
        self.assertTrue(out["merge_barrier_cleared"])
        self.assertIsNone(self.ledger()["merge_barrier"])
        events = (self.home / "runs" / self.run_id /
                  "events.jsonl").read_text()
        self.assertIn("merge_barrier_set", events)
        self.assertIn("merge_barrier_cleared", events)
        proc = self.transition("issue-101", "in_progress",
                               evidence=PREFLIGHT_EV)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestPipelinePreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prefix = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def preflight(self, engine):
        return run_cli(STATE, ["pipeline-preflight", "--engine", engine,
                               "--prefix", str(self.prefix)])

    def fake_install(self, engine):
        skill = self.prefix / f".{engine}" / "skills" / "pipeline"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: pipeline\n---\n")
        (skill / "scripts" / "pipeline.mjs").write_text("// entrypoint\n")

    def test_missing_skill_fails_closed(self):
        for engine in ("claude", "codex"):
            proc = self.preflight(engine)
            self.assertEqual(proc.returncode, 7, proc.stderr)
            self.assertIn("fails closed", proc.stderr)

    def test_installed_skill_passes_with_engine_specific_invocation(self):
        self.fake_install("claude")
        self.fake_install("codex")
        out = json.loads(self.preflight("claude").stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["invocation"], "/pipeline")
        self.assertIn("doctor --json", out["doctor_cmd"])
        out = json.loads(self.preflight("codex").stdout)
        self.assertEqual(out["invocation"], "$pipeline")
        self.assertIn(".codex/skills/pipeline/scripts/pipeline.mjs",
                      out["entrypoint"])

    def test_entrypoint_missing_fails_closed(self):
        self.fake_install("claude")
        (self.prefix / ".claude" / "skills" / "pipeline" / "scripts"
         / "pipeline.mjs").unlink()
        proc = self.preflight("claude")
        self.assertEqual(proc.returncode, 7)

    def test_env_prefix_override(self):
        self.fake_install("codex")
        proc = run_cli(STATE, ["pipeline-preflight", "--engine", "codex"],
                       extra_env={"GOAL_LOOP_INSTALL_PREFIX": str(self.prefix)})
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
