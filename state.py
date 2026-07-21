#!/usr/bin/env python3
"""goal-loop state helper.

Deterministic, stdlib-only CLI that owns all durable state for a goal-loop
run. Engines (Claude Code, Codex CLI) never edit state files directly; they
call this helper so both sides share one contract, one ledger, one lock.

State lives under ${GOAL_LOOP_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/goal-loop}.

Exit codes: 0 ok, 2 usage/validation, 3 lock, 4 authority gate, 5 stop/recovery,
6 conflict, 7 pipeline mandate (agent-pipeline unavailable or bypassed),
8 native goal mandate (native /goal bootstrap missing, inactive, or stale).
"""

import argparse
import json
import os
import socket
import sys
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

__version__ = "0.3.0"

CONTRACT_SCHEMA = "goal-loop/contract@3"
LEDGER_SCHEMA = "goal-loop/ledger@2"

# Execution is mandatory agent-pipeline: every selected item is owned by the
# installed pipeline skill from planning through pipeline:ready-to-deploy.
# Both engines are listed so the block is engine-neutral and canonical-hash
# stable across adapters. Paths use "~" literally; pipeline-preflight resolves
# them against the real prefix at run time.
PIPELINE_HANDOFF_STAGE = "pipeline:ready-to-deploy"
PIPELINE_ENGINES = {
    "claude": {"invocation": "/pipeline",
               "skill_dir": "~/.claude/skills/pipeline",
               "entrypoint": "~/.claude/skills/pipeline/scripts/pipeline.mjs"},
    "codex": {"invocation": "$pipeline",
              "skill_dir": "~/.codex/skills/pipeline",
              "entrypoint": "~/.codex/skills/pipeline/scripts/pipeline.mjs"},
}


def execution_block():
    engines = {}
    for eng, spec in PIPELINE_ENGINES.items():
        engines[eng] = {
            **spec,
            "preflight": f"node {spec['entrypoint']} doctor --json",
            "merge_surface": f"node {spec['entrypoint']} merge <pr>",
        }
    return {
        "mode": "agent-pipeline",
        "handoff_stage": PIPELINE_HANDOFF_STAGE,
        "engines": engines,
    }


# Native goal bootstrap is mandatory before any item may enter in_progress.
# state.py cannot independently detect native Goal-mode session state (the
# engine's own /goal primitive); it can only validate the SHAPE and
# FRESHNESS of a caller-supplied self-attestation, exactly like it already
# trusts pipeline evidence without re-running the doctor command itself.
NATIVE_GOAL_ENGINES = {
    "claude": {"bootstrap": "/goal", "loop_invocation": "/goal-loop"},
    "codex": {"bootstrap": "/goal", "loop_invocation": "$goal-loop"},
}
NATIVE_GOAL_TTL_SECONDS = 300


def native_goal_block():
    return {
        "mode": "native-goal-required",
        "engines": {eng: dict(spec) for eng, spec in NATIVE_GOAL_ENGINES.items()},
    }


def validate_native_goal_evidence(evidence, run_id, engine):
    """Validate a fresh, caller-supplied attestation that the engine's
    native /goal primitive is active. This is self-attested, not detected:
    state.py has no way to independently observe native Goal-mode session
    state. Only status == 'active' (matching run_id and engine, checked_at
    within NATIVE_GOAL_TTL_SECONDS) passes; everything else fails closed
    with exit code 8. Returns the validated native_goal sub-dict."""
    spec = NATIVE_GOAL_ENGINES.get(engine, {"bootstrap": "/goal",
                                             "loop_invocation": "/goal-loop"})
    corrective = (f"re-run the native bootstrap for engine '{engine}': "
                  f"{spec['bootstrap']} then {spec['loop_invocation']}, then "
                  "retry with fresh native-goal evidence.")

    def fail(reason):
        raise CliError(f"native goal bootstrap mandate: {reason}. {corrective}", 8)

    ng = evidence.get("native_goal") if isinstance(evidence, dict) else None
    if not isinstance(ng, dict):
        fail("missing 'native_goal' evidence")
    if ng.get("engine") != engine:
        fail(f"native_goal.engine must equal the acting engine '{engine}' "
             f"(got {ng.get('engine')!r})")
    if ng.get("run_id") != run_id:
        fail(f"native_goal.run_id must equal the run in play '{run_id}' "
             f"(got {ng.get('run_id')!r})")
    if ng.get("status") != "active":
        fail(f"native_goal.status must be 'active' (got {ng.get('status')!r})")
    checked_at = ng.get("checked_at")
    if not isinstance(checked_at, str):
        fail("native_goal.checked_at must be an ISO8601 string")
    try:
        checked_dt = datetime.fromisoformat(checked_at)
    except ValueError:
        fail(f"native_goal.checked_at is not a valid ISO8601 timestamp "
             f"(got {checked_at!r})")
    if checked_dt.tzinfo is None:
        checked_dt = checked_dt.replace(tzinfo=timezone.utc)
    age = abs((datetime.now(timezone.utc) - checked_dt).total_seconds())
    if age > NATIVE_GOAL_TTL_SECONDS:
        fail(f"native_goal.checked_at is stale ({age:.0f}s old, "
             f"max {NATIVE_GOAL_TTL_SECONDS}s)")
    return ng

# Item state machine. Gate transitions additionally require explicit authority
# in the contract plus direct evidence — an agent claim is never enough.
TRANSITIONS = {
    "pending": {"in_progress", "blocked", "abandoned"},
    "in_progress": {"implemented", "blocked", "abandoned"},
    "implemented": {"pr_opened", "blocked", "abandoned"},
    "pr_opened": {"ready", "blocked", "abandoned"},
    "ready": {"merged", "blocked", "abandoned"},
    "merged": {"released", "deployed"},
    "released": {"deployed"},
    "blocked": {"in_progress", "abandoned"},
    "deployed": set(),
    "abandoned": set(),
}
GATES = {
    "pr_opened": "push_pr",
    "merged": "merge",
    "released": "release",
    "deployed": "deploy",
}
GATE_NAMES = ("push_pr", "merge", "release", "deploy")
TERMINAL_STATES = {"deployed", "abandoned"}


class CliError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_home():
    env = os.environ.get("GOAL_LOOP_STATE_HOME")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "goal-loop"


def run_dir(run_id, must_exist=True):
    d = state_home() / "runs" / run_id
    if must_exist and not d.is_dir():
        raise CliError(f"run '{run_id}' not found under {d.parent}", 2)
    return d


def canonical_dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def atomic_write_json(path, obj):
    """Write JSON atomically: temp file in the same directory, fsync, rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    data = json.dumps(obj, sort_keys=True, indent=2) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path, obj):
    """Append one JSON line with a single O_APPEND write."""
    line = canonical_dumps(obj) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path):
    if not Path(path).exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def emit_event(rdir, kind, payload=None):
    events = rdir / "events.jsonl"
    seq = sum(1 for _ in open(events, "rb")) if events.exists() else 0
    append_jsonl(events, {"seq": seq, "at": now_iso(), "kind": kind, "data": payload or {}})


# ---------------------------------------------------------------- contract

def topo_order(items):
    """Deterministic dependency-aware order (Kahn, ties broken by id)."""
    ids = [it["id"] for it in items]
    if len(set(ids)) != len(ids):
        raise CliError("duplicate item ids in snapshot", 2)
    by_id = {it["id"]: it for it in items}
    deps = {i: set(by_id[i].get("deps", [])) & set(ids) for i in ids}
    order, ready = [], sorted(i for i in ids if not deps[i])
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        newly = []
        for i in ids:
            if cur in deps[i]:
                deps[i].discard(cur)
                if not deps[i] and i not in order and i not in ready:
                    newly.append(i)
        ready = sorted(ready + newly)
    if len(order) != len(ids):
        raise CliError("dependency cycle in snapshot items", 2)
    return [by_id[i] for i in order]


def compile_contract(discovery, adapter, run_id):
    """Compile a canonical run contract. Authority comes ONLY from explicit
    grants in discovery — objective/backlog wording never widens it."""
    for key in ("repo", "selector", "snapshot_items"):
        if key not in discovery:
            raise CliError(f"discovery missing required key '{key}'", 2)
    grants = discovery.get("authority_grants", [])
    if not isinstance(grants, list):
        raise CliError("authority_grants must be an explicit list", 2)
    unknown = [g for g in grants if g not in GATE_NAMES]
    if unknown:
        raise CliError(f"unknown authority grants: {unknown}", 2)
    exec_override = discovery.get("execution") or {}
    if exec_override.get("mode") not in (None, "agent-pipeline"):
        raise CliError(
            "execution.mode is fixed to 'agent-pipeline'; goal-loop refuses to "
            "compile a contract that bypasses the pipeline", 2)
    items = topo_order(discovery["snapshot_items"])
    contract = {
        "schema": CONTRACT_SCHEMA,
        "version": __version__,
        "run_id": run_id,
        "adapter": adapter,
        "repo": discovery["repo"],
        "selector": discovery["selector"],
        "objective": discovery.get("objective", ""),
        "items": items,
        "ordering": "dependency-aware-sequential",
        "max_active_items": 1,
        "execution": execution_block(),
        "native_goal": native_goal_block(),
        "worktree_policy": discovery.get("worktree_policy", "per-item-worktree"),
        "done_definition": discovery.get(
            "done_definition", "verified terminal state per item; never chat claims"
        ),
        "authority": {g: (g in grants) for g in GATE_NAMES},
        "concurrency": {"exclusive_lock": True, "single_engine_advance": True},
        "recovery": {
            "budgets": dict(discovery.get("recovery", {}).get("budgets", {"default": 2}))
        },
        "stops": {
            "max_consecutive_blocked": discovery.get("stops", {}).get(
                "max_consecutive_blocked", 3
            ),
            "on_recovery_exhausted": "stop",
            "on_authority_needed": "stop-and-report",
        },
        "verification": discovery.get("verification", {"commands": [], "checks": []}),
        "report": discovery.get("report", {"format": "markdown-summary"}),
    }
    body = {k: v for k, v in contract.items() if k not in ("adapter", "canonical_hash")}
    contract["canonical_hash"] = sha256(canonical_dumps(body).encode()).hexdigest()
    return contract


# ---------------------------------------------------------------- lock

def lock_path(rdir):
    return rdir / "lock.json"


def read_lock(rdir):
    p = lock_path(rdir)
    if not p.exists():
        return None
    return load_json(p)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_staleness(holder):
    """Return (is_stale, why). Only verifiably-dead same-host locks are stale."""
    if holder.get("hostname") != socket.gethostname():
        return False, "held from another host; cannot verify liveness"
    if pid_alive(int(holder["pid"])):
        return False, f"pid {holder['pid']} is alive"
    return True, f"pid {holder['pid']} is dead on this host"


def require_lock(rdir, token):
    holder = read_lock(rdir)
    if holder is None:
        raise CliError("no lock held; run 'lock acquire' first", 3)
    if not token or token != holder.get("token"):
        raise CliError(
            f"lock is held by engine '{holder.get('engine')}' (pid {holder.get('pid')}); "
            "provide the matching --token", 3)
    return holder


# ---------------------------------------------------------------- ledger ops

def load_state(run_id):
    rdir = run_dir(run_id)
    return rdir, load_json(rdir / "contract.json"), load_json(rdir / "ledger.json")


def check_not_stopped(ledger):
    stop = ledger.get("stop")
    if stop:
        raise CliError(f"run is terminally stopped: {canonical_dumps(stop)}", 5)


def do_transition(run_id, item_id, to_state, token, theme=None, evidence=None, note=None):
    rdir, contract, ledger = load_state(run_id)
    holder = require_lock(rdir, token)
    check_not_stopped(ledger)
    item = ledger["items"].get(item_id)
    if item is None:
        raise CliError(f"unknown item '{item_id}'", 2)
    cur = item["state"]
    if to_state not in TRANSITIONS.get(cur, set()):
        raise CliError(f"invalid transition {cur} -> {to_state} for '{item_id}'", 2)

    pipeline_required = (contract.get("execution") or {}).get("mode") == "agent-pipeline"
    barrier = ledger.get("merge_barrier")
    if barrier and to_state == "in_progress":
        raise CliError(
            f"merge barrier active for item '{barrier['item']}' "
            f"(merged sha {barrier['merged_sha']}): fetch and fast-forward the "
            "base branch, run post-merge cleanup, then reconcile with the "
            "merged sha in truth.merged_shas before starting the next item.", 6)

    gate = GATES.get(to_state)
    if gate:
        if not contract["authority"].get(gate, False):
            raise CliError(
                f"authority gate '{gate}' not granted by contract; transition to "
                f"'{to_state}' refused. Broad objectives do not grant gates — stop "
                "and report.", 4)
        if not evidence:
            raise CliError(
                f"transition to '{to_state}' requires --evidence with directly "
                "verified facts (PR/checks/SHA), not an agent claim", 2)

    if pipeline_required:
        if to_state == "in_progress":
            pf = ((evidence or {}).get("pipeline") or {}).get("preflight")
            if pf != "pass":
                raise CliError(
                    "agent-pipeline is mandatory: entering 'in_progress' requires "
                    '--evidence with {"pipeline": {"preflight": "pass", ...}} from '
                    "a real 'pipeline-preflight' + doctor run. If the installed "
                    "pipeline skill or its preflight is unavailable, fail closed: "
                    "do not start the item.", 7)
        elif to_state == "ready":
            stage = ((evidence or {}).get("pipeline") or {}).get("stage")
            if stage != PIPELINE_HANDOFF_STAGE:
                raise CliError(
                    "agent-pipeline owns the item through "
                    f"{PIPELINE_HANDOFF_STAGE}: 'ready' requires --evidence with "
                    '{"pipeline": {"stage": "pipeline:ready-to-deploy"}} verified '
                    "from the live issue labels.", 7)
        elif to_state == "merged":
            m = (evidence or {}).get("merge") or {}
            if m.get("via") != "pipeline-merge" or not m.get("sha"):
                raise CliError(
                    "merges must go through the pipeline merge surface after "
                    "ready-to-deploy: 'merged' requires --evidence with "
                    '{"merge": {"via": "pipeline-merge", "sha": ..., '
                    '"checks": ...}} verified directly.', 7)

    native_goal_check = None
    if to_state == "in_progress":
        native_goal_check = validate_native_goal_evidence(
            evidence, run_id, engine=holder["engine"])

    charged = None
    if cur == "blocked" and to_state == "in_progress":
        th = item.get("blocked_theme") or "default"
        remaining = ledger["recovery_remaining"]
        key = th if th in remaining else "default"
        if remaining.get(key, 0) <= 0:
            ledger["stop"] = {"reason": "recovery_exhausted", "theme": th, "item": item_id}
            atomic_write_json(rdir / "ledger.json", ledger)
            emit_event(rdir, "stop", ledger["stop"])
            raise CliError(f"recovery budget for theme '{th}' exhausted; run stopped", 5)
        remaining[key] -= 1
        charged = {"theme": key, "remaining": remaining[key]}

    if to_state == "blocked":
        if not theme:
            raise CliError("transition to 'blocked' requires --theme", 2)
        item["blocked_theme"] = theme
        ledger["consecutive_blocked"] = ledger.get("consecutive_blocked", 0) + 1
        limit = contract["stops"]["max_consecutive_blocked"]
        if ledger["consecutive_blocked"] > limit:
            ledger["stop"] = {"reason": "max_consecutive_blocked", "limit": limit}
    elif to_state in ("implemented", "pr_opened", "ready", "merged", "released", "deployed"):
        ledger["consecutive_blocked"] = 0

    if to_state == "merged" and pipeline_required:
        ledger["merge_barrier"] = {"item": item_id,
                                   "merged_sha": evidence["merge"]["sha"],
                                   "set_at": now_iso()}

    item["state"] = to_state
    entry = {"at": now_iso(), "from": cur, "to": to_state, "engine": holder["engine"]}
    if theme:
        entry["theme"] = theme
    if evidence:
        entry["evidence"] = evidence
    if note:
        entry["note"] = note
    if charged:
        entry["recovery_charged"] = charged
    if native_goal_check:
        entry["native_goal_check"] = native_goal_check
        ledger["last_native_goal_check"] = native_goal_check
    item["history"].append(entry)
    atomic_write_json(rdir / "ledger.json", ledger)
    emit_event(rdir, "transition", {"item": item_id, **entry})
    if to_state == "merged" and ledger.get("merge_barrier"):
        emit_event(rdir, "merge_barrier_set", ledger["merge_barrier"])
    if ledger.get("stop"):
        emit_event(rdir, "stop", ledger["stop"])
    return {"item": item_id, "state": to_state, "recovery_charged": charged,
            "merge_barrier": ledger.get("merge_barrier"),
            "stop": ledger.get("stop")}


def status_payload(run_id):
    rdir, contract, ledger = load_state(run_id)
    holder = read_lock(rdir)
    lock_info = None
    if holder:
        stale, why = lock_staleness(holder)
        lock_info = {"engine": holder["engine"], "pid": holder["pid"],
                     "hostname": holder["hostname"], "acquired_at": holder["acquired_at"],
                     "stale": stale, "detail": why}
    events = read_jsonl(rdir / "events.jsonl")
    states = {i: it["state"] for i, it in sorted(ledger["items"].items())}
    active = [i for i, s in states.items() if s in ("in_progress", "implemented",
                                                    "pr_opened", "ready")]
    return {
        "run_id": run_id,
        "adapter": contract["adapter"],
        "repo": contract["repo"],
        "canonical_hash": contract["canonical_hash"],
        "items": states,
        "active": active,
        "recovery_remaining": ledger["recovery_remaining"],
        "consecutive_blocked": ledger.get("consecutive_blocked", 0),
        "merge_barrier": ledger.get("merge_barrier"),
        "stop": ledger.get("stop"),
        "last_native_goal_check": ledger.get("last_native_goal_check"),
        "lock": lock_info,
        "last_reconcile": (ledger.get("reconciled") or {}).get("at"),
        "events": len(events),
        "last_event": events[-1] if events else None,
    }


# ---------------------------------------------------------------- commands

def cmd_compile_contract(args):
    discovery = load_json(args.discovery)
    contract = compile_contract(discovery, args.adapter, args.run_id)
    if args.out:
        # Writing the contract artifact is external mutation gated on the
        # same Phase 0 native-goal bootstrap as 'init' and 'transition'; an
        # in-memory/stdout-only compile (no --out) stays evidence-free.
        validate_native_goal_evidence(
            json.loads(args.native_goal_evidence) if args.native_goal_evidence else None,
            args.run_id, engine=args.adapter)
        atomic_write_json(args.out, contract)
    print(json.dumps(contract, sort_keys=True, indent=2))


def cmd_init(args):
    contract = load_json(args.contract)
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise CliError(f"contract schema must be {CONTRACT_SCHEMA}", 2)
    run_id = contract["run_id"]
    native_goal_check = validate_native_goal_evidence(
        json.loads(args.native_goal_evidence) if args.native_goal_evidence else None,
        run_id, engine=args.engine)
    rdir = state_home() / "runs" / run_id
    if rdir.exists():
        raise CliError(f"run '{run_id}' already exists; resume it instead", 6)
    rdir.mkdir(parents=True)
    ledger = {
        "schema": LEDGER_SCHEMA,
        "run_id": run_id,
        "created_at": now_iso(),
        "items": {it["id"]: {"state": "pending", "history": []}
                  for it in contract["items"]},
        "recovery_remaining": dict(contract["recovery"]["budgets"]),
        "consecutive_blocked": 0,
        "reconciled": None,
        "merge_barrier": None,
        "stop": None,
        "last_native_goal_check": native_goal_check,
    }
    atomic_write_json(rdir / "contract.json", contract)
    atomic_write_json(rdir / "ledger.json", ledger)
    emit_event(rdir, "run_initialized", {"adapter": contract["adapter"],
                                         "canonical_hash": contract["canonical_hash"]})
    print(json.dumps({"run_id": run_id, "dir": str(rdir)}))


def cmd_lock(args):
    rdir = run_dir(args.run)
    lp = lock_path(rdir)
    if args.lock_cmd == "acquire":
        # A paused-but-in_progress item releases the lock without any state
        # transition; the next acquire is the only choke point on the resume
        # path, so it must re-validate native-goal evidence just like entry
        # into in_progress does. Validated (and persisted to the ledger)
        # BEFORE the lock file is created, so a rejected resume never leaves
        # a lock behind for a caller who can't proceed.
        ledger_path = rdir / "ledger.json"
        resume_check = None
        if ledger_path.exists():
            ledger = load_json(ledger_path)
            in_progress = [i for i, it in ledger["items"].items()
                          if it["state"] == "in_progress"]
            if in_progress:
                evidence = (json.loads(args.native_goal_evidence)
                           if args.native_goal_evidence else None)
                resume_check = validate_native_goal_evidence(
                    evidence, args.run, args.engine)
        # The CLI process exits immediately, so its own pid would always look
        # dead. Default to the parent (the engine session) unless overridden.
        pid = args.pid if args.pid else os.getppid()
        payload = {"engine": args.engine, "pid": pid,
                   "hostname": socket.gethostname(), "acquired_at": now_iso(),
                   "token": uuid.uuid4().hex, "run_id": args.run}
        try:
            fd = os.open(lp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            holder = read_lock(rdir)
            stale, why = lock_staleness(holder) if holder else (False, "unreadable")
            raise CliError(
                f"lock already held by engine '{holder.get('engine')}' "
                f"(pid {holder.get('pid')} on {holder.get('hostname')}); stale={stale} "
                f"({why}). Use 'lock recover' only if stale.", 3)
        try:
            os.write(fd, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        if resume_check:
            ledger["last_native_goal_check"] = resume_check
            atomic_write_json(ledger_path, ledger)
            emit_event(rdir, "native_goal_resume_check", resume_check)
        emit_event(rdir, "lock_acquired", {"engine": args.engine, "pid": payload["pid"]})
        print(json.dumps(payload))
    elif args.lock_cmd == "release":
        holder = require_lock(rdir, args.token)
        os.unlink(lp)
        emit_event(rdir, "lock_released", {"engine": holder["engine"]})
        print(json.dumps({"released": True}))
    elif args.lock_cmd == "status":
        holder = read_lock(rdir)
        if holder is None:
            print(json.dumps({"held": False}))
            return
        stale, why = lock_staleness(holder)
        print(json.dumps({"held": True, "engine": holder["engine"],
                          "pid": holder["pid"], "hostname": holder["hostname"],
                          "acquired_at": holder["acquired_at"],
                          "stale": stale, "detail": why}, indent=2))
    elif args.lock_cmd == "recover":
        holder = read_lock(rdir)
        if holder is None:
            print(json.dumps({"recovered": False, "reason": "no lock held"}))
            return
        stale, why = lock_staleness(holder)
        if not stale and not args.force:
            raise CliError(
                f"refusing to recover: lock does not look stale ({why}). "
                "An active lock is never stolen silently; pass --force only with "
                "explicit operator intent.", 3)
        os.unlink(lp)
        kind = "lock_recovered_stale" if stale else "lock_broken_forcibly"
        emit_event(rdir, kind, {"previous_holder": holder, "detail": why})
        print(json.dumps({"recovered": True, "was_stale": stale, "event": kind}))


def cmd_transition(args):
    evidence = json.loads(args.evidence) if args.evidence else None
    result = do_transition(args.run, args.item, args.to, args.token,
                           theme=args.theme, evidence=evidence, note=args.note)
    print(json.dumps(result, sort_keys=True))


def cmd_decision(args):
    rdir = run_dir(args.run)
    require_lock(rdir, args.token)
    rec = {"at": now_iso(), "decision": args.text}
    if args.item:
        rec["item"] = args.item
    append_jsonl(rdir / "decisions.jsonl", rec)
    emit_event(rdir, "decision", rec)
    print(json.dumps({"recorded": True}))


def cmd_event(args):
    rdir = run_dir(args.run)
    require_lock(rdir, args.token)
    emit_event(rdir, args.kind, json.loads(args.data) if args.data else {})
    print(json.dumps({"appended": True}))


def cmd_reconcile(args):
    rdir, contract, ledger = load_state(args.run)
    require_lock(rdir, args.token)
    truth = load_json(args.input)
    mismatches = []
    observed = truth.get("items", {})
    for item_id, it in ledger["items"].items():
        obs = observed.get(item_id)
        if obs is None:
            continue
        obs_state = obs.get("state")
        if obs_state and obs_state != it["state"]:
            mismatches.append({"item": item_id, "ledger": it["state"],
                               "observed": obs_state})
    ledger["reconciled"] = {
        "at": now_iso(),
        "seq": ((ledger.get("reconciled") or {}).get("seq", 0)) + 1,
        "base_sha": truth.get("base_sha"),
        "truth": truth,
        "mismatches": mismatches,
    }
    barrier = ledger.get("merge_barrier")
    barrier_cleared = False
    if barrier:
        # The next item may only start once live truth shows the merged sha
        # reachable from a refreshed base branch.
        if truth.get("base_sha") and barrier["merged_sha"] in truth.get("merged_shas", []):
            ledger["merge_barrier"] = None
            barrier_cleared = True
    atomic_write_json(rdir / "ledger.json", ledger)
    emit_event(rdir, "reconciled", {"seq": ledger["reconciled"]["seq"],
                                    "base_sha": truth.get("base_sha"),
                                    "mismatches": mismatches})
    if barrier_cleared:
        emit_event(rdir, "merge_barrier_cleared", barrier)
    print(json.dumps({"mismatches": mismatches,
                      "seq": ledger["reconciled"]["seq"],
                      "merge_barrier": ledger.get("merge_barrier"),
                      "merge_barrier_cleared": barrier_cleared}, sort_keys=True))


def cmd_status(args):
    print(json.dumps(status_payload(args.run), sort_keys=True, indent=2))


def cmd_show(args):
    rdir = run_dir(args.run)
    name = {"contract": "contract.json", "ledger": "ledger.json"}.get(args.what)
    if name:
        print(json.dumps(load_json(rdir / name), sort_keys=True, indent=2))
    elif args.what == "events":
        for e in read_jsonl(rdir / "events.jsonl"):
            print(canonical_dumps(e))
    elif args.what == "decisions":
        for d in read_jsonl(rdir / "decisions.jsonl"):
            print(canonical_dumps(d))


def cmd_pipeline_preflight(args):
    """Deterministic fail-closed check that the engine's installed
    agent-pipeline skill exists. Prints the doctor command the engine must
    then run (and pass) before any item may enter in_progress."""
    if args.prefix:
        base = Path(args.prefix)
    else:
        env = os.environ.get("GOAL_LOOP_INSTALL_PREFIX")
        base = Path(env) if env else Path.home()
    skill_dir = base / f".{args.engine}" / "skills" / "pipeline"
    entrypoint = skill_dir / "scripts" / "pipeline.mjs"
    missing = [str(p) for p in (skill_dir / "SKILL.md", entrypoint)
               if not p.is_file()]
    if missing:
        raise CliError(
            f"agent-pipeline skill unavailable for engine '{args.engine}': "
            f"missing {missing}. goal-loop fails closed — install the pipeline "
            "skill; do not start or advance items without it.", 7)
    print(json.dumps({
        "ok": True,
        "engine": args.engine,
        "invocation": PIPELINE_ENGINES[args.engine]["invocation"],
        "skill_dir": str(skill_dir),
        "entrypoint": str(entrypoint),
        "doctor_cmd": f"node {entrypoint} doctor --json",
        "handoff_stage": PIPELINE_HANDOFF_STAGE,
    }, sort_keys=True, indent=2))


def cmd_runs(args):
    base = state_home() / "runs"
    runs = sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    print(json.dumps({"state_home": str(state_home()), "runs": runs}))


def build_parser():
    p = argparse.ArgumentParser(prog="goal-loop-state", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile-contract", help="compile canonical contract from discovery JSON")
    c.add_argument("--discovery", required=True)
    c.add_argument("--adapter", required=True, choices=["claude", "codex"])
    c.add_argument("--run-id", required=True)
    c.add_argument("--out")
    c.add_argument("--native-goal-evidence",
                   help="JSON native_goal self-attestation; required when "
                        "--out is given (writing the contract artifact is "
                        "gated on the Phase 0 bootstrap, same as 'init')")
    c.set_defaults(fn=cmd_compile_contract)

    c = sub.add_parser("init", help="initialize a run from a compiled contract")
    c.add_argument("--contract", required=True)
    c.add_argument("--engine", required=True, choices=["claude", "codex"])
    c.add_argument("--native-goal-evidence",
                   help="JSON native_goal self-attestation "
                        '({"native_goal": {"engine": ..., "run_id": ..., '
                        '"status": "active", "checked_at": ...}}); required')
    c.set_defaults(fn=cmd_init)

    c = sub.add_parser("lock", help="exclusive run lock")
    c.add_argument("lock_cmd", choices=["acquire", "release", "status", "recover"])
    c.add_argument("--run", required=True)
    c.add_argument("--engine", choices=["claude", "codex"])
    c.add_argument("--pid", type=int, help="engine session pid (default: parent pid)")
    c.add_argument("--token")
    c.add_argument("--force", action="store_true")
    c.add_argument("--native-goal-evidence",
                   help="JSON native_goal self-attestation; required on "
                        "'acquire' if the run has an item already "
                        "in_progress (resume path)")
    c.set_defaults(fn=cmd_lock)

    c = sub.add_parser("transition", help="move an item through the state machine")
    c.add_argument("--run", required=True)
    c.add_argument("--item", required=True)
    c.add_argument("--to", required=True, choices=sorted(TRANSITIONS))
    c.add_argument("--token", required=True)
    c.add_argument("--theme", help="blocker theme (required for 'blocked')")
    c.add_argument("--evidence", help="JSON of directly verified facts (required for gates)")
    c.add_argument("--note")
    c.set_defaults(fn=cmd_transition)

    c = sub.add_parser("decision", help="append a decision record")
    c.add_argument("--run", required=True)
    c.add_argument("--token", required=True)
    c.add_argument("--text", required=True)
    c.add_argument("--item")
    c.set_defaults(fn=cmd_decision)

    c = sub.add_parser("event", help="append a custom event")
    c.add_argument("--run", required=True)
    c.add_argument("--token", required=True)
    c.add_argument("--kind", required=True)
    c.add_argument("--data", help="JSON payload")
    c.set_defaults(fn=cmd_event)

    c = sub.add_parser("reconcile", help="record live repo/remote truth and flag mismatches")
    c.add_argument("--run", required=True)
    c.add_argument("--token", required=True)
    c.add_argument("--input", required=True, help="JSON file of observed live truth")
    c.set_defaults(fn=cmd_reconcile)

    c = sub.add_parser("status", help="run status summary")
    c.add_argument("--run", required=True)
    c.set_defaults(fn=cmd_status)

    c = sub.add_parser("show", help="print contract/ledger/events/decisions")
    c.add_argument("what", choices=["contract", "ledger", "events", "decisions"])
    c.add_argument("--run", required=True)
    c.set_defaults(fn=cmd_show)

    c = sub.add_parser("pipeline-preflight",
                       help="fail-closed check that the engine's agent-pipeline "
                            "skill is installed; prints the doctor command")
    c.add_argument("--engine", required=True, choices=["claude", "codex"])
    c.add_argument("--prefix", help="base dir containing .claude/.codex "
                   "(default: $GOAL_LOOP_INSTALL_PREFIX or $HOME)")
    c.set_defaults(fn=cmd_pipeline_preflight)

    c = sub.add_parser("runs", help="list runs")
    c.set_defaults(fn=cmd_runs)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "lock" and args.lock_cmd == "acquire" and not args.engine:
        print("error: lock acquire requires --engine", file=sys.stderr)
        return 2
    try:
        args.fn(args)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
