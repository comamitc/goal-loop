---
name: goal-loop
description: Engine-neutral durable backlog loop. Invoke with $goal-loop for durable autonomous ownership of a whole backlog, milestone, roadmap slice, label selection, or explicit work list that spans sessions or engines; every selected issue is executed through the installed agent-pipeline skill ($pipeline). Use a normal prompt for any bounded single task. Also triggers on resuming or auditing an existing goal-loop run.
---

# goal-loop

Trigger this skill with `$goal-loop`. Use the Codex `/goal` primitive only
when the run requires durable autonomous ownership: start native `/goal`,
then invoke `$goal-loop`. For bounded single tasks, stay with a normal
prompt — not native Goal mode, not goal-loop.

Native `/goal` is an operator-owned prerequisite for autonomous goal-loop
use, not a capability this skill detects, verifies, or controls. Native
goal status and completion are owned by the host/session and are not
detected, verified, or controlled by this standalone skill. Complete
native `/goal` only after goal-loop's own done definition is met and a
final reconciliation pass has run.

Own a durable backlog end to end, sharing state with Claude Code. goal-loop
is the outer orchestrator (selection, contract, ledger, lock, recovery,
authority, reconciliation, resume); the installed **agent-pipeline** skill
is the mandatory inner executor for every item. All run state lives under
`${XDG_STATE_HOME:-~/.local/state}/goal-loop`, owned by the bundled
`state.py`. Never edit state files directly; never resume from chat history
alone.

Read `references/LOOP.md` in this skill directory fully, then follow it.

Steps:

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
