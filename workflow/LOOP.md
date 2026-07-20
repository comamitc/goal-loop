# goal-loop — canonical workflow

One workflow, one state contract, two thin adapters (Claude Code, Codex CLI).
Everything durable lives on disk under
`${GOAL_LOOP_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/goal-loop}/runs/<run-id>/`
and is owned by `state.py`. Engines never edit state files directly.

## When this workflow applies

Use it when asked to process, resume, or audit a durable work backlog: a
milestone, a roadmap slice, a label selection, or an explicit work list —
anything expected to outlive one chat session. Do NOT use it for a single
bounded task; do that directly.

## Phase 1 — Discover

Gather live truth before writing anything:

1. Repo instructions (`CLAUDE.md`, `AGENTS.md`, `.github/` docs), source of
   truth for the backlog (issues, roadmap file, list given in the prompt).
2. Base/integration branch (do not trust `origin/HEAD`; check repo docs),
   current dirty state, delivery workflow (PR flow, checks, post-merge hooks).
3. Verification commands and required checks.
4. Authority boundaries: which of `push_pr`, `merge`, `release`, `deploy`
   the operator has EXPLICITLY granted. A broad objective ("ship the whole
   backlog") never grants any gate. When in doubt, grant nothing.

Write the result as a discovery JSON (see `fixtures/discovery.json` for the
shape): repo, selector, snapshot_items (with `deps`), authority_grants,
verification, recovery budgets, stops.

## Phase 2 — Contract

Compile and initialize:

```
python3 state.py compile-contract --discovery discovery.json \
    --adapter <claude|codex> --run-id <run-id> --out contract.json
python3 state.py init --contract contract.json
```

The contract is canonical: run id, repo, selector snapshot, dependency-aware
ordering, adapter, worktree policy, done definition, authority map,
concurrency, recovery budgets, stop conditions, verification, report format.
Its `canonical_hash` excludes the adapter, so Claude and Codex compile the
identical contract from the same discovery.

## Phase 3 — Execute

For every work session (start or resume):

1. **Acquire the lock**: `python3 state.py lock acquire --run <run-id>
   --engine <engine>`. If held, inspect `lock status`. Recover ONLY a stale
   lock (`lock recover`); never force-break a live one without explicit
   operator instruction.
2. **Reconcile before every item and every resume**: collect live truth —
   issue states, branches, PRs, check results, base SHA, roadmap state — into
   a truth JSON and run `state.py reconcile`. Resolve mismatches in favor of
   live truth. Resume from disk + live repo/remote truth, never from chat
   history alone.
3. Pick the next item: first `pending` item in contract order whose deps are
   all in a terminal-successful state. One active item at a time.
4. Work the item in its own worktree per the contract's worktree policy.
5. Record progress only through `state.py transition` and `state.py decision`.
   Every transition appends to `events.jsonl`.
6. **Verify terminal states directly.** Run the contract's verification
   commands; check PRs/checks/SHAs with the real tools. Never accept an agent
   claim when direct evidence exists. Gate transitions require `--evidence`
   containing the verified facts.
7. Release the lock (`lock release --token ...`) when pausing or done.

## Authority gates

`pr_opened` (push/PR), `merged`, `released`, `deployed` are separate gates.
`state.py` refuses a gated transition unless the contract granted that gate
at compile time AND evidence is supplied. If a gate is needed but not
granted: stop, report exactly what is blocked, and wait for the operator.
Never reinterpret the objective as a grant.

## Recovery and stops

- Blocked items carry a `--theme` (e.g. `flaky-test`, `environment`,
  `missing-context`). Re-entering `in_progress` from `blocked` charges that
  theme's recovery budget (falls back to `default`).
- Budget exhausted → the run stops terminally; report and hand back.
- More than `max_consecutive_blocked` blocks in a row → terminal stop.
- A stopped run refuses further transitions. Do not work around a stop.

## Report

At pause or completion, produce the contract's report format: item states,
evidence per terminal state, decisions taken, remaining budget, stop reason
if any, and the exact next step for whoever resumes (either engine).
