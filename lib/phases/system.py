"""Capture phase: system — passwd/group delta and shadow/sudoers staging.

Output (in staging/data/system/):
  passwd.delta  — /etc/passwd lines for uids 1000–64999
  group.delta   — /etc/group lines for non-system groups

Staged for age-encryption (in ctx.secrets_staging/):
  shadow.delta  — /etc/shadow lines for the same users
  sudoers.d/    — copy of /etc/sudoers.d/*
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from ..log import info, warn
from . import Context

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
    try:
        shadow = Path("/etc/shadow").read_text(encoding="utf-8").splitlines()
    except PermissionError:
        return []
    return [line for line in shadow if line.split(":")[0] in usernames]


def _copy_sudoers(dest: Path) -> None:
    sudoers_src = Path("/etc/sudoers.d")
    if not sudoers_src.exists():
        return
    sudoers_dest = dest / "sudoers.d"
    sudoers_dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sudoers_src.iterdir():
        if f.is_file() and not f.name.startswith("."):
            try:
                shutil.copy2(f, sudoers_dest / f.name)
                count += 1
            except PermissionError:
                warn(f"system: cannot read {f} — skipping")
    if count:
        info(f"system: sudoers.d → secrets staging ({count} file(s))")
