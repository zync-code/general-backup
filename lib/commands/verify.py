"""verify subcommand — bundle integrity checks.

Checks performed (in order):
  1. Tarball opens without error
  2. manifest.json present + parses + schema_version <= tool version
  3. checksums.sha256 present
  4. Every file in checksums.sha256 is present in the bundle and hash matches
  5. (Optional, requires --age-identity) secrets.age is addressable to the
     identity — detected by attempting a header-level decrypt and distinguishing
     "no matching recipient" from all other outcomes.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from ..log import error, info, warn
from ..manifest import Manifest, SCHEMA_VERSION, parse_checksums, sha256_file


def run(args) -> int:
    bundle = Path(args.bundle)
    if not bundle.is_file():
        error(f"bundle not found: {bundle}")
        return 1

    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="gb-verify-") as tmpdir:
        root = Path(tmpdir)

        # ── 1. Extract bundle ────────────────────────────────────────────────
        info(f"verify: opening {bundle.name}")
        result = subprocess.run(
            ["tar", "-xf", str(bundle), "-C", str(root)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            error(f"failed to open/extract bundle: {result.stderr.strip()}")
            return 2

        # Find the top-level bundle directory (general-backup-<host>-<stamp>/)
        tops = [p for p in root.iterdir() if p.is_dir()]
        if len(tops) != 1:
            error(f"expected one top-level directory in bundle, found {len(tops)}")
            return 2
        bundle_root = tops[0]

        # ── 2. Manifest ──────────────────────────────────────────────────────
        manifest_path = bundle_root / "manifest.json"
        if not manifest_path.exists():
            error("manifest.json not found in bundle")
            return 2

        try:
            manifest = Manifest.read(manifest_path)
        except Exception as exc:
            error(f"failed to parse manifest.json: {exc}")
            return 2

        errs = manifest.validate()
        for e in errs:
            failures.append(f"manifest: {e}")

        if manifest.schema_version > SCHEMA_VERSION:
            failures.append(
                f"bundle schema_version={manifest.schema_version} is newer than "
                f"this tool ({SCHEMA_VERSION}) — upgrade general-backup first"
            )

        info(
            f"verify: manifest ok — captured_at={manifest.captured_at} "
            f"schema_version={manifest.schema_version} "
            f"projects={len(manifest.projects)}"
        )

        # ── 3 + 4. Checksums ─────────────────────────────────────────────────
        checksum_path = bundle_root / (manifest.checksums_file or "checksums.sha256")
        if not checksum_path.exists():
            failures.append(f"checksums file not found: {checksum_path.name}")
        else:
            expected = parse_checksums(checksum_path)
            mismatches = 0
            missing = 0
            for rel, digest in expected.items():
                file_path = bundle_root / rel
                if not file_path.exists():
                    failures.append(f"missing file listed in checksums: {rel}")
                    missing += 1
                    continue
                actual = sha256_file(file_path)
                if actual != digest:
                    failures.append(f"checksum mismatch: {rel}")
                    mismatches += 1

            if mismatches == 0 and missing == 0:
                info(f"verify: checksums ok ({len(expected)} files)")
            else:
                warn(f"verify: {mismatches} checksum mismatch(es), {missing} missing file(s)")

        # ── 5. Age identity check (optional) ──────────────────────────────────
        age_identity = getattr(args, "age_identity", None)
        if age_identity:
            secrets_path = bundle_root / "secrets.age"
            if not secrets_path.exists():
                if manifest.secrets_encrypted:
                    failures.append("secrets.age missing but manifest.secrets_encrypted=true")
            else:
                result = _check_age_identity(secrets_path, age_identity)
                if result is True:
                    info("verify: secrets.age — identity matches a recipient")
                elif result is None:
                    warn("verify: secrets.age — could not determine identity match (age not installed?)")
                else:
                    failures.append(
                        "secrets.age — provided identity does not match any recipient "
                        "(no identity matched a recipient)"
                    )

        # ── Result ────────────────────────────────────────────────────────────
        if failures:
            error(f"verify FAILED ({len(failures)} issue(s)):")
            for f in failures:
                error(f"  • {f}")
            return 2

        info("verify: all checks passed")
        return 0


def _check_age_identity(secrets_path: Path, identity_path: str) -> bool | None:
    """Return True if the identity matches a recipient, False if not, None if undetermined."""
    try:
        result = subprocess.run(
            ["age", "-d", "-i", identity_path, str(secrets_path)],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True
        stderr = result.stderr.decode("utf-8", errors="replace").lower()
        if "no identity matched" in stderr or "no matching" in stderr:
            return False
        # age started to decrypt (recipient matched) but payload failed for some
        # other reason — treat as "identity matched"
        return True
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
