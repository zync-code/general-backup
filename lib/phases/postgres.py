"""Capture phase: postgres — dump all databases and extract role passwords.

Steps:
  1. pg_dumpall --globals-only --no-role-passwords → globals.sql
  2. pg_dump --format=custom --compress=9 per database → <db>.dump
  3. Extract role password hashes from pg_authid (SUPERUSER required) → roles.json
     in ctx.secrets_staging (for age-encryption by the secrets phase)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List

from ..log import info, warn
from . import Context, PhaseError

# Databases to skip (system-managed)
_SKIP_DBS = {"template0", "template1", "postgres"}


def run(ctx: Context) -> None:
    pg_dir = ctx.ensure_dir("data", "postgres")
    secrets_pg_dir = ctx.secrets_dir("postgres")

    # List databases
    dbs = _list_databases()
    user_dbs = [db for db in dbs if db not in _SKIP_DBS]
    info(f"postgres: found {len(user_dbs)} user database(s): {', '.join(user_dbs)}")

    # globals.sql (roles + tablespaces, no plaintext passwords)
    _dump_globals(pg_dir / "globals.sql")

    # per-DB dumps
    for db in user_dbs:
        _dump_db(db, pg_dir / f"{db}.dump")

    # Role password hashes → secrets staging
    _extract_role_passwords(secrets_pg_dir / "roles.json")

    ctx.manifest.components["postgres"] = {
        "version": _pg_version(),
        "databases": user_dbs,
    }
    info("postgres: done")


def _list_databases() -> List[str]:
    result = subprocess.run(
        ["psql", "-U", "postgres", "-lqt", "--no-align"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PhaseError(f"psql -lqt failed: {result.stderr.strip()}")

    dbs: List[str] = []
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if parts and parts[0].strip():
            dbs.append(parts[0].strip())
    return dbs


def _dump_globals(output: Path) -> None:
    info("postgres: pg_dumpall --globals-only --no-role-passwords")
    result = subprocess.run(
        ["pg_dumpall", "-U", "postgres", "--globals-only", "--no-role-passwords"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise PhaseError(f"pg_dumpall globals failed: {result.stderr.decode()[:200]}")
    output.write_bytes(result.stdout)
    info(f"postgres: globals.sql ({len(result.stdout):,} bytes)")


def _dump_db(db: str, output: Path) -> None:
    info(f"postgres: pg_dump {db!r}")
    result = subprocess.run(
        [
            "pg_dump",
            "-U", "postgres",
            "--format=custom",
            "--compress=9",
            "--file", str(output),
            db,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(f"postgres: pg_dump {db!r} failed: {result.stderr[:200]}")
    else:
        size = output.stat().st_size if output.exists() else 0
        info(f"postgres: {db}.dump ({size:,} bytes)")


def _extract_role_passwords(output: Path) -> None:
    """Extract pw hashes from pg_authid (requires SUPERUSER)."""
    sql = "SELECT rolname, rolpassword FROM pg_authid WHERE rolpassword IS NOT NULL;"
    result = subprocess.run(
        ["psql", "-U", "postgres", "-c", sql, "-t", "--no-align"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        warn(
            f"postgres: could not read pg_authid (no SUPERUSER?): {result.stderr[:200]}"
            " — role passwords will not be captured"
        )
        return

    passwords: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            passwords[parts[0].strip()] = parts[1].strip()

    if passwords:
        output.write_text(json.dumps(passwords, indent=2), encoding="utf-8")
        info(f"postgres: captured {len(passwords)} role password hash(es) → secrets staging")
    else:
        warn("postgres: no role passwords found in pg_authid")


def _pg_version() -> str:
    result = subprocess.run(
        ["psql", "--version"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"
