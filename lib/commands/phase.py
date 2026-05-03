"""phase subcommand — run a single capture- or restore-side phase by name.

This is the operator escape hatch and the building block the agent-mode
restore uses to invoke individual restore steps from the runbook.

Real phase modules land in subsequent PRs (GH-18 through GH-25). For
now the dispatcher just prints what it would do.
"""
from __future__ import annotations

from ..cli import ALL_CAPTURE_PHASES, ALL_RESTORE_PHASES
from ..log import info, warn


def run(args) -> int:
    name = args.name
    side = "capture" if name in ALL_CAPTURE_PHASES else "restore"
    if name in ALL_CAPTURE_PHASES and name in ALL_RESTORE_PHASES:
        side = "both"

    info(f"phase {name!r} ({side}-side) bundle={args.bundle or '<none>'} dry_run={args.dry_run}")

    if side == "restore" and not args.bundle:
        warn(f"phase {name!r} is restore-side and requires --bundle")
        return 1

    info(f"phase {name!r} not yet implemented (CLI wiring only)")
    return 0
