"""install subcommand — stub. Real bootstrap.sh ships in Epic C."""
from __future__ import annotations

from ..log import info


def run(args) -> int:
    info("install: would invoke bootstrap.sh on this host")
    info("install not yet implemented (foundations PR only)")
    return 0
