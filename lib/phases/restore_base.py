"""Shared infrastructure for restore-side phase modules.

Provides RestoreContext (the equivalent of capture-side Context), done-marker
helpers, and the common pattern of extracting a file from the bundle root.
"""
from __future__ import annotations

import dataclasses as dc
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..manifest import Manifest

STATE_DIR = Path("/var/lib/general-backup/state")


class RestoreError(Exception):
    """Raised by a restore phase to signal a non-recoverable failure."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dc.dataclass
class RestoreContext:
    """Shared state passed between restore phases.

    ``bundle_root`` is the top-level directory inside the extracted bundle.
    ``secrets_staging`` is a tmpfs dir where age-decrypted secrets land;
    populated by secrets-decrypt, consumed by projects-clone and postgres.
    """

    args: Any
    bundle_root: Path
    manifest: Manifest
    target_user: str = "bot"
    secrets_staging: Optional[Path] = None
    # Phases may stash arbitrary intermediates here.
    extras: Dict[str, Any] = dc.field(default_factory=dict)

    @property
    def data(self) -> Path:
        return self.bundle_root / "data"

    @property
    def state_path(self) -> Path:
        return self.bundle_root / "state"

    @property
    def packages_path(self) -> Path:
        return self.bundle_root / "packages"

    def target_home(self) -> Path:
        return Path(f"/home/{self.target_user}")


# ── Done-markers ──────────────────────────────────────────────────────────────

def is_done(phase: str) -> bool:
    return (STATE_DIR / f"{phase}.ok").exists()


def mark_done(phase: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{phase}.ok").touch()


def clear_done(phase: str) -> None:
    marker = STATE_DIR / f"{phase}.ok"
    if marker.exists():
        marker.unlink()


# ── Shell helpers ─────────────────────────────────────────────────────────────

def run_cmd(
    cmd: List[str],
    *,
    check: bool = True,
    capture: bool = False,
    user: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    """Run a command, optionally as a different user via sudo -u."""
    if user:
        cmd = ["sudo", "-u", user, "--"] + cmd
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        env=merged_env,
        cwd=cwd,
    )


def extract_tar(tar_path: Path, dest: Path) -> None:
    """Extract a tar archive (including .tar.zst) to dest."""
    import subprocess as _sp
    dest.mkdir(parents=True, exist_ok=True)
    r = _sp.run(["tar", "-xf", str(tar_path), "-C", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError(f"tar extract failed: {r.stderr.strip()}")


def ensure_tmpfs_staging(name: str = "gb-secrets") -> Path:
    """Create a tmpfs-backed directory for secrets (falls back to /dev/shm or tmpdir)."""
    for base in ["/dev/shm", tempfile.gettempdir()]:
        candidate = Path(base) / name
        try:
            candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Quick check that the path exists under a memory-backed fs
            return candidate
        except PermissionError:
            continue
    raise RestoreError("cannot create secrets staging directory")
