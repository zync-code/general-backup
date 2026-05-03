"""Restore phase: pm2 — resurrect processes and configure systemd startup."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError


def run(ctx: RestoreContext) -> None:
    pm2_dir = ctx.data / "pm2"
    if not pm2_dir.exists():
        warn("restore/pm2: no pm2 data in bundle — skipping")
        return

    # Install dump.pm2 into the target user's .pm2 directory
    dump_src = pm2_dir / "dump.pm2"
    if not dump_src.exists():
        warn("restore/pm2: dump.pm2 not found — skipping")
        return

    home = ctx.target_home()
    pm2_home = home / ".pm2"
    pm2_home.mkdir(mode=0o755, exist_ok=True)

    dump_dest = pm2_home / "dump.pm2"
    shutil.copy2(dump_src, dump_dest)
    try:
        shutil.chown(dump_dest, user=ctx.target_user, group=ctx.target_user)
    except Exception:
        pass

    # pm2 resurrect as target user
    info(f"restore/pm2: running pm2 resurrect as {ctx.target_user!r}")
    result = subprocess.run(
        ["sudo", "-u", ctx.target_user, "--", "pm2", "resurrect"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(f"restore/pm2: pm2 resurrect warning: {result.stderr[:200]}")

    # Verify count
    expected = ctx.manifest.components.get("pm2", {}).get("process_count")
    if expected is not None:
        _verify_count(ctx.target_user, expected)

    # pm2 save
    subprocess.run(
        ["sudo", "-u", ctx.target_user, "--", "pm2", "save"],
        capture_output=True,
    )

    # pm2 startup systemd
    _configure_startup(ctx.target_user)

    info("restore/pm2: done")


def _verify_count(user: str, expected: int) -> None:
    result = subprocess.run(
        ["sudo", "-u", user, "--", "pm2", "jlist"],
        capture_output=True, text=True,
    )
    try:
        count = len(json.loads(result.stdout or "[]"))
    except Exception:
        warn("restore/pm2: could not parse pm2 jlist")
        return

    if count == expected:
        info(f"restore/pm2: process count ok ({count})")
    else:
        warn(
            f"restore/pm2: process count mismatch — expected {expected}, got {count}. "
            "Missing processes may need manual pm2 start."
        )


def _configure_startup(user: str) -> None:
    result = subprocess.run(
        ["sudo", "-u", user, "--", "pm2", "startup", "systemd", "-u", user, "--hp", f"/home/{user}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(f"restore/pm2: startup config warning: {result.stderr[:200]}")
        return

    # pm2 startup prints a 'sudo ...' command to run; execute it
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("sudo env"):
            subprocess.run(line, shell=True, capture_output=True)
            break

    info("restore/pm2: systemd startup configured")
