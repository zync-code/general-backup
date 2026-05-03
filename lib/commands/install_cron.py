"""install-cron subcommand — install a daily capture cron job with retention.

Writes two files:
  /etc/cron.d/general-backup       daily cron trigger
  /usr/local/lib/gb-retain.sh      retention helper (prunes old bundles)

The cron entry runs as root (to access /etc/ and system postgres). The
retention helper removes all but the N newest .tar.zst files in --out-dir.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ..log import error, info, warn

CRON_PATH = Path("/etc/cron.d/general-backup")
RETAIN_SCRIPT_PATH = Path("/usr/local/lib/gb-retain.sh")


def run(args) -> int:
    out_dir = Path(args.out_dir)
    retain = int(args.retain)
    age_recipient = getattr(args, "age_recipient", None) or ""

    if retain < 1:
        error("--retain must be >= 1")
        return 1

    # ── Check we can write to /etc/cron.d/ ───────────────────────────────────
    if not CRON_PATH.parent.exists():
        error(f"/etc/cron.d/ does not exist — is this an Ubuntu 24.04 system?")
        return 4

    if not os.access(CRON_PATH.parent, os.W_OK):
        error(f"no write permission to {CRON_PATH.parent} — run as root or with sudo")
        return 4

    # ── Create output directory ───────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    info(f"install-cron: bundle directory: {out_dir}")

    # ── Write retention helper script ─────────────────────────────────────────
    _write_retain_script(out_dir, retain)

    # ── Write cron file ───────────────────────────────────────────────────────
    age_flag = f"--age-recipient {age_recipient}" if age_recipient else ""
    gb_bin = _find_gb_bin()

    cron_content = (
        f"# general-backup — daily capture with {retain}-bundle retention\n"
        f"# Managed by: general-backup install-cron\n"
        f"# Edit: re-run 'general-backup install-cron' with new options\n"
        f"SHELL=/bin/bash\n"
        f"PATH=/usr/local/bin:/usr/bin:/bin\n"
        f"\n"
        f"# Daily at 02:30 UTC\n"
        f"30 2 * * * root "
        f"{gb_bin} capture --out {out_dir} {age_flag} "
        f"&& bash {RETAIN_SCRIPT_PATH} {out_dir} {retain} "
        f">> /var/log/general-backup-cron.log 2>&1\n"
    )

    CRON_PATH.write_text(cron_content, encoding="utf-8")
    CRON_PATH.chmod(0o644)
    info(f"install-cron: wrote {CRON_PATH}")

    # ── Done ──────────────────────────────────────────────────────────────────
    info(
        f"install-cron: daily capture scheduled at 02:30 UTC, "
        f"output to {out_dir}, retaining {retain} bundle(s)"
    )
    if not age_recipient:
        warn(
            "install-cron: no --age-recipient set — bundles will be unencrypted. "
            "Re-run with --age-recipient to enable encryption."
        )

    return 0


def _write_retain_script(out_dir: Path, retain: int) -> None:
    script = (
        "#!/usr/bin/env bash\n"
        "# Retention helper for general-backup daily captures.\n"
        "# Usage: gb-retain.sh <bundle_dir> <keep_n>\n"
        "set -euo pipefail\n"
        'BUNDLE_DIR="${1:?bundle_dir required}"\n'
        'KEEP="${2:?keep_n required}"\n'
        "\n"
        "# List all bundles sorted oldest-first; delete all but the newest KEEP\n"
        'mapfile -t bundles < <(ls -1t "${BUNDLE_DIR}"/general-backup-*.tar.zst 2>/dev/null)\n'
        'count="${#bundles[@]}"\n'
        'if [[ "${count}" -le "${KEEP}" ]]; then\n'
        '    exit 0\n'
        "fi\n"
        "\n"
        'to_delete=("${bundles[@]:${KEEP}}")\n'
        'for f in "${to_delete[@]}"; do\n'
        '    rm -f "${f}"\n'
        '    echo "[$(date -u +%H:%M:%S)] removed old bundle: $(basename "${f}")"\n'
        "done\n"
    )

    RETAIN_SCRIPT_PATH.write_text(script, encoding="utf-8")
    RETAIN_SCRIPT_PATH.chmod(
        RETAIN_SCRIPT_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    info(f"install-cron: wrote {RETAIN_SCRIPT_PATH}")


def _find_gb_bin() -> str:
    """Return the path to the general-backup binary."""
    import shutil
    found = shutil.which("general-backup")
    if found:
        return found
    # Fall back to the repo-local binary
    local = Path(__file__).resolve().parent.parent.parent / "bin" / "general-backup"
    if local.exists():
        return str(local)
    return "general-backup"
