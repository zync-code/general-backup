"""Restore phase: redis — restore RDB snapshot and non-default CONFIG."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError


_RDB_DEST = Path("/var/lib/redis/dump.rdb")


def run(ctx: RestoreContext) -> None:
    redis_dir = ctx.data / "redis"
    if not redis_dir.exists():
        warn("restore/redis: no redis data in bundle — skipping")
        return

    rdb_src = redis_dir / "dump.rdb"
    if not rdb_src.exists():
        warn("restore/redis: dump.rdb not found in bundle — skipping")
        return

    # Stop redis
    info("restore/redis: stopping redis-server")
    subprocess.run(["systemctl", "stop", "redis-server"], capture_output=True)

    # Copy RDB
    info(f"restore/redis: installing dump.rdb → {_RDB_DEST}")
    _RDB_DEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rdb_src, _RDB_DEST)
    try:
        shutil.chown(_RDB_DEST, user="redis", group="redis")
    except Exception as exc:
        warn(f"restore/redis: chown failed: {exc}")

    # Apply non-default CONFIG values
    config_src = redis_dir / "config.json"
    if config_src.exists():
        _apply_config(config_src)

    # Start redis
    info("restore/redis: starting redis-server")
    result = subprocess.run(
        ["systemctl", "start", "redis-server"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RestoreError(f"failed to start redis-server: {result.stderr.strip()}")

    info("restore/redis: done")


def _apply_config(config_path: Path) -> None:
    try:
        overrides = json.loads(config_path.read_text())
    except Exception as exc:
        warn(f"restore/redis: could not parse config.json: {exc}")
        return

    for key, value in overrides.items():
        result = subprocess.run(
            ["redis-cli", "CONFIG", "SET", key, str(value)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            warn(f"restore/redis: CONFIG SET {key} failed: {result.stderr.strip()}")
        else:
            info(f"restore/redis: CONFIG SET {key}={value}")
