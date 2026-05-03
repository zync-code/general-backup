"""restore-agent subcommand — stub. Wired up in GH-26."""
from __future__ import annotations

from ..log import info


def run(args) -> int:
    info(f"restore-agent bundle={args.bundle} auto_confirm={getattr(args, 'auto_confirm', False)}")
    info("restore-agent not yet implemented (CLI wiring only)")
    return 0
