---
name: goal-loop
description: >-
  Engine-neutral durable backlog loop. Use when the user asks to process,
  resume, or audit a durable issue backlog, milestone, roadmap slice, label
  selection, or explicit work list end to end — work expected to span
  sessions or engines (Claude Code / Codex CLI). Triggers include "work
  through the v2 milestone", "resume the goal-loop run", "process everything
  labeled X", "audit the backlog run". Do NOT use for a single bounded task,
  one-off bug fix, or ordinary PR review.
---

# goal-loop

Durable, cross-engine backlog execution. All durable state lives on disk
under `${XDG_STATE_HOME:-~/.local/state}/goal-loop` and is owned by the
bundled `state.py` — never edit state files directly, and never rely on chat
history for run state.

Read `references/LOOP.md` in this skill directory FULLY before acting, then
follow it exactly. The short version:

1. **Discover** repo instructions, source of truth, base branch, dirty
   state, delivery workflow, verification commands, checks, post-merge
   hooks, and explicit authority grants. Write a discovery JSON
   (shape: `references/discovery.example.json`).
2. **Compile + init** the canonical contract with
   `python3 <skill-dir>/state.py compile-contract --adapter claude ...`
   then `init`. For an existing run, skip to resume.
3. **Execute**: acquire the exclusive lock as engine `claude`, reconcile
   against live repo/remote truth before every item and every resume, work
   one item at a time in dependency order inside a dedicated worktree, and
   record every state change via `state.py transition`.
4. **Verify directly**: run the contract's verification commands and check
   PRs/checks/SHAs yourself. Never accept a subagent claim as evidence.
5. **Respect gates**: push/PR, merge, release, deploy each require an
   explicit contract grant plus `--evidence`. A broad objective grants
   nothing. If a gate blocks progress: stop, report, wait.
6. **Stop conditions are terminal**: exhausted recovery budgets or repeated
   blocks stop the run; report the stop instead of working around it.
7. **Release the lock** whenever pausing or finishing, and emit the
   contract's report format with per-item evidence and the exact next step.

If the lock is held: `state.py lock status --run <id>` shows the holder and
staleness. Recover only verifiably stale locks; a live lock from the other
engine means that engine owns the run right now.
