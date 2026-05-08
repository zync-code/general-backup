"""CLI argument parser for general-backup.

Subcommands per PRD v2 §8:
  capture, restore, restore-agent, verify, diff, install, install-cron, phase

Each subcommand dispatches to a handler in lib/commands/.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Sequence

from . import __version__

# PRD §8 exit codes
EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_INTEGRITY = 2
EXIT_PARTIAL = 3
EXIT_PERMISSION = 4
EXIT_GIT_SYNC_CONFLICT = 5

ALL_CAPTURE_PHASES = [
    "preflight",
    "git-sync",
    "inventory",
    "packages",
    "system",
    "nginx",
    "cron",
    "postgres",
    "redis",
    "pm2",
    "state",
    "secrets",
    "checksums",
    "server_state",
    "package",        # legacy: local .tar.zst bundle (use --out)
]

ALL_RESTORE_PHASES = [
    "bootstrap",
    "packages",
    "users",
    "state-extract",
    "secrets-decrypt",
    "projects-clone",
    "postgres",
    "redis",
    "nginx",
    "pm2",
    "cron",
    "postcheck",
]

ALL_PHASES = sorted(set(ALL_CAPTURE_PHASES) | set(ALL_RESTORE_PHASES))


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
    cap.add_argument(
        "--include",
        type=_csv,
        default=["all"],
        help=f"Comma list of phases to include (default: all). Choices: all,{','.join(ALL_CAPTURE_PHASES)}",
    )
    cap.add_argument("--exclude", type=_csv, default=[], help="Phases to subtract from --include")
    snapshot_group = cap.add_mutually_exclusive_group()
    snapshot_group.add_argument(
        "--allow-snapshot-commit",
        dest="allow_snapshot_commit",
        action="store_true",
        default=True,
        help="Snapshot-commit dirty trees during git-sync (default)",
    )
    snapshot_group.add_argument(
        "--no-snapshot-commit",
        dest="allow_snapshot_commit",
        action="store_false",
        help="Refuse to snapshot-commit; abort if any project tree is dirty",
    )
    cap.add_argument(
        "--include-logs",
        action="store_true",
        help="Include ~/.orchestrator/logs/ in the state archive (default: skip)",
    )
    cap.add_argument("--dry-run", action="store_true", help="Print plan, do nothing")
    cap.add_argument("--sign", help="Path to a signing key for checksums.sha256")
    cap.add_argument("--quiet", action="store_true")
    cap.add_argument("--verbose", action="store_true")

    # restore-repo (GitHub server-state repo mode)
    rr = sub.add_parser(
        "restore-repo",
        help="Restore from zync-code/server-state GitHub repo (default restore method)",
    )
    rr.add_argument("--capture", help="Capture timestamp to restore (default: latest)")
    rr.add_argument("--target-user", default="bot")
    rr.add_argument("--age-identity", required=True, help="Path to age identity (private key) file")
    rr.add_argument(
        "--phases",
        type=_csv,
        default=["all"],
        help=f"Phases to run (default: all). Choices: all,{','.join(ALL_RESTORE_PHASES)}",
    )
    rr.add_argument("--skip-phases", type=_csv, default=[])
    rr.add_argument("--dry-run", action="store_true", help="Show plan, make no changes")
    rr.add_argument("--quiet", action="store_true")
    rr.add_argument("--verbose", action="store_true")

    # restore (script mode, legacy bundle)
    rs = sub.add_parser("restore", help="Replay a local bundle .tar.zst (legacy)")
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

    # restore-agent (LLM-driven)
    ra = sub.add_parser(
        "restore-agent",
        help="Replay a bundle by spawning a Claude agent against the runbook",
    )
    ra.add_argument("bundle", help="Path to bundle .tar.zst")
    ra.add_argument("--age-identity", help="Path to age identity (private key) file")
    ra.add_argument(
        "--auto-confirm",
        action="store_true",
        help="Skip the agent's pause-for-ack at end of run (default: pause)",
    )
    ra.add_argument("--quiet", action="store_true")
    ra.add_argument("--verbose", action="store_true")

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

    # install-cron
    ic = sub.add_parser(
        "install-cron",
        help="Install /etc/cron.d/general-backup for daily captures with retention",
    )
    ic.add_argument(
        "--retain",
        type=int,
        default=7,
        help="Number of bundles to retain (default: 7)",
    )
    ic.add_argument(
        "--out-dir",
        default="/var/backups/general-backup",
        help="Directory where daily bundles are written",
    )
    ic.add_argument("--age-recipient", help="age recipient public key for capture")

    # phase (advanced — run a single phase by name)
    ph = sub.add_parser(
        "phase",
        help="Run a single capture or restore phase (advanced)",
    )
    ph.add_argument("name", choices=ALL_PHASES, help="Phase name")
    ph.add_argument("--bundle", help="Bundle path (required for restore-side phases)")
    ph.add_argument("--age-identity", help="age identity for secrets-decrypt phase")
    ph.add_argument("--age-recipient", help="age recipient for secrets phase")
    ph.add_argument("--out", help="Output path for capture-side phases")
    ph.add_argument(
        "--include-logs",
        action="store_true",
        help="(state phase) include ~/.orchestrator/logs/",
    )
    ph.add_argument("--dry-run", action="store_true")
    ph.add_argument("--quiet", action="store_true")
    ph.add_argument("--verbose", action="store_true")

    return p


_DEFAULT_CAPTURE_PHASES = [p for p in ALL_CAPTURE_PHASES if p != "package"]


def _resolve_capture_phases(include: List[str], exclude: List[str], use_bundle: bool = False) -> List[str]:
    if "all" in include:
        if use_bundle:
            # Legacy mode: drop server_state, keep package
            phases = [p for p in ALL_CAPTURE_PHASES if p != "server_state"]
        else:
            # Default: drop legacy package phase
            phases = list(_DEFAULT_CAPTURE_PHASES)
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
        use_bundle = bool(getattr(args, "out", None))
        phases = _resolve_capture_phases(args.include, args.exclude, use_bundle=use_bundle)
        return cmd.run(args, phases)

    if args.command == "restore-repo":
        from .commands import restore_repo as cmd
        phases = _resolve_restore_phases(args.phases, args.skip_phases)
        return cmd.run(args, phases)

    if args.command == "restore":
        from .commands import restore as cmd
        phases = _resolve_restore_phases(args.phases, args.skip_phases)
        return cmd.run(args, phases)

    if args.command == "restore-agent":
        from .commands import restore_agent as cmd
        return cmd.run(args)

    if args.command == "verify":
        from .commands import verify as cmd
        return cmd.run(args)

    if args.command == "diff":
        from .commands import diff as cmd
        return cmd.run(args)

    if args.command == "install":
        from .commands import install as cmd
        return cmd.run(args)

    if args.command == "install-cron":
        from .commands import install_cron as cmd
        return cmd.run(args)

    if args.command == "phase":
        from .commands import phase as cmd
        return cmd.run(args)

    parser.error(f"unknown command: {args.command}")
    return EXIT_USER_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
