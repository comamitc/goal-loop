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

For durable autonomous ownership of a backlog: start native `/goal`, then
invoke `/goal-loop`. For a bounded single task, stay with a normal prompt —
not native Goal mode, not goal-loop.

Native `/goal` is an operator-owned prerequisite for autonomous goal-loop
use, not a capability this skill detects, verifies, or controls. Native
goal status and completion are owned by the host/session and are not
detected, verified, or controlled by this standalone skill. Complete
native `/goal` only after goal-loop's own done definition is met and a
final reconciliation pass has run.

Durable, cross-engine backlog execution. goal-loop is the outer orchestrator
(selection, contract, ledger, lock, recovery, authority, reconciliation,
resume); the installed **agent-pipeline** skill is the mandatory inner
executor for every item. All durable state lives on disk under
`${XDG_STATE_HOME:-~/.local/state}/goal-loop` and is owned by the bundled
`state.py` — never edit state files directly, and never rely on chat history
for run state.

Read `references/LOOP.md` in this skill directory FULLY before acting, then
follow it exactly. The short version:

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
   `python3 <skill-dir>/state.py compile-contract --adapter claude ...`
   then `init`. For an existing run, skip to resume.
4. **Execute**: acquire the exclusive lock as engine `claude`, reconcile
   against live repo/remote truth before every item and every resume, then
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
