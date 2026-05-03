"""diff subcommand — stub."""
from __future__ import annotations

from ..log import info


def run(args) -> int:
    info(f"diff bundle={args.bundle}")
    info("diff not yet implemented (foundations PR only)")
    return 0
