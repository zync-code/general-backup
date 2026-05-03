"""Capture phase: cron — save crontab and /etc/cron.d/* entries."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..log import info, warn
from . import Context


def run(ctx: Context) -> None:
    user = os.getenv("USER", "bot")
    cron_dir = ctx.ensure_dir("data", "cron")

    # bot crontab
    result = subprocess.run(
        ["crontab", "-l", "-u", user],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        (cron_dir / "bot.crontab").write_text(result.stdout, encoding="utf-8")
        lines = len([l for l in result.stdout.splitlines() if l.strip() and not l.startswith("#")])
        info(f"cron: saved {lines} bot crontab entr(ies)")
    else:
        info("cron: no crontab for bot (or empty)")

    # /etc/cron.d/*
    _copy_dir(Path("/etc/cron.d"), cron_dir / "etc-cron.d")

    # /etc/cron.{daily,hourly,weekly,monthly}/
    for period in ("daily", "hourly", "weekly", "monthly"):
        _copy_dir(Path(f"/etc/cron.{period}"), cron_dir / f"etc-cron.{period}")

    info("cron: done")


def _copy_dir(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in src.iterdir():
        if f.is_file() and not f.name.startswith("."):
            try:
                shutil.copy2(f, dest / f.name)
                count += 1
            except PermissionError:
                warn(f"cron: cannot read {f} — skipping")
    if count:
        info(f"cron: copied {count} file(s) from {src}")
