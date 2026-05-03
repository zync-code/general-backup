"""CLI argument parser for general-backup.

Subcommands: capture, restore, verify, diff, install.
Each subcommand dispatches to a handler in the corresponding lib module.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Sequence

from . import __version__

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_INTEGRITY = 2
EXIT_PARTIAL = 3
EXIT_PERMISSION = 4

ALL_CAPTURE_PHASES = [
    "inventory",
    "packages",
    "system",
    "nginx",
    "cron",
    "postgres",
    "redis",
    "pm2",
    "files",
    "secrets",
    "checksums",
]

ALL_RESTORE_PHASES = [
    "bootstrap",
    "packages",
    "users",
    "files",
    "secrets",
    "postgres",
    "redis",
    "nginx",
    "pm2",
    "cron",
]


def _csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="general-backup",
        description="Full-server snapshot & restore toolkit (Ubuntu 24.04).",
    )
    p.add_argument("--version", action="version", version=f"general-backup {__version__}")

    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # capture
    cap = sub.add_parser("capture", help="Produce a bundle from this host")
    cap.add_argument("--out", help="Output bundle path (default: ./general-backup-<host>-<stamp>.tar.zst)")
    cap.add_argument("--age-recipient", help="age recipient public key (X25519)")
    cap.add_argument("--age-passphrase", action="store_true", help="Use a passphrase instead of a recipient key")
    cap.add_argument(
        "--include",
        type=_csv,
        default=["all"],
        help=f"Comma list of phases to include (default: all). Choices: all,{','.join(ALL_CAPTURE_PHASES)}",
    )
    cap.add_argument("--exclude", type=_csv, default=[], help="Phases to subtract from --include")
    cap.add_argument("--dry-run", action="store_true", help="Print plan, do nothing")
    cap.add_argument("--sign", help="Path to a signing key for checksums.sha256")
    cap.add_argument("--quiet", action="store_true")
    cap.add_argument("--verbose", action="store_true")

    # restore
    rs = sub.add_parser("restore", help="Replay a bundle on a fresh host")
    rs.add_argument("bundle", help="Path to bundle .tar.zst")
    rs.add_argument("--target-user", default="bot")
    rs.add_argument("--age-identity", help="Path to age identity (private key) file")
    rs.add_argument(
        "--phases",
        type=_csv,
        default=["all"],
        help=f"Phases to run (default: all). Choices: all,{','.join(ALL_RESTORE_PHASES)}",
    )
    rs.add_argument("--skip-phases", type=_csv, default=[])
    rs.add_argument("--dry-run", action="store_true", help="Show diff, no changes")
    rs.add_argument("--force", action="store_true", help="Overwrite existing data")
    rs.add_argument("--quiet", action="store_true")
    rs.add_argument("--verbose", action="store_true")

    # verify
    vf = sub.add_parser("verify", help="Verify bundle integrity")
    vf.add_argument("bundle", help="Path to bundle .tar.zst")
    vf.add_argument("--age-identity", help="Optional: only required to test secrets decryptability")

    # diff
    df = sub.add_parser("diff", help="Diff a bundle against the live host")
    df.add_argument("bundle", help="Path to bundle .tar.zst")
    df.add_argument("--age-identity")

    # install
    ins = sub.add_parser("install", help="Bootstrap apt deps on a fresh Ubuntu 24.04 host")
    ins.add_argument("--force-os", action="store_true", help="Skip the Ubuntu 24.04 check")

    return p


def _resolve_capture_phases(include: List[str], exclude: List[str]) -> List[str]:
    if "all" in include:
        phases = list(ALL_CAPTURE_PHASES)
    else:
        phases = [p for p in include if p in ALL_CAPTURE_PHASES]
    return [p for p in phases if p not in exclude]


def _resolve_restore_phases(phases: List[str], skip: List[str]) -> List[str]:
    if "all" in phases:
        ph = list(ALL_RESTORE_PHASES)
    else:
        ph = [p for p in phases if p in ALL_RESTORE_PHASES]
    return [p for p in ph if p not in skip]


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "capture":
        from .commands import capture as cmd
        phases = _resolve_capture_phases(args.include, args.exclude)
        return cmd.run(args, phases)

    if args.command == "restore":
        from .commands import restore as cmd
        phases = _resolve_restore_phases(args.phases, args.skip_phases)
        return cmd.run(args, phases)

    if args.command == "verify":
        from .commands import verify as cmd
        return cmd.run(args)

    if args.command == "diff":
        from .commands import diff as cmd
        return cmd.run(args)

    if args.command == "install":
        from .commands import install as cmd
        return cmd.run(args)

    parser.error(f"unknown command: {args.command}")
    return EXIT_USER_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
