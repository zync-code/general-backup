"""install-cron subcommand — stub. Real implementation lands in GH-27."""
from __future__ import annotations

from ..log import info


def run(args) -> int:
    info(
        f"install-cron retain={args.retain} out_dir={args.out_dir} "
        f"age_recipient={'<set>' if args.age_recipient else '<unset>'}"
    )
    info("install-cron not yet implemented (CLI wiring only)")
    return 0
