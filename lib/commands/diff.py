"""diff subcommand — compare a bundle's manifest against the live host.

Output is Markdown so it can be pasted directly into a runbook or issue.
Sections:
  ## Projects
  ## PostgreSQL
  ## Redis
  ## PM2
  ## nginx
  ## Packages
  ## Environment files
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import List

from ..log import error, info, warn
from ..manifest import Manifest


def run(args) -> int:
    bundle = Path(args.bundle)
    if not bundle.is_file():
        error(f"bundle not found: {bundle}")
        return 1

    with tempfile.TemporaryDirectory(prefix="gb-diff-") as tmpdir:
        root = Path(tmpdir)

        info(f"diff: opening {bundle.name}")
        try:
            with tarfile.open(bundle, "r:*") as tf:
                tf.extractall(root, filter="data")
        except Exception as exc:
            error(f"failed to open bundle: {exc}")
            return 1

        tops = [p for p in root.iterdir() if p.is_dir()]
        if not tops:
            error("empty bundle")
            return 2
        bundle_root = tops[0]

        manifest_path = bundle_root / "manifest.json"
        if not manifest_path.exists():
            error("manifest.json not found in bundle")
            return 2

        try:
            manifest = Manifest.read(manifest_path)
        except Exception as exc:
            error(f"failed to parse manifest.json: {exc}")
            return 2

        lines: List[str] = [
            f"# general-backup diff",
            f"",
            f"Bundle: `{bundle.name}`  ",
            f"Captured: {manifest.captured_at}  ",
            f"Source host: {manifest.source.hostname if manifest.source else 'unknown'}",
            f"",
        ]

        lines += _diff_projects(manifest)
        lines += _diff_postgres(manifest, bundle_root)
        lines += _diff_pm2(manifest)
        lines += _diff_nginx(manifest)
        lines += _diff_packages(manifest, bundle_root)
        lines += _diff_env_files(manifest)

        print("\n".join(lines))
        return 0


# ── Projects ──────────────────────────────────────────────────────────────────

def _diff_projects(manifest: Manifest) -> List[str]:
    lines = ["## Projects", ""]
    if not manifest.projects:
        lines += ["_No projects in manifest._", ""]
        return lines

    rows = []
    for proj in manifest.projects:
        proj_dir = Path(proj.project_dir)
        if not proj_dir.exists():
            rows.append(f"| `{proj.name}` | missing | {proj.sha[:8]} | — |")
            continue
        if not (proj_dir / ".git").exists():
            rows.append(f"| `{proj.name}` | no .git | {proj.sha[:8]} | — |")
            continue
        try:
            actual_sha = subprocess.check_output(
                ["git", "-C", str(proj_dir), "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            actual_sha = "error"
        status = "ok" if actual_sha == proj.sha else "mismatch"
        rows.append(
            f"| `{proj.name}` | {status} | {proj.sha[:8]} | {actual_sha[:8]} |"
        )

    lines += [
        "| Project | Status | Bundle SHA | Live SHA |",
        "| --- | --- | --- | --- |",
    ]
    lines += rows
    lines.append("")
    return lines


# ── PostgreSQL ────────────────────────────────────────────────────────────────

def _diff_postgres(manifest: Manifest, bundle_root: Path) -> List[str]:
    lines = ["## PostgreSQL", ""]

    bundle_dbs = set(
        manifest.components.get("postgres", {}).get("databases", [])
    )

    # Also gather db names from dump files in the bundle
    pg_dir = bundle_root / "data" / "postgres"
    if pg_dir.exists():
        for f in pg_dir.iterdir():
            if f.suffix == ".dump":
                bundle_dbs.add(f.stem)

    try:
        result = subprocess.run(
            ["psql", "-U", "postgres", "-lqt"],
            capture_output=True, text=True, timeout=10,
        )
        live_dbs = set()
        for line in result.stdout.splitlines():
            parts = line.split("|")
            if parts:
                name = parts[0].strip()
                if name and name not in ("template0", "template1", "postgres", ""):
                    live_dbs.add(name)
    except Exception:
        lines += ["_Could not query live PostgreSQL (psql not available or not running)._", ""]
        if bundle_dbs:
            lines += ["Bundle databases: " + ", ".join(f"`{d}`" for d in sorted(bundle_dbs)), ""]
        return lines

    missing = sorted(bundle_dbs - live_dbs)
    extra = sorted(live_dbs - bundle_dbs)

    if not missing and not extra:
        lines += [f"All {len(bundle_dbs)} bundle database(s) present on host. No extras.", ""]
    else:
        if missing:
            lines += ["**Missing from host** (in bundle, not on host):"]
            lines += [f"- `{d}`" for d in missing]
            lines.append("")
        if extra:
            lines += ["**Extra on host** (on host, not in bundle):"]
            lines += [f"- `{d}`" for d in extra]
            lines.append("")
    return lines


# ── PM2 ──────────────────────────────────────────────────────────────────────

def _diff_pm2(manifest: Manifest) -> List[str]:
    lines = ["## PM2", ""]
    bundle_count = manifest.components.get("pm2", {}).get("process_count", None)

    try:
        result = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=10,
        )
        live_list = json.loads(result.stdout or "[]")
        live_count = len(live_list)
    except Exception:
        lines += [
            "_Could not query live PM2 (pm2 not available or not running)._",
            "",
        ]
        if bundle_count is not None:
            lines += [f"Bundle process count: **{bundle_count}**", ""]
        return lines

    if bundle_count is None:
        lines += [f"Live PM2 process count: **{live_count}** (no bundle count to compare)", ""]
    elif live_count == bundle_count:
        lines += [f"Process count matches: **{live_count}**", ""]
    else:
        delta = live_count - bundle_count
        sign = "+" if delta > 0 else ""
        lines += [
            f"**Count mismatch**: bundle={bundle_count}, live={live_count} "
            f"({sign}{delta})",
            "",
        ]
    return lines


# ── nginx ──────────────────────────────────────────────────────────────────────

def _diff_nginx(manifest: Manifest) -> List[str]:
    lines = ["## nginx", ""]

    bundle_vhosts = manifest.components.get("nginx", {}).get("vhost_count", None)
    sites_enabled = Path("/etc/nginx/sites-enabled")
    if sites_enabled.exists():
        live_vhosts = len(list(sites_enabled.iterdir()))
    else:
        live_vhosts = None

    if bundle_vhosts is None and live_vhosts is None:
        lines += ["_No nginx data available._", ""]
        return lines

    if bundle_vhosts == live_vhosts:
        lines += [f"Vhost count matches: **{live_vhosts}**", ""]
    else:
        lines += [
            f"**Vhost count**: bundle={bundle_vhosts}, "
            f"live={live_vhosts if live_vhosts is not None else 'unavailable'}",
            "",
        ]

    try:
        result = subprocess.run(
            ["nginx", "-t"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines += ["nginx -t: **ok**", ""]
        else:
            lines += [f"nginx -t: **FAILED**\n```\n{result.stderr.strip()}\n```", ""]
    except Exception:
        lines += ["_nginx not available on this host._", ""]

    return lines


# ── Packages ──────────────────────────────────────────────────────────────────

def _diff_packages(manifest: Manifest, bundle_root: Path) -> List[str]:
    lines = ["## Packages", ""]

    apt_file = bundle_root / "packages" / "apt-manual.txt"
    if not apt_file.exists():
        lines += ["_No package data in bundle._", ""]
        return lines

    bundle_pkgs = set(apt_file.read_text().splitlines())
    bundle_pkgs = {p.strip() for p in bundle_pkgs if p.strip()}

    try:
        result = subprocess.run(
            ["apt-mark", "showmanual"],
            capture_output=True, text=True, timeout=15,
        )
        live_pkgs = {p.strip() for p in result.stdout.splitlines() if p.strip()}
    except Exception:
        lines += ["_apt-mark not available._", ""]
        return lines

    missing = sorted(bundle_pkgs - live_pkgs)
    extra = sorted(live_pkgs - bundle_pkgs)

    if not missing and not extra:
        lines += [f"Manually-installed packages match ({len(bundle_pkgs)}).", ""]
    else:
        if missing:
            lines += [f"**{len(missing)} package(s) in bundle but not manually installed on host:**"]
            lines += [f"- `{p}`" for p in missing[:20]]
            if len(missing) > 20:
                lines.append(f"- …and {len(missing) - 20} more")
            lines.append("")
        if extra:
            lines += [f"**{len(extra)} package(s) on host but not in bundle:**"]
            lines += [f"- `{p}`" for p in extra[:20]]
            if len(extra) > 20:
                lines.append(f"- …and {len(extra) - 20} more")
            lines.append("")

    return lines


# ── Environment files ─────────────────────────────────────────────────────────

def _diff_env_files(manifest: Manifest) -> List[str]:
    lines = ["## Environment files", ""]
    if not manifest.projects:
        lines += ["_No projects in manifest._", ""]
        return lines

    rows = []
    for proj in manifest.projects:
        for env_rel in proj.env_paths:
            env_path = Path(proj.project_dir) / env_rel
            present = "present" if env_path.exists() else "**MISSING**"
            rows.append(f"| `{proj.name}` | `{env_rel}` | {present} |")

    if not rows:
        lines += ["_No env_paths recorded in manifest._", ""]
        return lines

    lines += [
        "| Project | env_path | Status |",
        "| --- | --- | --- |",
    ]
    lines += rows
    lines.append("")
    return lines
