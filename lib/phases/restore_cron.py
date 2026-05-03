"""Restore phase: cron — install bot crontab and /etc/cron.d/* entries."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError


def run(ctx: RestoreContext) -> None:
    cron_dir = ctx.data / "cron"
    if not cron_dir.exists():
        warn("restore/cron: no cron data in bundle — skipping")
        return

    # Bot crontab
    bot_crontab = cron_dir / "bot.crontab"
    if bot_crontab.exists():
        _install_crontab(bot_crontab, ctx.target_user)
    else:
        warn("restore/cron: bot.crontab not found in bundle")

    # /etc/cron.d/* entries
    etc_cron_d_src = cron_dir / "etc-cron.d"
    if etc_cron_d_src.exists():
        _install_etc_cron_d(etc_cron_d_src)

    info("restore/cron: done")


def _install_crontab(crontab_path: Path, user: str) -> None:
    result = subprocess.run(
        ["crontab", "-u", user, str(crontab_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(f"restore/cron: crontab install failed: {result.stderr.strip()}")
    else:
        info(f"restore/cron: installed crontab for {user!r}")


def _install_etc_cron_d(src_dir: Path) -> None:
    dest_dir = Path("/etc/cron.d")
    dest_dir.mkdir(parents=True, exist_ok=True)
    installed = 0
    for f in src_dir.iterdir():
        if f.is_file():
            dest = dest_dir / f.name
            shutil.copy2(f, dest)
            dest.chmod(0o644)
            installed += 1
    if installed:
        info(f"restore/cron: installed {installed} file(s) to /etc/cron.d/")
