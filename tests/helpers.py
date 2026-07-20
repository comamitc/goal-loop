import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state.py"
INSTALL = ROOT / "install.py"
FIXTURE = ROOT / "fixtures" / "discovery.json"


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


def make_run(state_home, run_id="r1", adapter="claude", discovery=None):
    """Compile the fixture discovery into a contract and init a run."""
    disc = Path(state_home) / "discovery.json"
    disc.write_text(json.dumps(discovery) if discovery else FIXTURE.read_text())
    contract_path = Path(state_home) / "contract.json"
    state_json(["compile-contract", "--discovery", str(disc),
                "--adapter", adapter, "--run-id", run_id,
                "--out", str(contract_path)], state_home)
    state_json(["init", "--contract", str(contract_path)], state_home)
    return run_id


def acquire(state_home, run_id, engine, pid=None):
    args = ["lock", "acquire", "--run", run_id, "--engine", engine,
            "--pid", str(pid if pid else os.getpid())]
    return state_json(args, state_home)["token"]
