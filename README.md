# goal-loop

Engine-neutral durable backlog loop for Claude Code CLI and Codex CLI.
One canonical workflow and state contract; thin per-engine skill adapters.
No OpenClaw dependency at runtime or install time.

## Layout

- `state.py` — stdlib-only CLI owning all durable run state (contracts,
  ledger, exclusive lock, append-only `events.jsonl`, decisions). State
  lives under `${XDG_STATE_HOME:-~/.local/state}/goal-loop` (override with
  `GOAL_LOOP_STATE_HOME`).
- `workflow/LOOP.md` — the canonical workflow both engines follow.
- `adapters/claude/`, `adapters/codex/` — thin skill projections; the Codex
  one carries `agents/openai.yaml` per Codex skill conventions.
- `schemas/` — JSON Schema for the contract and ledger.
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
- Authority gates (push/PR, merge, release, deploy) require explicit
  contract grants plus direct evidence; broad objectives grant nothing.
- Recovery budgets per blocker theme and terminal stop conditions.
- Resume from disk + live repo truth; both engines compile byte-identical
  canonical contracts from the same discovery.

## Tests

```sh
python3 -m unittest discover -s tests -v
```
