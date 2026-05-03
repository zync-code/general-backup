"""Capture phase: packages — record installed packages for restore replay."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..log import info, warn
from . import Context


def run(ctx: Context) -> None:
    pkg_dir = ctx.ensure_dir("packages")

    # apt-mark showmanual → apt-manual.txt
    _run_to_file(
        ["apt-mark", "showmanual"],
        pkg_dir / "apt-manual.txt",
        label="apt manual packages",
    )

    # dpkg --get-selections → apt-selections.txt
    _run_to_file(
        ["dpkg", "--get-selections"],
        pkg_dir / "apt-selections.txt",
        label="dpkg selections",
    )

    # pnpm ls -g --json → pnpm-global.json (best-effort)
    _run_json_to_file(
        ["pnpm", "ls", "-g", "--json"],
        pkg_dir / "pnpm-global.json",
        label="pnpm global packages",
    )

    # npm ls -g --json → npm-global.json (best-effort)
    _run_json_to_file(
        ["npm", "ls", "-g", "--json"],
        pkg_dir / "npm-global.json",
        label="npm global packages",
    )

    # pip3 freeze → pip3-freeze.txt (best-effort)
    _run_to_file(
        ["pip3", "freeze"],
        pkg_dir / "pip3-freeze.txt",
        label="pip3 packages",
    )

    # Count apt manual packages for manifest summary
    apt_manual_count = 0
    apt_manual = pkg_dir / "apt-manual.txt"
    if apt_manual.exists():
        apt_manual_count = len([
            l for l in apt_manual.read_text().splitlines() if l.strip()
        ])

    pnpm_count = 0
    pnpm_global = pkg_dir / "pnpm-global.json"
    if pnpm_global.exists():
        try:
            data = json.loads(pnpm_global.read_text())
            pnpm_count = len(data.get("dependencies", {}))
        except Exception:
            pass

    ctx.manifest.components["packages"] = {
        "apt_manual": apt_manual_count,
        "pnpm_global": pnpm_count,
    }
    info(f"packages: apt_manual={apt_manual_count} pnpm_global={pnpm_count}")


def _run_to_file(cmd: list, output: Path, *, label: str) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            output.write_text(result.stdout, encoding="utf-8")
            lines = len(result.stdout.strip().splitlines())
            info(f"packages: {label} ({lines} lines)")
        else:
            warn(f"packages: {label} — command failed or empty output")
    except FileNotFoundError:
        warn(f"packages: {label} — command not found")
    except Exception as exc:
        warn(f"packages: {label} — {exc}")


def _run_json_to_file(cmd: list, output: Path, *, label: str) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # npm/pnpm may exit non-zero but still produce valid JSON
        text = result.stdout.strip()
        if text:
            json.loads(text)  # validate JSON
            output.write_text(text, encoding="utf-8")
            info(f"packages: {label} captured")
        else:
            warn(f"packages: {label} — empty output")
    except FileNotFoundError:
        warn(f"packages: {label} — command not found")
    except (json.JSONDecodeError, Exception) as exc:
        warn(f"packages: {label} — {exc}")
