"""Restore phase: packages — replay apt package selections."""
from __future__ import annotations

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError, run_cmd


def run(ctx: RestoreContext) -> None:
    selections = ctx.packages_path / "apt-selections.txt"
    if not selections.exists():
        warn("restore/packages: apt-selections.txt not found in bundle — skipping")
        return

    info("restore/packages: applying dpkg --set-selections")
    try:
        with open(selections, "rb") as f:
            run_cmd(["dpkg", "--set-selections"], capture=False)
        # Pipe the selections file into dpkg --set-selections
        import subprocess, os
        with open(selections, "rb") as f:
            subprocess.run(
                ["dpkg", "--set-selections"],
                stdin=f,
                check=True,
            )
    except Exception as exc:
        raise RestoreError(f"dpkg --set-selections failed: {exc}")

    info("restore/packages: running apt-get dselect-upgrade")
    try:
        run_cmd(
            ["apt-get", "update", "-q"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        run_cmd(
            ["apt-get", "dselect-upgrade", "-y", "-q"],
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
    except Exception as exc:
        raise RestoreError(f"apt-get dselect-upgrade failed: {exc}")

    info("restore/packages: packages applied")
