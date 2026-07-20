---
name: goal-loop
description: Engine-neutral durable backlog loop. Invoke with $goal-loop for durable autonomous ownership of a whole backlog, milestone, roadmap slice, label selection, or explicit work list that spans sessions or engines; use a normal prompt for any bounded single task. Also triggers on resuming or auditing an existing goal-loop run.
---

# goal-loop

Trigger this skill with `$goal-loop`. Use the Codex `/goal` primitive only
when the run requires durable autonomous ownership; for bounded single
tasks, stay with a normal prompt.

Own a durable backlog end to end, sharing state with Claude Code. All run
state lives under `${XDG_STATE_HOME:-~/.local/state}/goal-loop`, owned by
the bundled `state.py`. Never edit state files directly; never resume from
chat history alone.

Read `references/LOOP.md` in this skill directory fully, then follow it.

Steps:

1. Discover repo instructions, backlog source of truth, base branch, dirty
   state, delivery workflow, verification commands, and explicit authority
   grants. Write a discovery JSON (`references/discovery.example.json`).
2. Compile the canonical contract:
   `python3 <skill-dir>/state.py compile-contract --adapter codex ...`,
   then `state.py init`. For an existing run, resume instead.
3. Acquire the lock as engine `codex`. If held and not verifiably stale,
   the other engine owns the run — stop and report.
4. Reconcile ledger against live issues, branches, PRs, checks, and base
   SHA before every item and every resume.
5. Work one item at a time in dependency order, in a dedicated worktree.
   Record all state changes via `state.py transition`.
6. Verify terminal states directly with real commands and check results;
   gated transitions (push/PR, merge, release, deploy) need an explicit
   contract grant plus `--evidence`. A broad objective grants nothing.
7. On exhausted recovery budgets or repeated blocks the run stops
   terminally — report the stop, do not work around it.
8. Release the lock when pausing or done and emit the contract's report
   with per-item evidence and the exact next step.
