"""restore subcommand — script-mode restore pipeline.

Each phase is loaded dynamically from lib.phases.restore_<name>.
Phases write done-markers under /var/lib/general-backup/state/<phase>.ok;
re-running restore skips completed phases.
"""
from __future__ import annotations

import importlib
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import List

from ..cli import EXIT_OK, EXIT_USER_ERROR, EXIT_INTEGRITY, EXIT_PARTIAL
from ..log import error, info, warn
from ..manifest import Manifest
from ..phases.restore_base import (
    RestoreContext,
    RestoreError,
    STATE_DIR,
    is_done,
    mark_done,
)

_PHASE_MODULE_MAP = {
    "bootstrap":      "restore_bootstrap",
    "packages":       "restore_packages",
    "users":          "restore_users",
    "state-extract":  "restore_state_extract",
    "secrets-decrypt":"restore_secrets_decrypt",
    "projects-clone": "restore_projects_clone",
    "postgres":       "restore_postgres",
    "redis":          "restore_redis",
    "nginx":          "restore_nginx",
    "pm2":            "restore_pm2",
    "cron":           "restore_cron",
    "postcheck":      "restore_postcheck",
}


def run(args, phases: List[str]) -> int:
    bundle = Path(args.bundle)
    if not bundle.is_file():
        error(f"bundle not found: {bundle}")
        return EXIT_USER_ERROR

    info(f"restore: bundle={bundle.name} phases={phases} dry_run={args.dry_run}")

    if args.dry_run:
        info("restore: dry-run — planned phases:")
        for phase in phases:
            done = " [already done]" if is_done(phase) else ""
            info(f"  {phase}{done}")
        return EXIT_OK

    # Extract bundle once to a staging directory (persisted across phases)
    staging_dir = Path(tempfile.mkdtemp(prefix="gb-restore-"))
    info(f"restore: extracting bundle to {staging_dir}")
    try:
        with tarfile.open(bundle, "r:*") as tf:
            tf.extractall(staging_dir, filter="data")
    except Exception as exc:
        error(f"failed to extract bundle: {exc}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return EXIT_INTEGRITY

    tops = [p for p in staging_dir.iterdir() if p.is_dir()]
    if not tops:
        error("bundle appears empty")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return EXIT_INTEGRITY

    bundle_root = tops[0]
    manifest_path = bundle_root / "manifest.json"
    if not manifest_path.exists():
        error("manifest.json not found in bundle")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return EXIT_INTEGRITY

    try:
        manifest = Manifest.read(manifest_path)
    except Exception as exc:
        error(f"failed to parse manifest.json: {exc}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        return EXIT_INTEGRITY

    ctx = RestoreContext(
        args=args,
        bundle_root=bundle_root,
        manifest=manifest,
        target_user=getattr(args, "target_user", "bot"),
    )

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    failed_phases: List[str] = []
    for phase in phases:
        if is_done(phase):
            info(f"restore: [{phase}] already done — skipping")
            continue

        module_name = _PHASE_MODULE_MAP.get(phase)
        if not module_name:
            warn(f"restore: no implementation for phase {phase!r} — skipping")
            continue

        info(f"restore: [{phase}] starting")
        try:
            mod = importlib.import_module(f"..phases.{module_name}", package=__name__)
            mod.run(ctx)
            mark_done(phase)
            info(f"restore: [{phase}] done")
        except RestoreError as exc:
            error(f"restore: [{phase}] FAILED: {exc}")
            failed_phases.append(phase)
            if exc.exit_code not in (3,):
                # Non-resumable failure; stop
                break
        except Exception as exc:
            error(f"restore: [{phase}] unexpected error: {exc}")
            failed_phases.append(phase)
            break

    if failed_phases:
        error(
            f"restore: {len(failed_phases)} phase(s) failed: {', '.join(failed_phases)}"
        )
        return EXIT_PARTIAL

    info("restore: all phases completed successfully")
    return EXIT_OK
