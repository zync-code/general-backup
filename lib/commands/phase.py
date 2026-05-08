"""phase subcommand — run a single capture- or restore-side phase by name.

The operator escape hatch and the building block used by the agent-mode
restore runbook to invoke individual phases.
"""
from __future__ import annotations

import importlib
import shutil
import tarfile
import tempfile
from pathlib import Path

from ..cli import ALL_CAPTURE_PHASES, ALL_RESTORE_PHASES
from ..log import error, info, warn
from ..manifest import Manifest

_RESTORE_MODULE_MAP = {
    "bootstrap":       "restore_bootstrap",
    "packages":        "restore_packages",
    "users":           "restore_users",
    "state-extract":   "restore_state_extract",
    "secrets-decrypt": "restore_secrets_decrypt",
    "projects-clone":  "restore_projects_clone",
    "postgres":        "restore_postgres",
    "redis":           "restore_redis",
    "nginx":           "restore_nginx",
    "pm2":             "restore_pm2",
    "cron":            "restore_cron",
    "postcheck":       "restore_postcheck",
}


def run(args) -> int:
    name = args.name
    is_restore = name in ALL_RESTORE_PHASES
    is_capture = name in ALL_CAPTURE_PHASES

    info(
        f"phase {name!r} "
        f"({'restore' if is_restore else 'capture'}-side) "
        f"bundle={args.bundle or '<none>'} dry_run={args.dry_run}"
    )

    if is_restore:
        return _run_restore_phase(name, args)

    if is_capture:
        info(f"phase {name!r}: capture-side phases not yet implemented via 'phase' subcommand")
        return 0

    error(f"unknown phase: {name!r}")
    return 1


def _run_restore_phase(name: str, args) -> int:
    bundle_path = getattr(args, "bundle", None)
    if not bundle_path:
        error(f"phase {name!r} requires --bundle <path>")
        return 1

    bundle = Path(bundle_path)
    if not bundle.is_file():
        error(f"bundle not found: {bundle}")
        return 1

    # Extract bundle
    staging_dir = Path(tempfile.mkdtemp(prefix=f"gb-phase-{name}-"))
    try:
        with tarfile.open(bundle, "r:*") as tf:
            tf.extractall(staging_dir, filter="data")
    except Exception as exc:
        error(f"failed to extract bundle: {exc}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return 2

    tops = [p for p in staging_dir.iterdir() if p.is_dir()]
    if not tops:
        error("bundle appears empty")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return 2

    bundle_root = tops[0]
    manifest_path = bundle_root / "manifest.json"
    if not manifest_path.exists():
        error("manifest.json not found in bundle")
        return 2

    try:
        manifest = Manifest.read(manifest_path)
    except Exception as exc:
        error(f"failed to parse manifest.json: {exc}")
        return 2

    from ..phases.restore_base import RestoreContext, RestoreError, mark_done
    ctx = RestoreContext(
        args=args,
        bundle_root=bundle_root,
        manifest=manifest,
        target_user=getattr(args, "target_user", "bot"),
    )

    module_name = _RESTORE_MODULE_MAP.get(name)
    if not module_name:
        error(f"no implementation for phase {name!r}")
        return 1

    try:
        mod = importlib.import_module(f"lib.phases.{module_name}")
        mod.run(ctx)
        mark_done(name)
        info(f"phase {name!r}: done")
        return 0
    except RestoreError as exc:
        error(f"phase {name!r} failed: {exc}")
        return exc.exit_code
    except Exception as exc:
        error(f"phase {name!r} unexpected error: {exc}")
        return 1
