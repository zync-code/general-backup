"""Smoke tests for lib.cli — argparse wiring + phase resolution."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.cli import (  # noqa: E402
    ALL_CAPTURE_PHASES,
    ALL_RESTORE_PHASES,
    _resolve_capture_phases,
    _resolve_restore_phases,
    build_parser,
)


class CliParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def test_capture_default_flags(self) -> None:
        args = self.parser.parse_args(["capture"])
        self.assertEqual(args.command, "capture")
        self.assertTrue(args.allow_snapshot_commit)  # default ON
        self.assertFalse(args.include_logs)
        self.assertFalse(args.dry_run)

    def test_capture_no_snapshot_commit(self) -> None:
        args = self.parser.parse_args(["capture", "--no-snapshot-commit"])
        self.assertFalse(args.allow_snapshot_commit)

    def test_capture_age_recipient(self) -> None:
        args = self.parser.parse_args(["capture", "--age-recipient", "age1abc"])
        self.assertEqual(args.age_recipient, "age1abc")

    def test_restore_phases(self) -> None:
        args = self.parser.parse_args(["restore", "/tmp/x.tar.zst", "--phases", "postgres,nginx"])
        self.assertEqual(args.phases, ["postgres", "nginx"])

    def test_restore_agent(self) -> None:
        args = self.parser.parse_args(["restore-agent", "/tmp/x.tar.zst", "--auto-confirm"])
        self.assertEqual(args.command, "restore-agent")
        self.assertTrue(args.auto_confirm)

    def test_install_cron_defaults(self) -> None:
        args = self.parser.parse_args(["install-cron"])
        self.assertEqual(args.retain, 7)
        self.assertEqual(args.out_dir, "/var/backups/general-backup")

    def test_phase_choice_must_be_known(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["phase", "totally-made-up-phase"])

    def test_phase_known(self) -> None:
        args = self.parser.parse_args(["phase", "git-sync"])
        self.assertEqual(args.name, "git-sync")


class PhaseResolutionTests(unittest.TestCase):
    def test_capture_all(self) -> None:
        # Default (no --out) omits legacy 'package' phase
        phases = _resolve_capture_phases(["all"], [])
        self.assertIn("server_state", phases)
        self.assertNotIn("package", phases)

    def test_capture_all_bundle_mode(self) -> None:
        phases = _resolve_capture_phases(["all"], [], use_bundle=True)
        self.assertIn("package", phases)
        self.assertNotIn("server_state", phases)

    def test_capture_subset(self) -> None:
        self.assertEqual(
            _resolve_capture_phases(["postgres", "redis"], []),
            ["postgres", "redis"],
        )

    def test_capture_exclude(self) -> None:
        out = _resolve_capture_phases(["all"], ["postgres", "redis"])
        self.assertNotIn("postgres", out)
        self.assertNotIn("redis", out)
        self.assertIn("nginx", out)

    def test_restore_all(self) -> None:
        self.assertEqual(_resolve_restore_phases(["all"], []), ALL_RESTORE_PHASES)

    def test_restore_skip(self) -> None:
        out = _resolve_restore_phases(["all"], ["postgres"])
        self.assertNotIn("postgres", out)

    def test_capture_phase_set_matches_prd(self) -> None:
        expected = {
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
            "package",        # legacy bundle mode
        }
        self.assertEqual(set(ALL_CAPTURE_PHASES), expected)

    def test_restore_phase_set_matches_prd(self) -> None:
        expected = {
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
        }
        self.assertEqual(set(ALL_RESTORE_PHASES), expected)


if __name__ == "__main__":
    unittest.main()
