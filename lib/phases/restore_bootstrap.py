"""Restore phase: bootstrap — install toolchain via bootstrap.sh."""
from __future__ import annotations

import os
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
    # restore always runs as root, but per-user tools (claude-code CLI) need
    # to land in the target user's $HOME, not root's — see GB_TARGET_USER
    # handling in bootstrap.sh's claude-code step.
    env = {**os.environ, "GB_TARGET_USER": ctx.target_user}
    result = subprocess.run(["bash", str(bootstrap)], check=False, env=env)
    if result.returncode != 0:
        raise RestoreError(
            f"bootstrap.sh exited {result.returncode}", exit_code=result.returncode
        )
    info("restore/bootstrap: toolchain installed")
