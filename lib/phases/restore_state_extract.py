"""Restore phase: state-extract — extract orchestrator/claude/config/dotfiles."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError, extract_tar


_TARBALLS = [
    ("orchestrator.tar.zst", "~/.orchestrator"),
    ("claude.tar.zst", "~/.claude"),
    ("config.tar.zst", "~/.config"),
    ("home-dotfiles.tar.zst", "~"),
]


def run(ctx: RestoreContext) -> None:
    home = ctx.target_home()
    state_dir = ctx.state_path

    for filename, dest_template in _TARBALLS:
        tar_path = state_dir / filename
        if not tar_path.exists():
            warn(f"restore/state-extract: {filename} not found in bundle — skipping")
            continue

        dest = Path(dest_template.replace("~", str(home)))
        info(f"restore/state-extract: extracting {filename} → {dest}")
        try:
            extract_tar(tar_path, dest)
        except Exception as exc:
            raise RestoreError(f"failed to extract {filename}: {exc}")

    # Chown everything under home to the target user
    try:
        subprocess.run(
            ["chown", "-R", f"{ctx.target_user}:{ctx.target_user}", str(home)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        warn(f"restore/state-extract: chown failed: {exc}")

    info(f"restore/state-extract: state extracted to {home}")
