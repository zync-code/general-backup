"""restore subcommand — stub. Phase modules wire in via subsequent PRs."""
from __future__ import annotations

from typing import List

from ..log import info


def run(args, phases: List[str]) -> int:
    info(f"restore plan: bundle={args.bundle} phases={phases} dry_run={args.dry_run}")
    if args.dry_run:
        info("dry-run mode — no actions taken")
        return 0
    info("restore pipeline not yet implemented (foundations PR only)")
    return 0
