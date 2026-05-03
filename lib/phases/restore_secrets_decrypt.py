"""Restore phase: secrets-decrypt — age-decrypt secrets.age to tmpfs staging."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import List

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError, ensure_tmpfs_staging


def run(ctx: RestoreContext) -> None:
    secrets_age = ctx.bundle_root / "secrets.age"
    age_identity = getattr(ctx.args, "age_identity", None) or ""

    if not secrets_age.exists():
        if ctx.manifest.secrets_encrypted:
            raise RestoreError(
                "secrets.age not found in bundle but manifest.secrets_encrypted=true"
            )
        warn("restore/secrets-decrypt: no secrets.age found and not expected — skipping")
        return

    if not age_identity:
        raise RestoreError(
            "secrets.age requires an age identity — pass --age-identity <key_file>"
        )

    if not shutil.which("age"):
        raise RestoreError("'age' is not installed — run bootstrap.sh first")

    # Decrypt into a tmpfs-backed staging directory
    staging = ensure_tmpfs_staging("gb-secrets-staging")
    staging.chmod(0o700)
    ctx.secrets_staging = staging

    # age decrypts to a tar archive; extract it
    decrypted_tar = staging / "secrets.tar"
    info("restore/secrets-decrypt: decrypting secrets.age")
    try:
        with open(decrypted_tar, "wb") as out_f:
            result = subprocess.run(
                ["age", "-d", "-i", age_identity, str(secrets_age)],
                stdout=out_f,
                stderr=subprocess.PIPE,
            )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            if "no identity matched" in stderr.lower():
                raise RestoreError(
                    "age private key does not match any recipient in secrets.age — "
                    "wrong key file?"
                )
            raise RestoreError(f"age decryption failed: {stderr.strip()}")
    except FileNotFoundError:
        raise RestoreError("'age' binary not found")

    info("restore/secrets-decrypt: extracting decrypted secrets")
    secrets_dir = staging / "contents"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(decrypted_tar, "r:*") as tf:
            tf.extractall(secrets_dir, filter="data")
    except Exception as exc:
        raise RestoreError(f"failed to extract decrypted secrets: {exc}")
    decrypted_tar.unlink()

    home = ctx.target_home()

    # Install SSH keys
    _install_ssh(secrets_dir, home)

    # Install GitHub hosts.yml
    _install_gh_hosts(secrets_dir, home)

    # Install project env files
    _install_env_files(secrets_dir, ctx)

    # Install shadow + sudoers (from system/ sub-dir in secrets)
    _install_shadow_sudoers(secrets_dir)

    # Load postgres role passwords into extras for the postgres phase
    _load_pg_passwords(secrets_dir, ctx)

    info("restore/secrets-decrypt: secrets installed")


def _install_ssh(secrets_dir: Path, home: Path) -> None:
    ssh_src = secrets_dir / ".ssh"
    if not ssh_src.exists():
        return
    ssh_dest = home / ".ssh"
    ssh_dest.mkdir(mode=0o700, exist_ok=True)
    for f in ssh_src.iterdir():
        dest = ssh_dest / f.name
        shutil.copy2(f, dest)
        dest.chmod(0o600)
    info(f"restore/secrets-decrypt: installed {len(list(ssh_src.iterdir()))} SSH file(s)")


def _install_gh_hosts(secrets_dir: Path, home: Path) -> None:
    gh_src = secrets_dir / ".config" / "gh"
    if not gh_src.exists():
        return
    gh_dest = home / ".config" / "gh"
    gh_dest.mkdir(parents=True, exist_ok=True)
    for f in gh_src.iterdir():
        shutil.copy2(f, gh_dest / f.name)
    info("restore/secrets-decrypt: installed GitHub config")


def _install_env_files(secrets_dir: Path, ctx: RestoreContext) -> None:
    env_dir = secrets_dir / "env"
    if not env_dir.exists():
        return
    installed = 0
    for project in ctx.manifest.projects:
        proj_dir = Path(project.project_dir)
        for env_rel in project.env_paths:
            src = env_dir / project.name / env_rel
            if not src.exists():
                warn(f"restore/secrets-decrypt: env file not found in secrets: {project.name}/{env_rel}")
                continue
            dest = proj_dir / env_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            dest.chmod(0o600)
            installed += 1
    if installed:
        info(f"restore/secrets-decrypt: installed {installed} env file(s)")


def _install_shadow_sudoers(secrets_dir: Path) -> None:
    system_dir = secrets_dir / "system"
    if not system_dir.exists():
        return

    shadow_src = system_dir / "shadow.delta"
    if shadow_src.exists():
        try:
            existing = Path("/etc/shadow").read_text(encoding="utf-8")
            existing_users = {line.split(":")[0] for line in existing.splitlines()}
            with open("/etc/shadow", "a", encoding="utf-8") as f:
                for line in shadow_src.read_text().splitlines():
                    name = line.split(":")[0]
                    if name not in existing_users:
                        f.write(line + "\n")
        except Exception as exc:
            warn(f"restore/secrets-decrypt: could not install shadow entries: {exc}")

    sudoers_src = system_dir / "sudoers.d"
    if sudoers_src.exists():
        import shutil as _sh
        dest_dir = Path("/etc/sudoers.d")
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in sudoers_src.iterdir():
            dest = dest_dir / f.name
            _sh.copy2(f, dest)
            dest.chmod(0o440)


def _load_pg_passwords(secrets_dir: Path, ctx: RestoreContext) -> None:
    roles_path = secrets_dir / "postgres" / "roles.json"
    if roles_path.exists():
        try:
            ctx.extras["pg_role_passwords"] = json.loads(roles_path.read_text())
            info(
                f"restore/secrets-decrypt: loaded {len(ctx.extras['pg_role_passwords'])} "
                "postgres role password(s)"
            )
        except Exception as exc:
            warn(f"restore/secrets-decrypt: could not load pg role passwords: {exc}")
