"""Capture phase: pm2 — save PM2 process list and dump ecosystem state."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..log import info, warn
from . import Context, PhaseError


def run(ctx: Context) -> None:
    user = os.getenv("USER", "bot")
    home = Path(f"/home/{user}")
    pm2_home = home / ".pm2"

    pm2_dir = ctx.ensure_dir("data", "pm2")

    # pm2 save (updates dump.pm2)
    info("pm2: running pm2 save")
    result = subprocess.run(
        ["sudo", "-u", user, "--", "pm2", "save", "--force"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(f"pm2: pm2 save warning: {result.stderr[:200]}")

    # Copy dump.pm2
    dump_src = pm2_home / "dump.pm2"
    if dump_src.exists():
        shutil.copy2(dump_src, pm2_dir / "dump.pm2")
        info("pm2: copied dump.pm2")
    else:
        warn("pm2: dump.pm2 not found — pm2 may not have saved")

    # pm2 jlist → jlist.json
    jlist_result = subprocess.run(
        ["sudo", "-u", user, "--", "pm2", "jlist"],
        capture_output=True, text=True,
    )
    process_count = 0
    if jlist_result.returncode == 0:
        try:
            processes = json.loads(jlist_result.stdout or "[]")
            process_count = len(processes)
            (pm2_dir / "jlist.json").write_text(
                json.dumps(processes, indent=2), encoding="utf-8"
            )
            info(f"pm2: captured {process_count} process(es)")
        except json.JSONDecodeError:
            warn("pm2: could not parse pm2 jlist output")
    else:
        warn(f"pm2: pm2 jlist failed: {jlist_result.stderr[:200]}")

    ctx.manifest.components["pm2"] = {"process_count": process_count}
