# goal-loop — canonical workflow

One workflow, one state contract, two thin adapters (Claude Code, Codex CLI).
Everything durable lives on disk under
`${GOAL_LOOP_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/goal-loop}/runs/<run-id>/`
and is owned by `state.py`. Engines never edit state files directly.

goal-loop is the outer durable orchestrator: selection, contract, ledger,
lock, recovery, authority, reconciliation, resume. The installed
**agent-pipeline** skill is the mandatory inner executor: it owns every
selected issue from planning through `pipeline:ready-to-deploy`. goal-loop
never plans, implements, reviews, or opens PRs itself.

## When this workflow applies

Use it when asked to process, resume, or audit a durable work backlog: a
milestone, a roadmap slice, a label selection, or an explicit work list —
anything expected to outlive one chat session. Do NOT use it for a single
bounded task; do that directly.

## Phase 0 — Native goal bootstrap

goal-loop's autonomous entrypoint is the engine's own native `/goal`
primitive: Claude Code is `/goal` + `/goal-loop`; Codex CLI is `/goal` +
`$goal-loop`. `state.py` **cannot independently verify native Goal-mode
session state** — it has no way to observe whether the engine's own `/goal`
is actually active. Instead it validates the SHAPE and FRESHNESS of a
caller-supplied **self-attestation** (this is self-attested, not detected —
exactly like `--evidence '{"pipeline": {"preflight": "pass"}}'` is trusted
without state.py re-running the doctor command itself).

Before `state.py init`, and before every transition to `in_progress`
(including resume from `blocked`), the caller must supply fresh evidence:

```json
{"native_goal": {"engine": "claude"|"codex", "run_id": "<run in play>",
                  "status": "active", "checked_at": "<ISO8601, now>"}}
```

Only `status: "active"` passes; `paused`, `cleared`, and `unknown` all fail
closed. `run_id` and `engine` must match the run and acting engine exactly.
`checked_at` must be within 5 minutes (300s) of the current time.

**Failing to supply valid evidence fails closed with exit code 8** and a
message giving the exact corrective invocation for that engine: re-run
`/goal` then `/goal-loop` (Claude) or `/goal` then `$goal-loop` (Codex),
then retry `init`/`transition` with fresh evidence.

**Terminal/read-only boundary.** `status`, `show`, `runs`, and `reconcile`
need NO native-goal evidence at all — including when run against a run
whose native `/goal` was since cleared or paused, and including a run whose
items are all in a terminal state. Only entry into `in_progress` is gated.

**No code path back into the contract.** Editing the native `/goal` text
mid-run has no effect on the durable contract or ledger — there is no
mechanism by which it could. An actual objective change requires compiling
a new contract (`compile-contract`) and a new `init` with a new `run_id`;
the old run is untouched.

**Native-completion rule (procedural only).** Never declare the native
`/goal` complete before the ledger's `done_definition` is verifiably met
AND a final `reconcile` pass shows every item in a terminal state. This is
guidance for the engine, not an enforced gate: `state.py` has no way to
intercept or block the engine's own native-completion action — it can only
refuse to have recorded the work as done in the durable ledger.

**Never claim a false recursive re-invocation.** Once execution for an
item has begun, never claim (in chat, in a report, or in a decision record)
that this skill recursively invoked `/goal`, `/goal-loop`, or `$goal-loop`
mid-run. Those are the engine's own top-level entrypoints; a skill running
inside a session cannot re-invoke them, and claiming otherwise is a false
attestation of exactly the kind this mandate exists to prevent.

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
5. **Pipeline availability (fail closed).** Run
   `python3 state.py pipeline-preflight --engine <claude|codex>`, then the
   doctor command it prints
   (`node ~/.<engine>/skills/pipeline/scripts/pipeline.mjs doctor --json`).
   If the skill is missing or doctor fails, the run must not start items:
   report the exact failure and stop. There is no non-pipeline fallback.

Write the result as a discovery JSON (see `fixtures/discovery.json` for the
shape): repo, selector, snapshot_items (with `deps`), authority_grants,
verification, recovery budgets, stops.

## Phase 2 — Contract

Compile and initialize:

```
python3 state.py compile-contract --discovery discovery.json \
    --adapter <claude|codex> --run-id <run-id> --out contract.json \
    --native-goal-evidence '{"native_goal": {"engine": "<claude|codex>", \
"run_id": "<run-id>", "status": "active", "checked_at": "<now, ISO8601>"}}'
python3 state.py init --contract contract.json --engine <claude|codex> \
    --native-goal-evidence '{"native_goal": {"engine": "<claude|codex>", \
"run_id": "<run-id>", "status": "active", "checked_at": "<now, ISO8601>"}}'
```

`compile-contract` fails closed with exit code 8 if `--out` is given without
this evidence — writing the contract artifact is external mutation, gated
the same way as `init`. Omitting `--out` (in-memory/stdout-only compile)
stays read-only and needs no evidence. `init` fails closed with exit code 8
if this evidence is missing, stale, mismatched, or not `status: "active"` —
see Phase 0.

The contract is canonical: run id, repo, selector snapshot, dependency-aware
ordering, adapter, mandatory `execution` block (agent-pipeline mode, both
engines' invocations/entrypoints/preflight/merge surface), worktree policy,
done definition, authority map, concurrency, recovery budgets, stop
conditions, verification, report format. Its `canonical_hash` excludes the
adapter, so Claude and Codex compile the identical contract from the same
discovery. `execution.mode` is fixed to `agent-pipeline` — a discovery that
tries to bypass the pipeline is refused at compile time.

## Phase 3 — Execute (via agent-pipeline, one item at a time)

For every work session (start or resume):

1. **Acquire the lock**: `python3 state.py lock acquire --run <run-id>
   --engine <engine>`. If held, inspect `lock status`. Recover ONLY a stale
   lock (`lock recover`); never force-break a live one without explicit
   operator instruction. **If the run has an item already `in_progress`
   (e.g. a prior session paused mid-item and released the lock without a
   transition), `acquire` additionally requires fresh native-goal evidence
   via `--native-goal-evidence` — same shape and freshness rule as Phase 0 —
   and fails closed with exit code 8 without it.** This is the resume
   choke point: no transition happens on a plain pause/resume, so the lock
   acquire itself re-validates the bootstrap before work continues.
2. **Reconcile before every item and every resume**: collect live truth —
   issue states, branches, PRs, check results, base SHA, roadmap state — into
   a truth JSON and run `state.py reconcile`. Resolve mismatches in favor of
   live truth. Resume from disk + live repo/remote truth, never from chat
   history alone.
3. Pick the next item: first `pending` item in contract order whose deps are
   all in a terminal-successful state. One active item at a time
   (concurrency is 1).
4. **Preflight, then hand the item to agent-pipeline.** Re-run
   `state.py pipeline-preflight --engine <engine>` plus the printed doctor
   command; record the item as `in_progress` with that evidence AND a fresh
   native-goal self-attestation in the same `--evidence` JSON
   (`--evidence '{"pipeline": {"preflight": "pass", "doctor": ...},
   "native_goal": {"engine": "<engine>", "run_id": "<run-id>",
   "status": "active", "checked_at": "<now>"}}'`) — `state.py` refuses
   `in_progress` without the pipeline evidence (exit 7) or without valid,
   fresh native-goal evidence (exit 8; see Phase 0). This applies to every
   entry into `in_progress`, including resume from `blocked`. Then invoke
   the pipeline on the issue:
   - Claude Code: `/pipeline <N>` (deterministic entrypoint:
     `node ~/.claude/skills/pipeline/scripts/pipeline.mjs`)
   - Codex CLI: `$pipeline <N>` (deterministic entrypoint:
     `node ~/.codex/skills/pipeline/scripts/pipeline.mjs`)
   The pipeline owns planning, implementation, reviews, fixes, and gates
   through `pipeline:ready-to-deploy`, in its own `pipeline/<N>-<slug>`
   worktree. goal-loop supervises: poll `pipeline:status <N>` / run logs,
   surface `blocked` items via `state.py transition --to blocked --theme ...`,
   and answer/unblock through the pipeline's own surfaces
   (`pipeline:unblock`, `pipeline:override`).
5. Record progress only through `state.py transition` and `state.py decision`.
   Map pipeline truth to ledger states: PR exists → `pr_opened` (push_pr
   gate + evidence); issue labeled `pipeline:ready-to-deploy` → `ready`
   (requires `--evidence '{"pipeline": {"stage": "pipeline:ready-to-deploy"}}'`
   verified from live labels). Every transition appends to `events.jsonl`.
6. **Verify terminal states directly.** Run the contract's verification
   commands; check PRs/checks/SHAs with the real tools. Never accept an agent
   claim when direct evidence exists. Gate transitions require `--evidence`
   containing the verified facts.
7. Release the lock (`lock release --token ...`) when pausing or done.

## Merge (only with explicit `merge` authority)

Without the `merge` grant, `ready` (= `pipeline:ready-to-deploy`) is the
terminal hand-back state: stop, report, wait for the operator.

With the grant, merges are serialized and go ONLY through the pipeline's
controlled merge surface, after ready-to-deploy:

1. `node ~/.<engine>/skills/pipeline/scripts/pipeline.mjs merge <pr>`
   (`/pipeline:merge` / `$pipeline:merge`). Never `gh pr merge` directly.
2. Verify the merge directly: fetch the merged SHA, confirm the PR state and
   required checks on it with real `gh`/git commands.
3. `state.py transition --to merged --evidence
   '{"merge": {"via": "pipeline-merge", "sha": "<sha>", "checks": ...}}'`.
   This sets a **merge barrier** in the ledger: no next item may enter
   `in_progress` (exit 6) until the barrier clears.
4. Refresh the base: `git fetch` and fast-forward the local base branch to
   the merged SHA; run post-merge hooks and `pipeline:cleanup`.
5. Reconcile with truth containing the refreshed `base_sha` and the merged
   SHA in `merged_shas` — this clears the barrier
   (`merge_barrier_cleared: true`).
6. Only then pick the next item, from the refreshed base.

`release` and `deploy` remain independent gates with their own grants and
evidence; nothing about the merge grant implies them.

## Authority gates

`pr_opened` (push/PR), `merged`, `released`, `deployed` are separate gates.
`state.py` refuses a gated transition unless the contract granted that gate
at compile time AND evidence is supplied. If a gate is needed but not
granted: stop, report exactly what is blocked, and wait for the operator.
Never reinterpret the objective as a grant.

## Recovery and stops

- Blocked items carry a `--theme` (e.g. `flaky-test`, `environment`,
  `missing-context`). Re-entering `in_progress` from `blocked` charges that
  theme's recovery budget (falls back to `default`) and requires a fresh
  pipeline preflight in evidence.
- Budget exhausted → the run stops terminally; report and hand back.
- More than `max_consecutive_blocked` blocks in a row → terminal stop.
- A stopped run refuses further transitions. Do not work around a stop.

## Report

At pause or completion, produce the contract's report format: item states,
evidence per terminal state, decisions taken, remaining budget, merge
barrier state, stop reason if any, and the exact next step for whoever
resumes (either engine).
