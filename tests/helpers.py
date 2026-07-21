import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state.py"
INSTALL = ROOT / "install.py"
FIXTURE = ROOT / "fixtures" / "discovery.json"

# Canonical evidence payloads for the mandatory agent-pipeline envelope.
PREFLIGHT_EV = '{"pipeline": {"preflight": "pass"}}'
READY_EV = '{"pipeline": {"stage": "pipeline:ready-to-deploy"}, "checks": "green"}'
MERGE_EV = '{"merge": {"via": "pipeline-merge", "sha": "def456", "checks": "green"}}'


def run_cli(script, args, state_home=None, extra_env=None):
    env = dict(os.environ)
    if state_home:
        env["GOAL_LOOP_STATE_HOME"] = str(state_home)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env)
    return proc


def state(args, state_home, **kw):
    return run_cli(STATE, args, state_home=state_home, **kw)


def state_json(args, state_home, **kw):
    proc = state(args, state_home, **kw)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def native_goal_evidence(engine, run_id, status="active", stale=False,
                         run_id_override=None, engine_override=None):
    """Build a JSON string {"native_goal": {...}} matching the shape
    state.py's validate_native_goal_evidence expects. A fresh, self-attested
    claim that the engine's native /goal primitive is active — never
    independently detected."""
    at = datetime.now(timezone.utc)
    if stale:
        at -= timedelta(seconds=400)
    return json.dumps({
        "native_goal": {
            "engine": engine_override if engine_override is not None else engine,
            "run_id": run_id_override if run_id_override is not None else run_id,
            "status": status,
            "checked_at": at.isoformat(timespec="seconds"),
        }
    })


def IN_PROGRESS_EV(engine, run_id, **kw):
    """Combined --evidence for a transition to in_progress: both the
    mandatory pipeline-preflight evidence and a fresh native-goal
    self-attestation, since both are validated from the same --evidence
    flag at that call site."""
    ng = json.loads(native_goal_evidence(engine, run_id, **kw))
    return json.dumps({"pipeline": {"preflight": "pass"}, **ng})


def make_run(state_home, run_id="r1", adapter="claude", discovery=None, engine=None):
    """Compile the fixture discovery into a contract and init a run."""
    disc = Path(state_home) / "discovery.json"
    disc.write_text(json.dumps(discovery) if discovery else FIXTURE.read_text())
    contract_path = Path(state_home) / "contract.json"
    init_engine = engine if engine else adapter
    state_json(["compile-contract", "--discovery", str(disc),
                "--adapter", adapter, "--run-id", run_id,
                "--out", str(contract_path),
                "--native-goal-evidence",
                native_goal_evidence(adapter, run_id)], state_home)
    state_json(["init", "--contract", str(contract_path),
                "--engine", init_engine,
                "--native-goal-evidence",
                native_goal_evidence(init_engine, run_id)], state_home)
    return run_id


def acquire(state_home, run_id, engine, pid=None, resume_evidence=None):
    args = ["lock", "acquire", "--run", run_id, "--engine", engine,
            "--pid", str(pid if pid else os.getpid())]
    if resume_evidence is not None:
        args += ["--native-goal-evidence", resume_evidence]
    return state_json(args, state_home)["token"]
