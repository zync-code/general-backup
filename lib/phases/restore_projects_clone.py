"""Restore phase: projects-clone — git clone each project at captured SHA."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError, run_cmd


def run(ctx: RestoreContext) -> None:
    if not ctx.manifest.projects:
        info("restore/projects-clone: no projects in manifest")
        return

    degraded: List[str] = []

    for proj in ctx.manifest.projects:
        name = proj.name
        proj_dir = Path(proj.project_dir)
        git_url = proj.git_url
        sha = proj.sha

        info(f"restore/projects-clone: [{name}] cloning {git_url} @ {sha[:8]}")

        try:
            _clone_or_fetch(proj_dir, git_url, sha)
        except Exception as exc:
            warn(f"restore/projects-clone: [{name}] git failed: {exc} — marking degraded")
            degraded.append(name)
            continue

        # pnpm install (best-effort)
        _pnpm_install(proj_dir, name, degraded)

    if degraded:
        warn(
            f"restore/projects-clone: {len(degraded)} degraded project(s): "
            + ", ".join(degraded)
        )
        ctx.extras["degraded_projects"] = degraded

    info(
        f"restore/projects-clone: {len(ctx.manifest.projects) - len(degraded)} "
        f"project(s) ok, {len(degraded)} degraded"
    )


def _clone_or_fetch(proj_dir: Path, git_url: str, sha: str) -> None:
    if (proj_dir / ".git").exists():
        # Repo already exists — fetch and reset
        subprocess.run(
            ["git", "-C", str(proj_dir), "fetch", "--quiet", "origin"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(proj_dir), "reset", "--hard", sha],
            check=True, capture_output=True,
        )
    else:
        proj_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", git_url, str(proj_dir)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(proj_dir), "checkout", sha],
            check=True, capture_output=True,
        )


def _pnpm_install(proj_dir: Path, name: str, degraded: List[str]) -> None:
    package_json = proj_dir / "package.json"
    if not package_json.exists():
        return  # not a Node project

    info(f"restore/projects-clone: [{name}] pnpm install --frozen-lockfile")
    result = subprocess.run(
        ["pnpm", "install", "--frozen-lockfile"],
        cwd=proj_dir,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(
            f"restore/projects-clone: [{name}] pnpm install failed "
            f"(exit {result.returncode}) — marking degraded"
        )
        if name not in degraded:
            degraded.append(name)
