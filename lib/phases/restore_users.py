"""Restore phase: users — ensure target user exists, apply passwd/group delta."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError, run_cmd


def run(ctx: RestoreContext) -> None:
    user = ctx.target_user
    uid = 1000

    # Ensure the user exists with the correct uid
    _ensure_user(user, uid)

    # Apply passwd.delta and group.delta for non-system users
    data_system = ctx.data / "system"
    _apply_delta(data_system / "passwd.delta", "/etc/passwd", user)
    _apply_delta(data_system / "group.delta", "/etc/group", user)

    info(f"restore/users: user {user!r} (uid {uid}) ready")


def _ensure_user(user: str, uid: int) -> None:
    result = subprocess.run(
        ["id", "-u", user], capture_output=True, text=True
    )
    if result.returncode == 0:
        actual_uid = int(result.stdout.strip())
        if actual_uid != uid:
            warn(
                f"restore/users: user {user!r} exists with uid {actual_uid}, "
                f"expected {uid} — continuing with existing uid"
            )
        else:
            info(f"restore/users: user {user!r} already exists with uid {uid}")
        return

    info(f"restore/users: creating user {user!r} with uid {uid}")
    try:
        run_cmd([
            "useradd",
            "--uid", str(uid),
            "--create-home",
            "--shell", "/bin/bash",
            user,
        ])
    except subprocess.CalledProcessError as exc:
        raise RestoreError(f"useradd failed: {exc}")


def _apply_delta(delta_path: Path, target_file: str, skip_user: str) -> None:
    """Merge lines from delta into target /etc/passwd or /etc/group."""
    if not delta_path.exists():
        return

    target = Path(target_file)
    existing = target.read_text(encoding="utf-8").splitlines()
    existing_names = {line.split(":")[0] for line in existing if line.strip()}

    added = 0
    with open(target, "a", encoding="utf-8") as f:
        for line in delta_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            name = line.split(":")[0]
            if name in existing_names or name == skip_user:
                continue
            f.write(line + "\n")
            existing_names.add(name)
            added += 1

    if added:
        info(f"restore/users: added {added} entr(ies) to {target_file}")
