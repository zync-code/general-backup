"""install-cron subcommand — install a daily capture cron job.

Writes /etc/cron.d/general-backup.

Default mode: push to zync-code/server-state (requires --age-recipient).
Legacy mode:  --out-dir produces local .tar.zst bundles with retention.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ..log import error, info, warn

CRON_PATH = Path("/etc/cron.d/general-backup")
RETAIN_SCRIPT_PATH = Path("/usr/local/lib/gb-retain.sh")


def run(args) -> int:
    age_recipient = getattr(args, "age_recipient", None) or ""
    out_dir_arg = getattr(args, "out_dir", None)
    retain = int(args.retain)
    bundle_mode = bool(out_dir_arg and out_dir_arg != "/var/backups/general-backup")

    # ── Check we can write to /etc/cron.d/ ───────────────────────────────────
    if not CRON_PATH.parent.exists():
        error("/etc/cron.d/ does not exist — is this an Ubuntu 24.04 system?")
        return 4

    if not os.access(CRON_PATH.parent, os.W_OK):
        error(f"no write permission to {CRON_PATH.parent} — run as root or with sudo")
        return 4

    gb_bin = _find_gb_bin()

    if bundle_mode:
        return _install_bundle_mode(gb_bin, Path(out_dir_arg), retain, age_recipient)
    else:
        return _install_repo_mode(gb_bin, age_recipient)


def _install_repo_mode(gb_bin: str, age_recipient: str) -> int:
    """Default: push daily capture to zync-code/server-state."""
    if not age_recipient:
        warn(
            "install-cron: no --age-recipient set — captures will not be encrypted. "
            "Re-run with --age-recipient <pubkey> to enable encryption."
        )

    age_flag = f"--age-recipient {age_recipient}" if age_recipient else ""
    cron_content = (
        "# general-backup — daily capture → zync-code/server-state\n"
        "# Managed by: general-backup install-cron\n"
        "# Edit: re-run 'general-backup install-cron' with new options\n"
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/bin:/usr/bin:/bin\n"
        "\n"
        "# Daily at 02:30 UTC\n"
        f"30 2 * * * root {gb_bin} capture {age_flag} "
        ">> /var/log/general-backup-cron.log 2>&1\n"
    )

    CRON_PATH.write_text(cron_content, encoding="utf-8")
    CRON_PATH.chmod(0o644)
    info(f"install-cron: wrote {CRON_PATH}")
    info("install-cron: daily capture scheduled at 02:30 UTC → server-state repo")
    return 0


def _install_bundle_mode(gb_bin: str, out_dir: Path, retain: int, age_recipient: str) -> int:
    """Legacy: produce local .tar.zst bundles with retention."""
    if retain < 1:
        error("--retain must be >= 1")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    info(f"install-cron: bundle directory: {out_dir}")

    _write_retain_script(out_dir, retain)

    age_flag = f"--age-recipient {age_recipient}" if age_recipient else ""
    cron_content = (
        f"# general-backup — daily bundle capture, retain {retain}\n"
        "# Managed by: general-backup install-cron\n"
        "SHELL=/bin/bash\n"
        "PATH=/usr/local/bin:/usr/bin:/bin\n"
        "\n"
        f"30 2 * * * root "
        f"{gb_bin} capture --out {out_dir} {age_flag} "
        f"&& bash {RETAIN_SCRIPT_PATH} {out_dir} {retain} "
        ">> /var/log/general-backup-cron.log 2>&1\n"
    )

    CRON_PATH.write_text(cron_content, encoding="utf-8")
    CRON_PATH.chmod(0o644)
    info(f"install-cron: wrote {CRON_PATH}")
    info(f"install-cron: daily bundle capture at 02:30 UTC → {out_dir}, retain {retain}")
    if not age_recipient:
        warn("install-cron: no --age-recipient — bundles will be unencrypted")
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
