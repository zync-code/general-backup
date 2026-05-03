"""Capture phase: secrets — gather sensitive files and age-encrypt to secrets.age.

Sources collected (into a tar archive, then piped through age):
  - .env* files from each project tree (per manifest.projects[].env_paths)
  - ~/.ssh/* (full contents)
  - ~/.config/gh/* (GitHub tokens)
  - postgres role passwords (roles.json from staging-secrets, populated by postgres phase)
  - /etc/shadow lines for non-system users (from staging-secrets)
  - /etc/sudoers.d/* (from staging-secrets)
  - ~/.orchestrator/config/settings.json
"""
from __future__ import annotations

import glob
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from ..log import info, warn
from . import Context, PhaseError, project_entries


def run(ctx: Context) -> None:
    age_recipient = getattr(ctx.args, "age_recipient", None)
    if not age_recipient:
        warn("secrets: no --age-recipient set — secrets.age will NOT be created")
        warn("secrets: all sensitive files will be omitted from the bundle")
        ctx.manifest.secrets_encrypted = False
        return

    home = Path(f"/home/{os.getenv('USER', 'bot')}")
    secrets_src = Path(tempfile.mkdtemp(prefix="gb-secrets-src-"))

    try:
        _collect_env_files(ctx, secrets_src)
        _collect_ssh(home, secrets_src)
        _collect_gh_config(home, secrets_src)
        _collect_orchestrator_settings(home, secrets_src)
        _collect_from_secrets_staging(ctx.secrets_staging, secrets_src)

        _encrypt(secrets_src, ctx.staging / "secrets.age", age_recipient)
    finally:
        import shutil
        shutil.rmtree(secrets_src, ignore_errors=True)

    ctx.manifest.secrets_encrypted = True
    info("secrets: secrets.age created")


def _collect_env_files(ctx: Context, dest: Path) -> None:
    projects = project_entries(ctx.projects_json or {})
    for proj in projects:
        proj_dir = Path(proj.get("project_dir", ""))
        if not proj_dir.exists():
            continue
        name = proj["name"]
        env_paths: list[str] = []
        for pat in ["**/.env", "**/.env.*", "**/*.env"]:
            for match in proj_dir.glob(pat):
                if _should_skip(match):
                    continue
                rel = str(match.relative_to(proj_dir))
                env_paths.append(rel)
                env_dest = dest / ".env" / name / rel
                env_dest.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(match, env_dest)

        # Record env_paths in manifest
        for p in ctx.manifest.projects:
            if p.name == name:
                p.env_paths = sorted(set(p.env_paths) | set(env_paths))
                break

    if projects:
        info(f"secrets: collected env files from {len(projects)} project(s)")


def _should_skip(path: Path) -> bool:
    parts = path.parts
    for skip in ("node_modules", ".next", "dist", "build", ".git"):
        if skip in parts:
            return True
    return False


def _collect_ssh(home: Path, dest: Path) -> None:
    ssh_dir = home / ".ssh"
    if not ssh_dir.exists():
        return
    ssh_dest = dest / ".ssh"
    ssh_dest.mkdir(parents=True, exist_ok=True)
    for f in ssh_dir.iterdir():
        if f.is_file():
            import shutil
            shutil.copy2(f, ssh_dest / f.name)
    info(f"secrets: collected {len(list(ssh_dir.iterdir()))} SSH file(s)")


def _collect_gh_config(home: Path, dest: Path) -> None:
    gh_dir = home / ".config" / "gh"
    if not gh_dir.exists():
        return
    gh_dest = dest / ".config" / "gh"
    gh_dest.mkdir(parents=True, exist_ok=True)
    import shutil
    for f in gh_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, gh_dest / f.name)
    info("secrets: collected GitHub config")


def _collect_orchestrator_settings(home: Path, dest: Path) -> None:
    settings = home / ".orchestrator" / "config" / "settings.json"
    if not settings.exists():
        return
    settings_dest = dest / "orchestrator" / "settings.json"
    settings_dest.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(settings, settings_dest)
    info("secrets: collected orchestrator settings.json")


def _collect_from_secrets_staging(staging: Path, dest: Path) -> None:
    """Copy files from the capture-side secrets staging dir (shadow, sudoers, pg roles)."""
    if not staging or not staging.exists():
        return
    import shutil
    for item in staging.iterdir():
        dst = dest / "system" / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)


def _encrypt(src_dir: Path, output: Path, recipient: str) -> None:
    """Tar src_dir and pipe through age -r recipient → output."""
    if not _check_age():
        raise PhaseError("'age' is not installed — cannot encrypt secrets")

    info(f"secrets: encrypting to {output.name} (recipient: {recipient[:20]}…)")
    with open(output, "wb") as out_f:
        tar_proc = subprocess.Popen(
            ["tar", "-C", str(src_dir), "-c", "."],
            stdout=subprocess.PIPE,
        )
        age_proc = subprocess.Popen(
            ["age", "-r", recipient],
            stdin=tar_proc.stdout,
            stdout=out_f,
            stderr=subprocess.PIPE,
        )
        tar_proc.stdout.close()  # allow tar to receive SIGPIPE if age exits
        _, age_err = age_proc.communicate()
        tar_proc.wait()

        if tar_proc.returncode != 0:
            raise PhaseError(f"tar failed during secrets encryption (exit {tar_proc.returncode})")
        if age_proc.returncode != 0:
            raise PhaseError(
                f"age encryption failed: {age_err.decode('utf-8', errors='replace').strip()}"
            )


def _check_age() -> bool:
    import shutil
    return shutil.which("age") is not None
