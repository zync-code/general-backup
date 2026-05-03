"""Capture phase: preflight — validate required tools, disk space, projects.json.

Fails fast if:
  - Any required tool is missing
  - Staging directory has insufficient free space (< 2 GB)
  - projects.json cannot be loaded (missing is ok; no projects captured)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..log import info, warn
from . import Context, PhaseError, disk_free_bytes, load_projects_json

_REQUIRED_TOOLS = ["git", "pg_dump", "redis-cli", "tar", "zstd", "age"]
_STAGING_MIN_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
_PROJECTS_JSON_PATH = Path.home() / ".orchestrator" / "config" / "projects.json"


def run(ctx: Context) -> None:
    errors: list[str] = []

    # Check required tools
    missing = [t for t in _REQUIRED_TOOLS if not shutil.which(t)]
    if missing:
        errors.append(f"Missing tools: {', '.join(missing)}")
    else:
        info(f"preflight: all required tools present ({', '.join(_REQUIRED_TOOLS)})")

    # Check staging disk space
    free = disk_free_bytes(ctx.staging.parent)
    if free < _STAGING_MIN_BYTES:
        errors.append(
            f"Insufficient disk space: {free // (1024 ** 3)} GB free "
            f"(need {_STAGING_MIN_BYTES // (1024 ** 3)} GB)"
        )
    else:
        info(f"preflight: disk space ok ({free // (1024 ** 3)} GB free)")

    # Load projects.json
    projects_json_path = _PROJECTS_JSON_PATH
    projects_json = load_projects_json(projects_json_path)
    project_count = len(projects_json.get("projects", {}))
    ctx.projects_json = projects_json

    if project_count == 0:
        warn(
            f"preflight: no projects found in {projects_json_path} "
            "(git-sync phase will be a no-op)"
        )
    else:
        info(f"preflight: found {project_count} project(s) in projects.json")

    if errors:
        raise PhaseError("preflight failed:\n" + "\n".join(f"  • {e}" for e in errors))

    info("preflight: all checks passed")
