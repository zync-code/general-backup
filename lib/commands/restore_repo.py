"""restore-repo subcommand — restore from zync-code/server-state GitHub repo.

Flow:
  1. git clone server-state (shallow)
  2. Find latest capture (or --capture TIMESTAMP)
  3. Stage into a bundle_root-compatible tmpdir:
       data/postgres/  ← age-decrypt postgres.age
       data/redis/     ← age-decrypt redis.age
       data/nginx/     ← plain copy
       data/cron/      ← plain copy
       data/pm2/       ← plain copy
       packages/       ← plain copy
       system/         ← plain copy
       secrets.age     ← plain copy (decrypted by secrets-decrypt phase)
       manifest.json   ← plain copy
  4. Run the standard restore pipeline against the staged bundle_root.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from ..cli import EXIT_OK, EXIT_INTEGRITY
from ..log import error, info, warn
from ..manifest import Manifest
from ..phases.restore_base import RestoreContext, RestoreError

_SERVER_STATE_REPO = "https://github.com/zync-code/server-state.git"

_RESTORE_PHASE_MAP = {
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


def run(args, phases: List[str]) -> int:
    age_identity = getattr(args, "age_identity", None)
    capture_ts = getattr(args, "capture", None)
    target_user = getattr(args, "target_user", "bot")

    clone_dir = Path(tempfile.mkdtemp(prefix="gb-server-state-clone-"))
    staging_dir = Path(tempfile.mkdtemp(prefix="gb-restore-repo-"))

    try:
        # ── 1. Clone server-state ────────────────────────────────────────────
        info("restore-repo: cloning server-state repo")
        result = subprocess.run(
            ["git", "clone", "--depth=1", _SERVER_STATE_REPO, str(clone_dir)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            error(f"restore-repo: git clone failed: {result.stderr.strip()}")
            return EXIT_INTEGRITY

        # ── 2. Find capture dir ──────────────────────────────────────────────
        captures_root = clone_dir / "captures"
        if not captures_root.exists():
            error("restore-repo: no captures/ directory found in server-state repo")
            return EXIT_INTEGRITY

        if capture_ts:
            capture_dir = captures_root / capture_ts
            if not capture_dir.exists():
                error(f"restore-repo: capture {capture_ts!r} not found")
                _list_captures(captures_root)
                return EXIT_INTEGRITY
        else:
            # Latest = lexicographically last (timestamps are ISO-sortable)
            available = sorted(p for p in captures_root.iterdir() if p.is_dir())
            if not available:
                error("restore-repo: no captures found in server-state repo")
                return EXIT_INTEGRITY
            capture_dir = available[-1]

        info(f"restore-repo: using capture {capture_dir.name}")

        # ── 3. Stage bundle_root ─────────────────────────────────────────────
        bundle_root = staging_dir
        _stage_bundle_root(capture_dir, bundle_root, age_identity)

        # ── 4. Load manifest ─────────────────────────────────────────────────
        manifest_path = bundle_root / "manifest.json"
        if not manifest_path.exists():
            error("restore-repo: manifest.json not found after staging")
            return EXIT_INTEGRITY

        try:
            manifest = Manifest.read(manifest_path)
        except Exception as exc:
            error(f"restore-repo: failed to parse manifest.json: {exc}")
            return EXIT_INTEGRITY

        info(
            f"restore-repo: manifest ok — captured_at={manifest.captured_at} "
            f"projects={len(manifest.projects)}"
        )

        # ── 5. Run restore pipeline ──────────────────────────────────────────
        ctx = RestoreContext(
            args=args,
            bundle_root=bundle_root,
            manifest=manifest,
            target_user=target_user,
        )

        for phase in phases:
            module_name = _RESTORE_PHASE_MAP.get(phase)
            if not module_name:
                warn(f"restore-repo: no implementation for phase {phase!r} — skipping")
                continue

            info(f"restore-repo: [{phase}] starting")
            try:
                mod = importlib.import_module(f"lib.phases.{module_name}")
                mod.run(ctx)
                info(f"restore-repo: [{phase}] done")
            except RestoreError as exc:
                error(f"restore-repo: [{phase}] FAILED: {exc}")
                return exc.exit_code
            except Exception as exc:
                error(f"restore-repo: [{phase}] unexpected error: {exc}")
                import traceback
                traceback.print_exc()
                return 1

        info("restore-repo: all phases complete")
        return EXIT_OK

    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)
        shutil.rmtree(staging_dir, ignore_errors=True)


def _stage_bundle_root(capture_dir: Path, bundle_root: Path, age_identity: str | None) -> None:
    """Reconstruct the bundle_root layout restore phases expect."""

    # Plain dirs that live under data/ in the restore context
    data_dir = bundle_root / "data"
    for name in ["nginx", "cron", "pm2"]:
        src = capture_dir / name
        if src.exists():
            shutil.copytree(src, data_dir / name, dirs_exist_ok=True)
            info(f"restore-repo: staged data/{name}/")

    # Plain dirs at bundle_root level
    for name in ["packages", "system", "inventory"]:
        src = capture_dir / name
        if src.exists():
            shutil.copytree(src, bundle_root / name, dirs_exist_ok=True)
            info(f"restore-repo: staged {name}/")

    # secrets.age — restore phases decrypt it themselves
    secrets_src = capture_dir / "secrets.age"
    if secrets_src.exists():
        shutil.copy2(secrets_src, bundle_root / "secrets.age")
        info("restore-repo: staged secrets.age")
    else:
        warn("restore-repo: secrets.age not found — secrets will not be restored")

    # manifest.json
    manifest_src = capture_dir / "manifest.json"
    if manifest_src.exists():
        shutil.copy2(manifest_src, bundle_root / "manifest.json")

    # Decrypt postgres.age → data/postgres/
    postgres_age = capture_dir / "postgres.age"
    if postgres_age.exists():
        _decrypt_age_tar(postgres_age, data_dir / "postgres", age_identity, "postgres")
    else:
        warn("restore-repo: postgres.age not found — postgres will not be restored")

    # Decrypt redis.age → data/redis/
    redis_age = capture_dir / "redis.age"
    if redis_age.exists():
        _decrypt_age_tar(redis_age, data_dir / "redis", age_identity, "redis")
    else:
        warn("restore-repo: redis.age not found — redis will not be restored")


def _decrypt_age_tar(age_file: Path, dest: Path, age_identity: str | None, label: str) -> None:
    """Decrypt an age-encrypted tar and extract to dest."""
    if not age_identity:
        warn(f"restore-repo: no --age-identity provided — cannot decrypt {label}.age")
        return

    dest.mkdir(parents=True, exist_ok=True)

    age_proc = subprocess.Popen(
        ["age", "-d", "-i", age_identity, str(age_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    tar_proc = subprocess.Popen(
        ["tar", "-x", "-C", str(dest)],
        stdin=age_proc.stdout,
        stderr=subprocess.PIPE,
    )
    age_proc.stdout.close()
    _, tar_err = tar_proc.communicate()
    _, age_err = age_proc.communicate()

    if age_proc.returncode != 0:
        error(f"restore-repo: age decrypt failed for {label}.age: {age_err.decode()[:200]}")
        return
    if tar_proc.returncode != 0:
        error(f"restore-repo: tar extract failed for {label}: {tar_err.decode()[:200]}")
        return

    info(f"restore-repo: decrypted {label}.age → {dest.relative_to(dest.parent.parent)}/")


def _list_captures(captures_root: Path) -> None:
    available = sorted(p.name for p in captures_root.iterdir() if p.is_dir())
    if available:
        info("restore-repo: available captures:")
        for c in available:
            info(f"  {c}")
