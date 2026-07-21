---
name: goal-loop
description: >-
  Engine-neutral durable backlog loop. Use when the user asks to process,
  resume, or audit a durable issue backlog, milestone, roadmap slice, label
  selection, or explicit work list end to end — work expected to span
  sessions or engines (Claude Code / Codex CLI). Every selected issue is
  executed through the installed agent-pipeline skill (/pipeline). Triggers
  include "work through the v2 milestone", "resume the goal-loop run",
  "process everything labeled X", "audit the backlog run". Do NOT use for a
  single bounded task, one-off bug fix, or ordinary PR review.
---

# goal-loop

Durable, cross-engine backlog execution. goal-loop is the outer orchestrator
(selection, contract, ledger, lock, recovery, authority, reconciliation,
resume); the installed **agent-pipeline** skill is the mandatory inner
executor for every item. All durable state lives on disk under
`${XDG_STATE_HOME:-~/.local/state}/goal-loop` and is owned by the bundled
`state.py` — never edit state files directly, and never rely on chat history
for run state.

Read `references/LOOP.md` in this skill directory FULLY before acting, then
follow it exactly. The short version:

0. **Native goal bootstrap (self-attested, not detected).** This skill's
   autonomous entrypoint is `/goal` + `/goal-loop`. `state.py` cannot
   independently verify native Goal-mode session state; it only validates
   the shape and freshness of a caller-supplied self-attestation. Before
   `state.py init`, and before every `in_progress` transition (including
   resume from `blocked`), supply fresh evidence: `{"native_goal":
   {"engine": "claude", "run_id": "<run-id>", "status": "active",
   "checked_at": "<now, ISO8601>"}}`. Missing, stale (>300s old), mismatched
   run_id/engine, or any status other than `"active"` (including `"paused"`,
   `"cleared"`, `"unknown"`) fails closed with exit code 8: re-run `/goal`
   then `/goal-loop`, then retry with fresh evidence. `status`, `show`,
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
1. **Discover** repo instructions, source of truth, base branch, dirty
   state, delivery workflow, verification commands, checks, post-merge
   hooks, and explicit authority grants. Write a discovery JSON
   (shape: `references/discovery.example.json`).
2. **Preflight the pipeline (fail closed)**: run
   `python3 <skill-dir>/state.py pipeline-preflight --engine claude`, then
   `node ~/.claude/skills/pipeline/scripts/pipeline.mjs doctor --json`.
   If the pipeline skill is missing or doctor fails, do not start any item —
   report the failure and stop. There is no non-pipeline fallback.
3. **Compile + init** the canonical contract with
   `python3 <skill-dir>/state.py compile-contract --adapter claude ...
   --native-goal-evidence ...` (required whenever `--out` writes the
   contract artifact) then `init`. For an existing run, skip to resume.
4. **Execute**: acquire the exclusive lock as engine `claude` — if the run
   has an item already `in_progress` (e.g. resuming a paused session with no
   intervening transition), `lock acquire` also requires fresh native-goal
   evidence via `--native-goal-evidence` and fails closed (exit 8) without
   it — reconcile against live repo/remote truth before every item and
   every resume, then
   hand each item to the pipeline with `/pipeline <N>` — it owns planning
   through `pipeline:ready-to-deploy` in its own worktree. One item at a
   time. `state.py` refuses `in_progress` without preflight evidence
   (exit 7) and `ready` without verified `pipeline:ready-to-deploy` stage
   evidence. Record every state change via `state.py transition`.
5. **Verify directly**: run the contract's verification commands and check
   PRs/checks/SHAs yourself. Never accept a subagent claim as evidence.
6. **Respect gates**: push/PR, merge, release, deploy each require an
   explicit contract grant plus `--evidence`. A broad objective grants
   nothing. Without the merge grant, stop at ready-to-deploy and report.
7. **Merge (only with explicit merge authority)**: use only the pipeline's
   merge surface `node ~/.claude/skills/pipeline/scripts/pipeline.mjs merge
   <pr>` after ready-to-deploy; verify the merge SHA and checks directly;
   transition `merged` with `{"merge": {"via": "pipeline-merge", "sha": ...}}`
   evidence (this sets a merge barrier); fetch + fast-forward the local base
   branch, run `/pipeline:cleanup` and post-merge hooks; reconcile with the
   merged SHA in `merged_shas` to clear the barrier; only then start the
   next item from the refreshed base.
8. **Stop conditions are terminal**: exhausted recovery budgets or repeated
   blocks stop the run; report the stop instead of working around it.
9. **Release the lock** whenever pausing or finishing, and emit the
   contract's report format with per-item evidence and the exact next step.

If the lock is held: `state.py lock status --run <id>` shows the holder and
staleness. Recover only verifiably stale locks; a live lock from the other
engine means that engine owns the run right now.
