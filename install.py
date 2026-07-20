#!/usr/bin/env python3
"""goal-loop installer.

Projects the canonical package into native skill directories:
  claude -> <prefix>/.claude/skills/goal-loop
  codex  -> <prefix>/.codex/skills/goal-loop

<prefix> defaults to $GOAL_LOOP_INSTALL_PREFIX or $HOME; override with
--prefix so tests never touch the real HOME. Installs are plain copies plus
an ownership manifest (.goal-loop-manifest.json) with sha256 checksums.

Rules:
  - A target directory without our manifest is unmanaged: refuse to touch it.
  - Re-running install on an identical managed target is a no-op (idempotent).
  - A managed target whose content differs requires --upgrade; upgrade
    replaces/removes only files listed in the old manifest and refuses to
    overwrite any unmanaged file that collides with a shipped one.
  - Uninstall removes only manifest-owned files (verifying checksums unless
    --force) and the directory if it is then empty.

Exit codes: 0 ok, 1 refusal/verify failure, 2 usage.
"""

import argparse
import json
import os
import shutil
import sys
from hashlib import sha256
from pathlib import Path

MANIFEST_NAME = ".goal-loop-manifest.json"
PACKAGE_ROOT = Path(__file__).resolve().parent

# relpath in skill dir -> source path in the package
COMMON_FILES = {
    "state.py": "state.py",
    "references/LOOP.md": "workflow/LOOP.md",
    "references/discovery.example.json": "fixtures/discovery.json",
    "schemas/contract.schema.json": "schemas/contract.schema.json",
    "schemas/ledger.schema.json": "schemas/ledger.schema.json",
}
ENGINE_FILES = {
    "claude": {"SKILL.md": "adapters/claude/SKILL.md", **COMMON_FILES},
    "codex": {
        "SKILL.md": "adapters/codex/SKILL.md",
        "agents/openai.yaml": "adapters/codex/agents/openai.yaml",
        **COMMON_FILES,
    },
}


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def prefix_dir(args):
    if args.prefix:
        return Path(args.prefix)
    env = os.environ.get("GOAL_LOOP_INSTALL_PREFIX")
    return Path(env) if env else Path.home()


def target_dir(engine, args):
    return prefix_dir(args) / f".{engine}" / "skills" / "goal-loop"


def read_manifest(target):
    p = target / MANIFEST_NAME
    if not p.exists():
        return None
    return json.loads(p.read_text())


def version():
    for line in (PACKAGE_ROOT / "state.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    return "0"


def plan(engine):
    files = ENGINE_FILES[engine]
    out = {}
    for rel, src in files.items():
        sp = PACKAGE_ROOT / src
        if not sp.is_file():
            raise SystemExit(f"error: package source missing: {sp}")
        out[rel] = sp
    return out


def copy_all(target, sources):
    manifest = {"package": "goal-loop", "version": version(), "files": {}}
    for rel, src in sorted(sources.items()):
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        manifest["files"][rel] = file_sha(dst)
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


def install_engine(engine, args):
    target = target_dir(engine, args)
    sources = plan(engine)
    if not target.exists():
        target.mkdir(parents=True)
        copy_all(target, sources)
        print(f"{engine}: installed -> {target}")
        return 0

    manifest = read_manifest(target)
    if manifest is None:
        print(f"{engine}: REFUSED — {target} exists and has no goal-loop "
              "manifest (unmanaged content). Remove it manually if you own it.",
              file=sys.stderr)
        return 1

    identical = (
        set(manifest["files"]) == set(sources)
        and all((target / rel).is_file()
                and file_sha(target / rel) == manifest["files"][rel]
                and file_sha(sources[rel]) == manifest["files"][rel]
                for rel in sources)
    )
    if identical:
        print(f"{engine}: already up to date at {target}")
        return 0

    if not args.upgrade:
        print(f"{engine}: REFUSED — managed install at {target} differs from "
              "this package. Re-run with --upgrade for a safe upgrade.",
              file=sys.stderr)
        return 1

    # Safe upgrade: only touch files the old manifest owns; never overwrite
    # a colliding file we do not own.
    owned = set(manifest["files"])
    for rel in sources:
        dst = target / rel
        if dst.exists() and rel not in owned:
            print(f"{engine}: REFUSED — {dst} exists but is not owned by the "
                  "manifest; will not overwrite unmanaged content.",
                  file=sys.stderr)
            return 1
    for rel in owned - set(sources):
        stale = target / rel
        if stale.exists():
            stale.unlink()
    copy_all(target, sources)
    print(f"{engine}: upgraded -> {target}")
    return 0


def uninstall_engine(engine, args):
    target = target_dir(engine, args)
    if not target.exists():
        print(f"{engine}: nothing installed at {target}")
        return 0
    manifest = read_manifest(target)
    if manifest is None:
        print(f"{engine}: REFUSED — {target} has no goal-loop manifest; "
              "not ours to delete.", file=sys.stderr)
        return 1
    for rel, digest in manifest["files"].items():
        p = target / rel
        if not p.exists():
            continue
        if not args.force and file_sha(p) != digest:
            print(f"{engine}: REFUSED — {p} was modified since install "
                  "(checksum mismatch). Use --force to remove anyway.",
                  file=sys.stderr)
            return 1
        p.unlink()
    (target / MANIFEST_NAME).unlink(missing_ok=True)
    # Remove now-empty directories bottom-up; leave anything with foreign files.
    for d in sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    if not any(target.iterdir()):
        target.rmdir()
        print(f"{engine}: uninstalled from {target}")
    else:
        print(f"{engine}: removed owned files; left foreign content in {target}")
    return 0


def verify_engine(engine, args):
    target = target_dir(engine, args)
    manifest = read_manifest(target)
    if manifest is None:
        print(f"{engine}: not installed (no manifest) at {target}",
              file=sys.stderr)
        return 1
    bad = [rel for rel, digest in manifest["files"].items()
           if not (target / rel).is_file() or file_sha(target / rel) != digest]
    if bad:
        print(f"{engine}: VERIFY FAILED at {target}: {bad}", file=sys.stderr)
        return 1
    print(f"{engine}: verified {len(manifest['files'])} files "
          f"(v{manifest['version']}) at {target}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="goal-loop-install", description=__doc__)
    p.add_argument("command", choices=["install", "uninstall", "verify"])
    p.add_argument("--engine", choices=["claude", "codex", "all"], default="all")
    p.add_argument("--prefix", help="base dir containing .claude/.codex "
                   "(default: $GOAL_LOOP_INSTALL_PREFIX or $HOME)")
    p.add_argument("--upgrade", action="store_true",
                   help="allow replacing an existing managed install")
    p.add_argument("--force", action="store_true",
                   help="uninstall: remove owned files even if modified")
    args = p.parse_args(argv)

    engines = ["claude", "codex"] if args.engine == "all" else [args.engine]
    fn = {"install": install_engine, "uninstall": uninstall_engine,
          "verify": verify_engine}[args.command]
    return max(fn(e, args) for e in engines)


if __name__ == "__main__":
    sys.exit(main())
