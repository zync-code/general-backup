"""capture subcommand — full 14-phase capture pipeline.

Phases (in order):
  preflight, git-sync, inventory, packages, system, nginx, cron,
  postgres, redis, pm2, state, secrets, checksums, package
"""
from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from typing import List

from ..cli import EXIT_OK, EXIT_USER_ERROR, EXIT_GIT_SYNC_CONFLICT
from ..log import error, info, warn
from ..manifest import Manifest
from ..phases import Context, PhaseError

_PHASE_MODULE_MAP = {
    "preflight":  "preflight",
    "git-sync":   "git_sync",
    "inventory":  "inventory",
    "packages":   "packages",
    "system":     "system",
    "nginx":      "nginx",
    "cron":       "cron",
    "postgres":   "postgres",
    "redis":      "redis",
    "pm2":        "pm2",
    "state":      "state",
    "secrets":    "secrets",
    "checksums":  "checksums",
    "package":    "package",
}


def run(args, phases: List[str]) -> int:
    info(f"capture: phases={', '.join(phases)} out={args.out or '<auto>'} dry_run={args.dry_run}")

    if args.dry_run:
        info(f"capture: dry-run — phases that would run: {', '.join(phases)}")
        return EXIT_OK

    staging = Path(tempfile.mkdtemp(prefix="gb-capture-"))
    secrets_staging = Path(tempfile.mkdtemp(prefix="gb-secrets-"))
    try:
        return _run_pipeline(args, phases, staging, secrets_staging)
    finally:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(secrets_staging, ignore_errors=True)


def _run_pipeline(
    args, phases: List[str], staging: Path, secrets_staging: Path
) -> int:
    manifest = Manifest()
    ctx = Context(
        args=args,
        staging=staging,
        secrets_staging=secrets_staging,
        manifest=manifest,
    )

    for phase in phases:
        module_name = _PHASE_MODULE_MAP.get(phase)
        if not module_name:
            warn(f"capture: no implementation for phase {phase!r} — skipping")
            continue

        if phase == "checksums":
            manifest.write(staging / "manifest.json")

        info(f"capture: [{phase}] starting")
        try:
            mod = importlib.import_module(f"lib.phases.{module_name}")
            mod.run(ctx)
            info(f"capture: [{phase}] done")
        except PhaseError as exc:
            error(f"capture: [{phase}] FAILED: {exc}")
            return exc.exit_code
        except Exception as exc:
            error(f"capture: [{phase}] unexpected error: {exc}")
            return 1

    return EXIT_OK
