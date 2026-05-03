"""Restore phase: bootstrap — install toolchain via bootstrap.sh."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..log import info
from .restore_base import RestoreContext, RestoreError


def run(ctx: RestoreContext) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    bootstrap = repo_root / "bootstrap.sh"
    if not bootstrap.exists():
        raise RestoreError(f"bootstrap.sh not found at {bootstrap}")

    info("restore/bootstrap: running bootstrap.sh")
    result = subprocess.run(["bash", str(bootstrap)], check=False)
    if result.returncode != 0:
        raise RestoreError(
            f"bootstrap.sh exited {result.returncode}", exit_code=result.returncode
        )
    info("restore/bootstrap: toolchain installed")
