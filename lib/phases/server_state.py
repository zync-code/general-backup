"""Capture phase: server_state — push server state to zync-code/server-state GitHub repo.

Layout inside the repo:
  captures/<YYYY-MM-DDTHH-MM-SS>/
    manifest.json
    inventory.json          (plain)
    packages/               (plain)
    system/                 (plain)
    nginx/                  (plain)
    cron/                   (plain)
    pm2/                    (plain)
    secrets.age             (age-encrypted: .env, SSH, tokens, sudoers, shadow)
    postgres.age            (age-encrypted: globals.sql + per-DB .dump files)
    redis.age               (age-encrypted: dump.rdb + config.json)
  latest -> captures/<latest>   (symlink updated each capture)
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..log import info, warn
from ..manifest import utc_now_iso
from . import Context, PhaseError

_SERVER_STATE_REPO = "https://github.com/zync-code/server-state.git"


def run(ctx: Context) -> None:
    age_recipient = getattr(ctx.args, "age_recipient", None)
    if not age_recipient:
        raise PhaseError("--age-recipient is required for server_state push")

    stamp = utc_now_iso().replace(":", "-")[:19]  # 2026-05-08T12-31-51
    capture_dir_name = f"captures/{stamp}"

    clone_dir = Path(tempfile.mkdtemp(prefix="gb-server-state-"))
    try:
        _clone_or_init(clone_dir)
        capture_path = clone_dir / capture_dir_name
        capture_path.mkdir(parents=True, exist_ok=True)

        _copy_plain_state(ctx, capture_path)
        _encrypt_postgres(ctx, capture_path, age_recipient)
        _encrypt_redis(ctx, capture_path, age_recipient)
        _copy_secrets_age(ctx, capture_path)
        _copy_manifest(ctx, capture_path)
        _update_latest_symlink(clone_dir, capture_dir_name)

        sha = _commit_and_push(clone_dir, stamp)
        info(f"server_state: pushed capture {stamp} → {_SERVER_STATE_REPO} @ {sha[:8]}")

        ctx.manifest.components["server_state"] = {
            "repo": _SERVER_STATE_REPO,
            "capture": capture_dir_name,
            "sha": sha,
        }
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def _clone_or_init(clone_dir: Path) -> None:
    info("server_state: cloning server-state repo")
    result = subprocess.run(
        ["git", "clone", "--depth=1", _SERVER_STATE_REPO, str(clone_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Repo is empty (first capture) — init locally
        warn("server_state: clone failed (empty repo?), initialising locally")
        subprocess.run(["git", "-C", str(clone_dir), "init"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone_dir), "remote", "add", "origin", _SERVER_STATE_REPO],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "-b", "main"],
            capture_output=True,
        )


def _copy_plain_state(ctx: Context, dest: Path) -> None:
    """Copy non-sensitive staging dirs directly (plain text, no secrets)."""
    plain_dirs = ["inventory", "packages", "system", "nginx", "cron", "pm2"]
    for d in plain_dirs:
        src = ctx.staging / "data" / d
        if src.exists():
            shutil.copytree(src, dest / d, dirs_exist_ok=True)
            info(f"server_state: copied {d}/")
        else:
            # Try top-level dir name variants
            src2 = ctx.staging / d
            if src2.exists():
                shutil.copytree(src2, dest / d, dirs_exist_ok=True)
                info(f"server_state: copied {d}/")


def _encrypt_postgres(ctx: Context, dest: Path, recipient: str) -> None:
    """Tar all postgres dump files and age-encrypt to postgres.age."""
    pg_dir = ctx.staging / "data" / "postgres"
    if not pg_dir.exists():
        warn("server_state: no postgres data to encrypt")
        return

    output = dest / "postgres.age"
    info("server_state: encrypting postgres dumps → postgres.age")
    _tar_and_encrypt(pg_dir, output, recipient)
    info(f"server_state: postgres.age ({output.stat().st_size:,} bytes)")


def _encrypt_redis(ctx: Context, dest: Path, recipient: str) -> None:
    """Tar redis data dir and age-encrypt to redis.age."""
    redis_dir = ctx.staging / "data" / "redis"
    if not redis_dir.exists():
        warn("server_state: no redis data to encrypt")
        return

    output = dest / "redis.age"
    info("server_state: encrypting redis dump → redis.age")
    _tar_and_encrypt(redis_dir, output, recipient)
    info(f"server_state: redis.age ({output.stat().st_size:,} bytes)")


def _copy_secrets_age(ctx: Context, dest: Path) -> None:
    """Copy the already-encrypted secrets.age from staging."""
    src = ctx.staging / "secrets.age"
    if src.exists():
        shutil.copy2(src, dest / "secrets.age")
        info(f"server_state: copied secrets.age ({src.stat().st_size:,} bytes)")
    else:
        warn("server_state: secrets.age not found in staging — skipped")


def _copy_manifest(ctx: Context, dest: Path) -> None:
    src = ctx.staging / "manifest.json"
    if src.exists():
        shutil.copy2(src, dest / "manifest.json")


def _update_latest_symlink(clone_dir: Path, capture_dir_name: str) -> None:
    latest = clone_dir / "latest"
    if latest.is_symlink():
        latest.unlink()
    latest.symlink_to(capture_dir_name)


def _commit_and_push(clone_dir: Path, stamp: str) -> str:
    env_extra = {
        "GIT_AUTHOR_NAME": "general-backup",
        "GIT_AUTHOR_EMAIL": "backup@localhost",
        "GIT_COMMITTER_NAME": "general-backup",
        "GIT_COMMITTER_EMAIL": "backup@localhost",
    }
    import os
    env = {**os.environ, **env_extra}

    subprocess.run(
        ["git", "-C", str(clone_dir), "add", "-A"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone_dir), "commit", "-m", f"capture: {stamp}"],
        check=True, capture_output=True, env=env,
    )

    result = subprocess.run(
        ["git", "-C", str(clone_dir), "push", "origin", "main", "--set-upstream"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PhaseError(f"git push to server-state failed: {result.stderr.strip()[:300]}")

    sha_result = subprocess.run(
        ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    return sha_result.stdout.strip()


def _tar_and_encrypt(src_dir: Path, output: Path, recipient: str) -> None:
    """Tar src_dir contents and pipe through age → output."""
    tar_proc = subprocess.Popen(
        ["tar", "-C", str(src_dir), "-c", "."],
        stdout=subprocess.PIPE,
    )
    with open(output, "wb") as out_f:
        age_proc = subprocess.Popen(
            ["age", "-r", recipient],
            stdin=tar_proc.stdout,
            stdout=out_f,
            stderr=subprocess.PIPE,
        )
        tar_proc.stdout.close()
        _, age_err = age_proc.communicate()
        tar_proc.wait()

    if tar_proc.returncode != 0:
        raise PhaseError(f"tar failed during encryption (exit {tar_proc.returncode})")
    if age_proc.returncode != 0:
        raise PhaseError(f"age encryption failed: {age_err.decode()[:200]}")
