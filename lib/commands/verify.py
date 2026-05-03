"""verify subcommand — stub."""
from __future__ import annotations

from ..log import info


def run(args) -> int:
    info(f"verify bundle={args.bundle}")
    info("verify not yet implemented (foundations PR only)")
    return 0
