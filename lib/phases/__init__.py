"""Capture- and restore-side phases.

Each phase is a small module exposing a single ``run(ctx)`` function that
takes a :class:`Context` and either succeeds or raises a
:class:`PhaseError`. Phases are independently re-runnable; the orchestrator
in ``commands/capture.py`` and ``commands/restore.py`` decides which to
invoke and in what order.
"""
from __future__ import annotations

import dataclasses as dc
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..manifest import Manifest


class PhaseError(Exception):
    """Raised by a phase to signal a non-recoverable failure.

    ``exit_code`` lets a phase request a specific top-level exit code
    (e.g., 5 for git-sync conflicts).
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dc.dataclass
class Context:
    """Shared state passed between capture phases.

    ``staging`` is the working directory where every phase drops its
    artifacts. ``secrets_staging`` is a separate dir holding plaintext
    files that must be encrypted before reaching the bundle.
    """

    args: Any
    staging: Path
    secrets_staging: Path
    manifest: Manifest
    projects_json: Optional[Dict[str, Any]] = None
    # Phases may stash arbitrary intermediates here.
    extras: Dict[str, Any] = dc.field(default_factory=dict)

    def ensure_dir(self, *parts: str) -> Path:
        p = self.staging.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def secrets_dir(self, *parts: str) -> Path:
        p = self.secrets_staging.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_projects_json(path: os.PathLike) -> Dict[str, Any]:
    """Read ~/.orchestrator/config/projects.json. Missing → empty registry.

    Returned dict matches the on-disk shape: ``{"projects": {<name>: {...}}}``
    so callers can branch cleanly on absence.
    """
    p = Path(path)
    if not p.exists():
        return {"projects": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def project_entries(projects_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten the registry into a sorted list of project entries with a
    `name` key, dropping any that lack a `github_repo` (per PRD §5)."""
    out: List[Dict[str, Any]] = []
    raw = projects_json.get("projects") or {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("github_repo"):
            continue
        merged = {"name": name, **entry}
        out.append(merged)
    out.sort(key=lambda e: e["name"])
    return out


def disk_free_bytes(path: os.PathLike) -> int:
    """Bytes free on the filesystem holding ``path``."""
    return shutil.disk_usage(Path(path)).free
