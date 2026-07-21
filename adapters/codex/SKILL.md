---
name: goal-loop
description: Engine-neutral durable backlog loop. Invoke with $goal-loop for durable autonomous ownership of a whole backlog, milestone, roadmap slice, label selection, or explicit work list that spans sessions or engines; every selected issue is executed through the installed agent-pipeline skill ($pipeline). Use a normal prompt for any bounded single task. Also triggers on resuming or auditing an existing goal-loop run.
---

# goal-loop

Trigger this skill with `$goal-loop`. Use the Codex `/goal` primitive only
when the run requires durable autonomous ownership; for bounded single
tasks, stay with a normal prompt.

Own a durable backlog end to end, sharing state with Claude Code. goal-loop
is the outer orchestrator (selection, contract, ledger, lock, recovery,
authority, reconciliation, resume); the installed **agent-pipeline** skill
is the mandatory inner executor for every item. All run state lives under
`${XDG_STATE_HOME:-~/.local/state}/goal-loop`, owned by the bundled
`state.py`. Never edit state files directly; never resume from chat history
alone.

Read `references/LOOP.md` in this skill directory fully, then follow it.

Steps:

0. Native goal bootstrap (self-attested, not detected). This skill's
   autonomous entrypoint is `/goal` + `$goal-loop`. `state.py` cannot
   independently verify native Goal-mode session state; it only validates
   the shape and freshness of a caller-supplied self-attestation. Before
   `state.py init`, and before every `in_progress` transition (including
   resume from `blocked`), supply fresh evidence: `{"native_goal":
   {"engine": "codex", "run_id": "<run-id>", "status": "active",
   "checked_at": "<now, ISO8601>"}}`. Missing, stale (>300s old), mismatched
   run_id/engine, or any status other than `"active"` (including `"paused"`,
   `"cleared"`, `"unknown"`) fails closed with exit code 8: re-run `/goal`
   then `$goal-loop`, then retry with fresh evidence. `status`, `show`,
   `runs`, and `reconcile` need no native-goal evidence — including on a run
   whose native `/goal` was since cleared or paused, or whose items are all
   terminal. Editing the native `/goal` text mid-run has no code path back
   into the durable contract or ledger; an actual objective change requires
   a new `compile-contract` + `init` with a new `run_id`. Never declare the
   native `/goal` complete before `done_definition` is met and a final
   `reconcile` shows every item terminal — `state.py` cannot intercept the
   engine's own native-completion action, this is procedural guidance only.
   Never claim this skill recursively invoked `/goal`, `/goal-loop`, or
   `$goal-loop` mid-run — it cannot, and claiming otherwise is exactly the
   false attestation this mandate exists to prevent.
1. Discover repo instructions, backlog source of truth, base branch, dirty
   state, delivery workflow, verification commands, and explicit authority
   grants. Write a discovery JSON (`references/discovery.example.json`).
2. Preflight the pipeline (fail closed): run
   `python3 <skill-dir>/state.py pipeline-preflight --engine codex`, then
   `node ~/.codex/skills/pipeline/scripts/pipeline.mjs doctor --json`.
   If the pipeline skill is missing or doctor fails, do not start any item —
   report and stop. There is no non-pipeline fallback.
3. Compile the canonical contract:
   `python3 <skill-dir>/state.py compile-contract --adapter codex ...`,
   then `state.py init`. For an existing run, resume instead.
4. Acquire the lock as engine `codex`. If held and not verifiably stale,
   the other engine owns the run — stop and report.
5. Reconcile ledger against live issues, branches, PRs, checks, and base
   SHA before every item and every resume.
6. Work one item at a time in dependency order by handing it to the
   pipeline with `$pipeline <N>` — it owns planning through
   `pipeline:ready-to-deploy` in its own worktree. `state.py` refuses
   `in_progress` without preflight evidence (exit 7) and `ready` without
   verified `pipeline:ready-to-deploy` stage evidence. Record all state
   changes via `state.py transition`.
7. Verify terminal states directly with real commands and check results;
   gated transitions (push/PR, merge, release, deploy) need an explicit
   contract grant plus `--evidence`. A broad objective grants nothing.
   Without the merge grant, stop at ready-to-deploy and report.
8. Merge (only with explicit merge authority): use only the pipeline's
   merge surface `node ~/.codex/skills/pipeline/scripts/pipeline.mjs merge
   <pr>` after ready-to-deploy; verify the merge SHA and checks directly;
   transition `merged` with `{"merge": {"via": "pipeline-merge", "sha": ...}}`
   evidence (this sets a merge barrier); fetch + fast-forward the local
   base branch, run `$pipeline:cleanup` and post-merge hooks; reconcile
   with the merged SHA in `merged_shas` to clear the barrier; only then
   start the next item from the refreshed base.
9. On exhausted recovery budgets or repeated blocks the run stops
   terminally — report the stop, do not work around it.
10. Release the lock when pausing or done and emit the contract's report
    with per-item evidence and the exact next step.
