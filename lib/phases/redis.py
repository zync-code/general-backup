"""Capture phase: redis — SAVE, copy dump.rdb, record non-default CONFIG."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict

from ..log import info, warn
from . import Context, PhaseError

_RDB_DEFAULT = Path("/var/lib/redis/dump.rdb")

# Redis defaults we compare against when recording non-default CONFIG values.
# Only keys that are commonly tuned and safe to record here.
_CONFIG_DEFAULTS: Dict[str, str] = {
    "maxmemory": "0",
    "maxmemory-policy": "noeviction",
    "save": "3600 1 300 100 60 10000",
    "appendonly": "no",
    "databases": "16",
    "bind": "127.0.0.1 -::1",
    "requirepass": "",
    "loglevel": "notice",
}


def run(ctx: Context) -> None:
    redis_dir = ctx.ensure_dir("data", "redis")

    # SAVE (flush to disk)
    info("redis: SAVE")
    result = subprocess.run(
        ["redis-cli", "SAVE"], capture_output=True, text=True
    )
    if result.returncode != 0:
        warn(f"redis: SAVE failed: {result.stderr.strip()} — dump.rdb may be stale")

    # Copy dump.rdb (owned by redis user — read via sudo)
    result = subprocess.run(
        ["sudo", "-u", "redis", "cat", str(_RDB_DEFAULT)],
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        (redis_dir / "dump.rdb").write_bytes(result.stdout)
        info(f"redis: copied dump.rdb ({len(result.stdout):,} bytes)")
    else:
        warn(f"redis: could not read dump.rdb: {result.stderr.decode()[:100]}")

    # CONFIG GET * → diff vs defaults
    config_overrides = _get_config_overrides()
    if config_overrides:
        (redis_dir / "config.json").write_text(
            json.dumps(config_overrides, indent=2), encoding="utf-8"
        )
        info(f"redis: recorded {len(config_overrides)} non-default CONFIG value(s)")
    else:
        info("redis: all CONFIG values are defaults (config.json skipped)")

    # Count keys for manifest
    key_count = _count_keys()
    ctx.manifest.components["redis"] = {
        "db_count": _count_dbs(),
        "key_count": key_count,
    }
    info(f"redis: done ({key_count} key(s))")


def _get_config_overrides() -> Dict[str, str]:
    result = subprocess.run(
        ["redis-cli", "CONFIG", "GET", "*"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(f"redis: CONFIG GET failed: {result.stderr.strip()}")
        return {}

    # Output alternates key / value lines
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    live: Dict[str, str] = {}
    for i in range(0, len(lines) - 1, 2):
        live[lines[i]] = lines[i + 1]

    # Keep only keys that differ from defaults
    overrides: Dict[str, str] = {}
    for key, default in _CONFIG_DEFAULTS.items():
        if key in live and live[key] != default:
            overrides[key] = live[key]

    return overrides


def _count_keys() -> int:
    try:
        result = subprocess.run(
            ["redis-cli", "INFO", "keyspace"],
            capture_output=True, text=True, timeout=5,
        )
        total = 0
        for line in result.stdout.splitlines():
            if line.startswith("db"):
                # e.g.: db0:keys=16540,expires=123,avg_ttl=0
                parts = line.split("keys=")
                if len(parts) == 2:
                    total += int(parts[1].split(",")[0])
        return total
    except Exception:
        return 0


def _count_dbs() -> int:
    try:
        result = subprocess.run(
            ["redis-cli", "INFO", "keyspace"],
            capture_output=True, text=True, timeout=5,
        )
        return sum(1 for line in result.stdout.splitlines() if line.startswith("db"))
    except Exception:
        return 0
