"""Restore phase: postgres — restore globals, role passwords, and per-DB dumps."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict

from ..log import info, warn
from .restore_base import RestoreContext, RestoreError


def run(ctx: RestoreContext) -> None:
    pg_dir = ctx.data / "postgres"
    if not pg_dir.exists():
        warn("restore/postgres: no postgres data in bundle — skipping")
        return

    # Start PostgreSQL if not running
    _ensure_pg_running()

    # Restore globals (roles, tablespaces — no passwords)
    globals_sql = pg_dir / "globals.sql"
    if globals_sql.exists():
        info("restore/postgres: restoring globals.sql")
        _psql_file(globals_sql)
    else:
        warn("restore/postgres: globals.sql not found")

    # Restore role passwords from secrets staging
    pg_passwords: Dict[str, str] = ctx.extras.get("pg_role_passwords", {})
    if pg_passwords:
        info(f"restore/postgres: restoring {len(pg_passwords)} role password(s)")
        for role, pwhash in pg_passwords.items():
            try:
                _psql_cmd(f"ALTER ROLE {_pg_ident(role)} PASSWORD '{pwhash}';")
            except Exception as exc:
                warn(f"restore/postgres: could not set password for {role!r}: {exc}")

    # Restore per-database dumps
    dump_files = sorted(pg_dir.glob("*.dump"))
    for dump in dump_files:
        db_name = dump.stem
        _restore_db(db_name, dump)

    info("restore/postgres: done")


def _ensure_pg_running() -> None:
    result = subprocess.run(
        ["pg_ctlcluster", "16", "main", "status"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return
    info("restore/postgres: starting PostgreSQL")
    result = subprocess.run(
        ["pg_ctlcluster", "16", "main", "start"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RestoreError(f"failed to start PostgreSQL: {result.stderr.strip()}")


def _psql_file(sql_path: Path) -> None:
    result = subprocess.run(
        ["psql", "-U", "postgres", "-f", str(sql_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RestoreError(f"psql failed on {sql_path.name}: {result.stderr.strip()}")


def _psql_cmd(sql: str) -> None:
    result = subprocess.run(
        ["psql", "-U", "postgres", "-c", sql],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RestoreError(f"psql command failed: {result.stderr.strip()}")


def _restore_db(db_name: str, dump_path: Path) -> None:
    # Create database if it doesn't exist
    result = subprocess.run(
        ["psql", "-U", "postgres", "-lqt"],
        capture_output=True, text=True,
    )
    existing_dbs = {
        line.split("|")[0].strip()
        for line in result.stdout.splitlines()
        if "|" in line
    }

    if db_name not in existing_dbs:
        info(f"restore/postgres: creating database {db_name!r}")
        result = subprocess.run(
            ["createdb", "-U", "postgres", db_name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            warn(f"restore/postgres: createdb failed for {db_name!r}: {result.stderr.strip()}")
            return
    else:
        info(f"restore/postgres: database {db_name!r} exists — restoring into it")

    info(f"restore/postgres: pg_restore {db_name!r}")
    result = subprocess.run(
        [
            "pg_restore",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "-U", "postgres",
            "-d", db_name,
            str(dump_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # pg_restore exits non-zero on warnings too; only warn unless stderr is substantial
        warn(f"restore/postgres: pg_restore {db_name!r} warnings: {result.stderr[:200]}")


def _pg_ident(name: str) -> str:
    """Quote a PostgreSQL identifier."""
    return '"' + name.replace('"', '""') + '"'
