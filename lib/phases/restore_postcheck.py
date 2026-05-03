"""Restore phase: postcheck — assertions and restore-report.md."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Tuple

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError


def run(ctx: RestoreContext) -> None:
    results: List[Tuple[str, str, str]] = []  # (section, label, status)

    # PM2 process count
    _check_pm2(ctx, results)

    # nginx -t
    _check_nginx(results)

    # PostgreSQL databases
    _check_postgres(ctx, results)

    # Project .git presence + SHA
    _check_projects(ctx, results)

    # Write restore-report.md
    report_path = Path("restore-report.md")
    _write_report(ctx, results, report_path)

    failed = [r for r in results if r[2] != "ok"]
    if failed:
        warn(f"restore/postcheck: {len(failed)} assertion(s) failed — see {report_path}")
        raise RestoreError(
            f"{len(failed)} postcheck assertion(s) failed", exit_code=3
        )

    info(f"restore/postcheck: all {len(results)} assertion(s) passed — see {report_path}")


def _check_pm2(ctx: RestoreContext, results: list) -> None:
    expected = ctx.manifest.components.get("pm2", {}).get("process_count")
    if expected is None:
        return
    try:
        r = subprocess.run(
            ["sudo", "-u", ctx.target_user, "--", "pm2", "jlist"],
            capture_output=True, text=True, timeout=10,
        )
        actual = len(json.loads(r.stdout or "[]"))
    except Exception as exc:
        results.append(("PM2", f"process count (expected {expected})", f"error: {exc}"))
        return

    if actual == expected:
        results.append(("PM2", f"process count = {actual}", "ok"))
    else:
        results.append(("PM2", f"process count", f"FAIL: expected {expected}, got {actual}"))


def _check_nginx(results: list) -> None:
    try:
        r = subprocess.run(
            ["nginx", "-t"], capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            results.append(("nginx", "nginx -t", "ok"))
        else:
            results.append(("nginx", "nginx -t", f"FAIL: {r.stderr.strip()[:100]}"))
    except Exception as exc:
        results.append(("nginx", "nginx -t", f"error: {exc}"))


def _check_postgres(ctx: RestoreContext, results: list) -> None:
    expected_dbs = ctx.manifest.components.get("postgres", {}).get("databases", [])
    if not expected_dbs:
        return
    try:
        r = subprocess.run(
            ["psql", "-U", "postgres", "-lqt"],
            capture_output=True, text=True, timeout=10,
        )
        live_dbs = {
            line.split("|")[0].strip()
            for line in r.stdout.splitlines()
            if "|" in line and line.split("|")[0].strip()
        }
    except Exception as exc:
        results.append(("PostgreSQL", "databases listable", f"error: {exc}"))
        return

    for db in expected_dbs:
        if db in live_dbs:
            results.append(("PostgreSQL", f"database {db!r}", "ok"))
        else:
            results.append(("PostgreSQL", f"database {db!r}", "FAIL: not found"))


def _check_projects(ctx: RestoreContext, results: list) -> None:
    for proj in ctx.manifest.projects:
        proj_dir = Path(proj.project_dir)
        if not proj_dir.exists():
            results.append(("Projects", f"{proj.name} .git", "FAIL: directory missing"))
            continue
        if not (proj_dir / ".git").exists():
            results.append(("Projects", f"{proj.name} .git", "FAIL: .git missing"))
            continue
        try:
            r = subprocess.run(
                ["git", "-C", str(proj_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            )
            actual = r.stdout.strip()
        except Exception:
            actual = "error"
        if actual == proj.sha:
            results.append(("Projects", f"{proj.name} @ {proj.sha[:8]}", "ok"))
        else:
            results.append(
                ("Projects", f"{proj.name} SHA",
                 f"FAIL: expected {proj.sha[:8]}, got {actual[:8] if actual else '?'}")
            )


def _write_report(
    ctx: RestoreContext,
    results: List[Tuple[str, str, str]],
    path: Path,
) -> None:
    degraded = ctx.extras.get("degraded_projects", [])
    ok_count = sum(1 for r in results if r[2] == "ok")
    fail_count = len(results) - ok_count

    lines = [
        "# Restore Report",
        "",
        f"Bundle: `{ctx.args.bundle}`  ",
        f"Captured at: {ctx.manifest.captured_at}  ",
        f"Source host: {ctx.manifest.source.hostname if ctx.manifest.source else 'unknown'}",
        "",
        f"**{ok_count} passed** · **{fail_count} failed**",
        "",
    ]

    # Group by section
    sections: dict = {}
    for sec, label, status in results:
        sections.setdefault(sec, []).append((label, status))

    for sec, items in sections.items():
        lines.append(f"## {sec}")
        lines.append("")
        lines.append("| Check | Result |")
        lines.append("| --- | --- |")
        for label, status in items:
            icon = "ok" if status == "ok" else f"**{status}**"
            lines.append(f"| {label} | {icon} |")
        lines.append("")

    if degraded:
        lines += [
            "## Degraded Projects",
            "",
            "These projects require manual intervention:",
            "",
        ]
        for name in degraded:
            lines.append(f"- `{name}` — run `pnpm install` (and `pnpm build` if applicable)")
        lines.append("")

    if fail_count == 0 and not degraded:
        lines += ["## Summary", "", "Restore completed successfully.", ""]
    else:
        lines += [
            "## Summary",
            "",
            "Restore completed with issues. Address the items above.",
            "",
        ]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    info(f"restore/postcheck: wrote {path}")
