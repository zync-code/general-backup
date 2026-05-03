"""Capture phase: inventory — populate manifest with host and toolchain info."""
from __future__ import annotations

import os
import platform
import socket
import subprocess
from pathlib import Path

from ..log import info, warn
from ..manifest import Source, Toolchain, utc_now_iso
from . import Context


def run(ctx: Context) -> None:
    hostname = socket.gethostname()
    user = os.getenv("USER", "bot")
    uid = os.getuid()

    os_name = _run_cmd(["lsb_release", "-d", "-s"]) or platform.platform()
    kernel = _run_cmd(["uname", "-r"]) or platform.release()

    ctx.manifest.source = Source(
        hostname=hostname,
        os=os_name,
        kernel=kernel,
        user=user,
        uid=uid,
    )
    ctx.manifest.captured_at = utc_now_iso()

    ctx.manifest.toolchain = Toolchain(
        node=_run_cmd(["node", "--version"]),
        pnpm=_run_cmd(["pnpm", "--version"]),
        pm2=_run_cmd(["pm2", "--version"]),
        python3=_run_cmd(["python3", "--version"]),
        postgres=_run_cmd(["psql", "--version"]),
        redis=_run_cmd(["redis-server", "--version"]),
    )

    info(
        f"inventory: {hostname} / {os_name} / uid={uid} / "
        f"node={ctx.manifest.toolchain.node} pnpm={ctx.manifest.toolchain.pnpm}"
    )


def _run_cmd(cmd: list) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (result.stdout.strip() or result.stderr.strip()).splitlines()[0]
    except Exception:
        return ""
