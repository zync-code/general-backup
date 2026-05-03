"""Capture phase: package — tar+zstd the staging dir into the final bundle.

Output: general-backup-<hostname>-<UTCstamp>.tar.zst
Prints: bundle path, compressed size, sha256.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from ..log import info, warn
from ..manifest import sha256_file, utc_now_iso
from . import Context, PhaseError


def run(ctx: Context) -> None:
    hostname = socket.gethostname().split(".")[0]
    stamp = utc_now_iso().replace(":", "").replace("-", "")[:15]  # 20260503T183000Z → 20260503T183000
    bundle_name = f"general-backup-{hostname}-{stamp}.tar.zst"

    out_arg = getattr(ctx.args, "out", None)
    if out_arg:
        out_dir = Path(out_arg)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path.cwd()

    bundle_path = out_dir / bundle_name

    info(f"package: creating {bundle_name}")

    result = subprocess.run(
        [
            "tar",
            "--create",
            "--use-compress-program", "zstd -19 -T0",
            "--file", str(bundle_path),
            "--directory", str(ctx.staging.parent),
            ctx.staging.name,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PhaseError(
            f"tar+zstd failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    size_bytes = bundle_path.stat().st_size
    digest = sha256_file(bundle_path)
    size_human = _human_size(size_bytes)

    # Print to stdout (machine-parsable) regardless of quiet mode
    print(f"{bundle_path}")
    print(f"size:   {size_human} ({size_bytes} bytes)")
    print(f"sha256: {digest}")

    info(f"package: bundle ready — {bundle_name} ({size_human})")


def _human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} TB"
