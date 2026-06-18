"""Capture phase: system — passwd/group delta and shadow/sudoers staging.

Output (in staging/data/system/):
  passwd.delta  — /etc/passwd lines for uids 1000–64999
  group.delta   — /etc/group lines for non-system groups

Staged for age-encryption (in ctx.secrets_staging/):
  shadow.delta       — /etc/shadow lines for the same users
  sudoers.d/         — copy of /etc/sudoers.d/*
  sudoers_main.delta — custom "User privilege specification" lines added
                        directly to /etc/sudoers (outside sudoers.d), e.g.
                        a NOPASSWD grant for the deploy user. Stock
                        Defaults/root/%admin/%sudo/@includedir lines are
                        excluded — only the non-stock additions are kept.

Both /etc/shadow and /etc/sudoers.d/* are root-only readable, and capture
always runs as the unprivileged target user — so every read here goes
through `sudo cat` rather than a direct file open.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List

from ..log import info, warn
from . import Context

_STOCK_SUDOERS_USER_LINES = {
    "root\tALL=(ALL:ALL) ALL",
    "%admin ALL=(ALL) ALL",
    "%sudo\tALL=(ALL:ALL) ALL",
}


def _sudo_read(path: str) -> str:
    """Read a root-only file via `sudo cat`. Returns "" if unreadable."""
    result = subprocess.run(
        ["sudo", "-n", "cat", path], capture_output=True, text=True
    )
    if result.returncode != 0:
        return ""
    return result.stdout

_UID_MIN = 1000
_UID_MAX = 64999


def run(ctx: Context) -> None:
    system_dir = ctx.ensure_dir("data", "system")
    secrets_sys = ctx.secrets_dir("system")

    # passwd.delta
    passwd_lines = _filter_passwd(_UID_MIN, _UID_MAX)
    if passwd_lines:
        (system_dir / "passwd.delta").write_text(
            "\n".join(passwd_lines) + "\n", encoding="utf-8"
        )
        info(f"system: passwd.delta — {len(passwd_lines)} entr(ies)")
    else:
        warn("system: no non-system user entries found in /etc/passwd")

    # group.delta (groups whose gid >= 1000)
    group_lines = _filter_group(_UID_MIN, _UID_MAX)
    if group_lines:
        (system_dir / "group.delta").write_text(
            "\n".join(group_lines) + "\n", encoding="utf-8"
        )
        info(f"system: group.delta — {len(group_lines)} entr(ies)")

    # shadow.delta → secrets staging
    shadow_lines = _filter_shadow(passwd_lines)
    if shadow_lines:
        (secrets_sys / "shadow.delta").write_text(
            "\n".join(shadow_lines) + "\n", encoding="utf-8"
        )
        info(f"system: shadow.delta → secrets staging ({len(shadow_lines)} lines)")
    else:
        warn("system: /etc/shadow not readable or no matching users — shadow lines not captured")

    # sudoers.d → secrets staging
    _copy_sudoers(secrets_sys)

    # custom lines from the main /etc/sudoers (outside sudoers.d) → secrets staging
    _copy_main_sudoers_extras(secrets_sys)

    ctx.manifest.components["system"] = {
        "users": [line.split(":")[0] for line in passwd_lines],
        "uid_range": [_UID_MIN, _UID_MAX],
    }
    info("system: done")


def _filter_passwd(uid_min: int, uid_max: int) -> List[str]:
    try:
        lines = Path("/etc/passwd").read_text(encoding="utf-8").splitlines()
    except PermissionError:
        return []
    result = []
    for line in lines:
        parts = line.split(":")
        if len(parts) >= 4:
            try:
                uid = int(parts[2])
                if uid_min <= uid <= uid_max:
                    result.append(line)
            except ValueError:
                pass
    return result


def _filter_group(gid_min: int, gid_max: int) -> List[str]:
    try:
        lines = Path("/etc/group").read_text(encoding="utf-8").splitlines()
    except PermissionError:
        return []
    result = []
    for line in lines:
        parts = line.split(":")
        if len(parts) >= 3:
            try:
                gid = int(parts[2])
                if gid_min <= gid <= gid_max:
                    result.append(line)
            except ValueError:
                pass
    return result


def _filter_shadow(passwd_lines: List[str]) -> List[str]:
    usernames = {line.split(":")[0] for line in passwd_lines}
    content = _sudo_read("/etc/shadow")
    if not content:
        try:
            content = Path("/etc/shadow").read_text(encoding="utf-8")
        except PermissionError:
            return []
    shadow = content.splitlines()
    return [line for line in shadow if line.split(":")[0] in usernames]


def _copy_sudoers(dest: Path) -> None:
    sudoers_src = Path("/etc/sudoers.d")
    if not sudoers_src.exists():
        return
    sudoers_dest = dest / "sudoers.d"
    sudoers_dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sorted(sudoers_src.glob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            shutil.copy2(f, sudoers_dest / f.name)
            count += 1
            continue
        except PermissionError:
            pass
        content = _sudo_read(str(f))
        if content:
            (sudoers_dest / f.name).write_text(content, encoding="utf-8")
            count += 1
        else:
            warn(f"system: cannot read {f} (even via sudo) — skipping")
    if count:
        info(f"system: sudoers.d → secrets staging ({count} file(s))")


def _copy_main_sudoers_extras(dest: Path) -> None:
    content = _sudo_read("/etc/sudoers")
    if not content:
        warn("system: cannot read /etc/sudoers (even via sudo) — skipping main-sudoers extras")
        return

    extras = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in _STOCK_SUDOERS_USER_LINES:
            continue
        if stripped.startswith(("Defaults", "@include", "Host_Alias", "User_Alias", "Cmnd_Alias")):
            continue
        # A "User privilege specification" line: "<user> <host>=(...) ..."
        if "ALL=" in stripped or "=(" in stripped:
            extras.append(line)

    if extras:
        (dest / "sudoers_main.delta").write_text("\n".join(extras) + "\n", encoding="utf-8")
        info(f"system: sudoers_main.delta — {len(extras)} custom line(s) from /etc/sudoers")
