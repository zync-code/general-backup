"""Capture phase: git-sync — push all registered projects to their remotes.

For each project in ~/.orchestrator/config/projects.json with a github_repo:
  1. git -C <dir> fetch origin
  2. If clean: ensure all commits pushed (git push origin <branch>)
  3. If dirty + --allow-snapshot-commit (default): git add -A && git commit -m "snapshot: ..."
                                                   then push
  4. If dirty + --no-snapshot-commit: add to error list; abort if non-empty

On success, records {name, git_url, branch, sha, project_dir, deploy_type,
env_paths, pm2_apps, db_names} into manifest.projects[].

Exit code 5 is returned via PhaseError if dirty repos exist and snapshot
commits are disabled.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from ..log import info, warn
from ..manifest import Project
from . import Context, PhaseError, project_entries


def run(ctx: Context) -> None:
    projects = project_entries(ctx.projects_json or {})
    if not projects:
        info("git-sync: no projects to sync")
        return

    allow_snapshot = getattr(ctx.args, "allow_snapshot_commit", True)
    dirty_errors: List[str] = []
    synced: List[Project] = []

    for proj in projects:
        name = proj["name"]
        proj_dir = Path(proj.get("project_dir", ""))
        github_repo = proj.get("github_repo", "")

        if not proj_dir.exists():
            warn(f"git-sync: [{name}] project_dir not found: {proj_dir} — skipping")
            continue
        if not (proj_dir / ".git").exists():
            warn(f"git-sync: [{name}] not a git repo — skipping")
            continue

        info(f"git-sync: [{name}] syncing")

        # Fetch
        _git(proj_dir, ["fetch", "--quiet", "origin"])

        # Check if dirty
        dirty_files = _dirty_files(proj_dir)
        if dirty_files:
            if allow_snapshot:
                _snapshot_commit(proj_dir, name)
            else:
                dirty_errors.append(
                    f"{name}: {len(dirty_files)} uncommitted file(s) — use "
                    "--allow-snapshot-commit to auto-commit"
                )
                continue

        # Push
        branch = _current_branch(proj_dir)
        result = subprocess.run(
            ["git", "-C", str(proj_dir), "push", "origin", branch],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            warn(f"git-sync: [{name}] push failed: {result.stderr[:200]}")

        sha = _current_sha(proj_dir)
        info(f"git-sync: [{name}] @ {sha[:8]} on {branch}")

        synced.append(Project(
            name=name,
            git_url=github_repo,
            branch=branch,
            sha=sha,
            project_dir=str(proj_dir),
            deploy_type=proj.get("deploy_type", ""),
            env_paths=list(proj.get("env_paths", [])),
            pm2_apps=list(proj.get("pm2_apps", [])),
            db_names=list(proj.get("db_names", [])),
        ))

    ctx.manifest.projects = synced
    info(f"git-sync: synced {len(synced)}/{len(projects)} project(s)")

    if dirty_errors:
        raise PhaseError(
            f"{len(dirty_errors)} project(s) have uncommitted changes:\n"
            + "\n".join(f"  • {e}" for e in dirty_errors),
            exit_code=5,
        )


def _git(proj_dir: Path, args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(proj_dir)] + args,
        capture_output=True, text=True,
    )


def _dirty_files(proj_dir: Path) -> List[str]:
    result = _git(proj_dir, ["status", "--porcelain"])
    return [l for l in result.stdout.splitlines() if l.strip()]


def _snapshot_commit(proj_dir: Path, name: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _git(proj_dir, ["add", "-A"])
    result = _git(proj_dir, [
        "commit",
        "-m", f"snapshot: pre-backup capture {stamp}",
        "--no-verify",
    ])
    if result.returncode != 0:
        warn(f"git-sync: [{name}] snapshot commit failed: {result.stderr[:200]}")
    else:
        info(f"git-sync: [{name}] snapshot commit created")


def _current_branch(proj_dir: Path) -> str:
    result = _git(proj_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip() or "main"


def _current_sha(proj_dir: Path) -> str:
    result = _git(proj_dir, ["rev-parse", "HEAD"])
    return result.stdout.strip()
