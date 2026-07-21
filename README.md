# goal-loop

Engine-neutral durable backlog loop for Claude Code CLI and Codex CLI.
One canonical workflow and state contract; thin per-engine skill adapters.
No OpenClaw dependency at runtime or install time.

goal-loop is the outer durable orchestrator (selection, contract, ledger,
lock, recovery, authority, reconciliation, resume). Since v0.2.0 the
installed **agent-pipeline** skill is the mandatory inner executor: every
selected issue is owned by the pipeline from planning through
`pipeline:ready-to-deploy` (Claude: `/pipeline`, Codex: `$pipeline`). If
the pipeline skill or its preflight is unavailable, the run fails closed.

## Native `/goal` bootstrap

Both engines expose a built-in `/goal` primitive that is the operator-owned
prerequisite for autonomous goal-loop use:

- Claude Code: start native `/goal`, then invoke `/goal-loop`.
- Codex CLI: start native `/goal`, then invoke `$goal-loop`.

These are plain-text instructions to type, not clickable command links or
invocations goal-loop performs recursively. For a bounded single task, stay
with a normal prompt — not native Goal mode, not goal-loop. Native goal
status and completion are owned by the host/session and are not detected,
verified, or controlled by this standalone skill; complete native `/goal`
only after goal-loop's own done definition is met and a final
reconciliation pass has run.

## Layout

- `state.py` — stdlib-only CLI owning all durable run state (contracts,
  ledger, exclusive lock, append-only `events.jsonl`, decisions, merge
  barrier, pipeline preflight). State lives under
  `${XDG_STATE_HOME:-~/.local/state}/goal-loop` (override with
  `GOAL_LOOP_STATE_HOME`).
- `workflow/LOOP.md` — the canonical workflow both engines follow.
- `adapters/claude/`, `adapters/codex/` — thin skill projections; the Codex
  one carries `agents/openai.yaml` per Codex skill conventions.
- `schemas/` — JSON Schema for the contract and ledger (`@2`).
- `fixtures/discovery.json` — example discovery input (also installed as
  `references/discovery.example.json`).
- `install.py` — idempotent installer/uninstaller with an ownership
  manifest and sha256 checksums.

## Install

```sh
python3 install.py install            # both engines, into $HOME
python3 install.py install --engine claude
python3 install.py install --upgrade  # safe upgrade of a managed install
python3 install.py verify
python3 install.py uninstall
```

Targets: `~/.claude/skills/goal-loop` and `~/.codex/skills/goal-loop`.
Use `--prefix DIR` or `GOAL_LOOP_INSTALL_PREFIX` to redirect (tests do).
The installer refuses to touch unmanaged directories and never overwrites
files its manifest does not own.

## Guarantees

- Atomic writes (temp file + fsync + rename) and an append-only event log.
- One exclusive lock per run; stale locks are inspectable and recoverable,
  live locks are never silently stolen.
- **Mandatory agent-pipeline execution**: contracts compile with a fixed
  `execution.mode = agent-pipeline` block (engine-neutral: `/pipeline` and
  `$pipeline` invocations, deterministic `pipeline.mjs` entrypoints,
  preflight and merge-surface commands). `state.py pipeline-preflight`
  fails closed (exit 7) when the installed skill is missing; entering
  `in_progress` requires preflight-pass evidence, and `ready` requires
  verified `pipeline:ready-to-deploy` stage evidence.
- Authority gates (push/PR, merge, release, deploy) require explicit
  contract grants plus direct evidence; broad objectives grant nothing.
  Without the merge grant, items stop at ready-to-deploy.
- **Serialized merge → refresh → next**: with the merge grant, merges go
  only through the pipeline merge surface; a `merged` transition sets a
  ledger merge barrier that refuses to start the next item (exit 6) until a
  reconcile proves the merged SHA is reachable from a refreshed base.
- Recovery budgets per blocker theme and terminal stop conditions.
- Resume from disk + live repo truth; both engines compile byte-identical
  canonical contracts from the same discovery.

## Tests

```sh
python3 -m unittest discover -s tests -v
```
