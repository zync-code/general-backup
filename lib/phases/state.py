"""Capture phase: state — archive operator config and dotfiles.

Creates four .tar.zst archives in staging/state/:
  orchestrator.tar.zst  — ~/.orchestrator (excl logs/ unless --include-logs)
  claude.tar.zst        — ~/.claude (filtered — see CLAUDE_EXCLUDES)
  config.tar.zst        — ~/.config/{gh,pnpm,turborepo,...}
  home-dotfiles.tar.zst — ~/.bashrc, ~/.profile, ~/.gitconfig, etc.

Also copies ~/.orchestrator/config/projects.json → staging/state/projects.json.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List

from ..log import info, warn
from . import Context, PhaseError

CLAUDE_EXCLUDES = [
    ".cache",
    "paste-cache",
    "shell-snapshots",
    "telemetry",
    "file-history",
    "history.jsonl",
]


def run(ctx: Context) -> None:
    user = os.getenv("USER", "bot")
    home = Path(f"/home/{user}")
    state_dir = ctx.ensure_dir("state")
    include_logs = getattr(ctx.args, "include_logs", False)

    _archive_orchestrator(home, state_dir, include_logs)
    _archive_claude(home, state_dir)
    _archive_config(home, state_dir)
    _archive_dotfiles(home, state_dir)
    _copy_projects_json(home, state_dir)

    ctx.manifest.components["state"] = {
        "archives": [
            "orchestrator.tar.zst",
            "claude.tar.zst",
            "config.tar.zst",
            "home-dotfiles.tar.zst",
        ]
    }
    info("state: done")


def _tar_zstd(src_dir: Path, output: Path, excludes: List[str] = None) -> None:
    cmd = [
        "tar",
        "--create",
        "--use-compress-program", "zstd -19 -T0",
        "--file", str(output),
        "--directory", str(src_dir.parent),
    ]
    for exc in (excludes or []):
        cmd += ["--exclude", exc]
    cmd.append(src_dir.name)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 1):  # exit 1 = "some files changed while archiving" (ok)
        raise PhaseError(f"tar failed for {output.name}: {result.stderr.strip()}")


def _archive_orchestrator(home: Path, state_dir: Path, include_logs: bool) -> None:
    src = home / ".orchestrator"
    if not src.exists():
        warn("state: ~/.orchestrator not found — skipping")
        return
    excludes = [] if include_logs else [".orchestrator/logs"]
    _tar_zstd(src, state_dir / "orchestrator.tar.zst", excludes)
    info("state: archived ~/.orchestrator")


def _archive_claude(home: Path, state_dir: Path) -> None:
    src = home / ".claude"
    if not src.exists():
        warn("state: ~/.claude not found — skipping")
        return
    excludes = [f".claude/{x}" for x in CLAUDE_EXCLUDES]
    _tar_zstd(src, state_dir / "claude.tar.zst", excludes)
    info("state: archived ~/.claude (filtered)")


def _archive_config(home: Path, state_dir: Path) -> None:
    src = home / ".config"
    if not src.exists():
        warn("state: ~/.config not found — skipping")
        return
    _tar_zstd(src, state_dir / "config.tar.zst")
    info("state: archived ~/.config")


def _archive_dotfiles(home: Path, state_dir: Path) -> None:
    dotfiles = [".bashrc", ".profile", ".gitconfig", ".bash_profile", ".bash_aliases"]
    present = [home / f for f in dotfiles if (home / f).exists()]
    if not present:
        return

    # Create a temp dir with just the dotfiles and tar it
    import tempfile
    with tempfile.TemporaryDirectory(prefix="gb-dotfiles-") as tmp:
        tmp_path = Path(tmp)
        for df in present:
            shutil.copy2(df, tmp_path / df.name)
        _tar_zstd(tmp_path, state_dir / "home-dotfiles.tar.zst")

    info(f"state: archived {len(present)} dotfile(s)")


def _copy_projects_json(home: Path, state_dir: Path) -> None:
    projects_json = home / ".orchestrator" / "config" / "projects.json"
    if not projects_json.exists():
        return
    shutil.copy2(projects_json, state_dir / "projects.json")
    info("state: copied projects.json")
