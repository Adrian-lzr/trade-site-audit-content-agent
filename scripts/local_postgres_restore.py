"""Rehearse a PostgreSQL backup restore into an empty local restore database.

The source and target URLs must point to localhost. The target database must
have a ``_restore`` suffix and contain no public tables. A failed restore uses
psql's single-transaction mode so it does not leave a partially restored
schema. The script never drops databases, tables, or volumes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url


ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
COUNT_TABLES = ("workspaces", "sites", "page_snapshots", "visibility_runs")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database-url", required=True, help="local PostgreSQL source DSN")
    parser.add_argument("--target-database-url", required=True, help="empty local PostgreSQL DB ending in _restore")
    parser.add_argument("--backup", required=True, type=Path, help="new path for the plain SQL dump")
    parser.add_argument("--pg-dump", default=os.getenv("PG_DUMP_BIN", "pg_dump"), help="pg_dump executable")
    parser.add_argument("--psql", default=os.getenv("PSQL_BIN", "psql"), help="psql executable")
    parser.add_argument("--source-container", help="optional Docker container that provides pg_dump")
    parser.add_argument("--target-container", help="optional Docker container that provides psql")
    return parser.parse_args()


def _parse_local_dsn(value: str, label: str) -> URL:
    try:
        url = make_url(value)
    except Exception as exc:
        raise ValueError(f"{label} must be a valid SQLAlchemy PostgreSQL DSN") from exc
    if url.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise ValueError(f"{label} must use PostgreSQL")
    host = (url.host or "").lower()
    if host not in LOCAL_HOSTS:
        raise ValueError(f"{label} must use localhost, 127.0.0.1, or ::1")
    if not url.database or not url.username:
        raise ValueError(f"{label} must specify a database and username")
    if url.query:
        raise ValueError(f"{label} query parameters are not supported by this local rehearsal")
    return url


def validate_targets(source_value: str, target_value: str) -> tuple[URL, URL]:
    source = _parse_local_dsn(source_value, "source database URL")
    target = _parse_local_dsn(target_value, "target database URL")
    if source.database == target.database and source.host == target.host and source.port == target.port:
        raise ValueError("source and target must be different databases")
    if not re.fullmatch(r"[A-Za-z0-9_]+_restore", target.database or ""):
        raise ValueError("target database name must end in _restore")
    return source, target


def _tool_args(tool: str, url: URL) -> tuple[list[str], dict[str, str]]:
    command = [tool, "--host", url.host or "localhost", "--port", str(url.port or 5432), "--username", url.username or "", "--dbname", url.database or ""]
    child_env = os.environ.copy()
    child_env.pop("PGPASSWORD", None)
    if url.password is not None:
        child_env["PGPASSWORD"] = url.password
    return command, child_env


def _container_name(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"{label} must be a simple Docker container name")
    return value


def _docker_tool_args(container: str, tool: str, url: URL, *, interactive: bool) -> tuple[list[str], dict[str, str]]:
    command = ["docker", "exec"]
    if interactive:
        command.append("-i")
    command.extend([container, tool, "--username", url.username or "", "--dbname", url.database or ""])
    return command, os.environ.copy()


def _assert_ephemeral_container(container: str) -> None:
    """Reject Docker data volumes when container tooling is explicitly used."""

    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{json .}}", container],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if inspected.returncode:
        raise RuntimeError(f"could not inspect Docker container {container!r}: {(inspected.stderr or '').strip()[:300]}")
    try:
        metadata = json.loads(inspected.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError(f"Docker inspect returned invalid mount metadata for {container!r}") from exc
    if not isinstance(metadata, dict):
        raise RuntimeError(f"Docker inspect returned invalid mount metadata for {container!r}")
    mounts = metadata.get("Mounts") or []
    host_config = metadata.get("HostConfig") or {}
    tmpfs = host_config.get("Tmpfs") or {}
    if not isinstance(mounts, list) or not isinstance(tmpfs, dict) or "/var/lib/postgresql/data" not in tmpfs:
        raise RuntimeError(
            f"Docker container {container!r} must use a tmpfs PostgreSQL data mount; "
            "refusing containers without the explicit ephemeral data boundary"
        )
    persistent = [
        mount
        for mount in mounts
        if not isinstance(mount, dict)
        or mount.get("Type") != "tmpfs"
        or mount.get("Destination") != "/var/lib/postgresql/data"
    ]
    if persistent:
        raise RuntimeError(
            f"Docker container {container!r} must use only a tmpfs PostgreSQL data mount; "
            "refusing named or anonymous volumes"
        )


def _connect_url(url: URL) -> URL:
    driver = "postgresql+psycopg" if url.drivername.startswith("postgresql") else url.drivername
    return url.set(drivername=driver)


def _state(database_url: URL) -> dict[str, Any]:
    engine = create_engine(_connect_url(database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            migration = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            inspector = inspect(connection)
            tables = set(inspector.get_table_names(schema="public"))
            counts: dict[str, int] = {}
            for table in COUNT_TABLES:
                if table in tables:
                    counts[table] = int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
            return {"migration": migration, "tables": sorted(tables), "row_counts": counts}
    finally:
        engine.dispose()


def _target_tables(database_url: URL) -> list[str]:
    engine = create_engine(_connect_url(database_url), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return inspect(connection).get_table_names(schema="public")
    finally:
        engine.dispose()


def _run(command: list[str], *, env: dict[str, str], stdout=None, stdin=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=stdout is None and stdin is None,
        check=False,
    )


def run_rehearsal(
    source_value: str,
    target_value: str,
    backup_path: Path,
    *,
    pg_dump: str = "pg_dump",
    psql: str = "psql",
    source_container: str | None = None,
    target_container: str | None = None,
) -> dict[str, Any]:
    source, target = validate_targets(source_value, target_value)
    source_container = _container_name(source_container, "source container")
    target_container = _container_name(target_container, "target container")
    if (source_container is None) != (target_container is None):
        raise ValueError("source and target Docker containers must be supplied together")
    if source_container and target_container:
        _assert_ephemeral_container(source_container)
        _assert_ephemeral_container(target_container)
    backup_path = backup_path.expanduser().resolve()
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise ValueError(f"backup path already exists; refusing to overwrite: {backup_path}")

    try:
        if source_container and target_container:
            pg_dump_bin = psql_bin = None
        else:
            pg_dump_bin = shutil.which(pg_dump) or (pg_dump if Path(pg_dump).is_file() else None)
            psql_bin = shutil.which(psql) or (psql if Path(psql).is_file() else None)
            if not pg_dump_bin or not psql_bin:
                raise RuntimeError("pg_dump and psql must be installed or supplied with --pg-dump/--psql")

        source_state = _state(source)
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
        expected_head = ScriptDirectory.from_config(config).get_current_head()
        if source_state["migration"] != expected_head:
            raise RuntimeError(f"source migration head {source_state['migration']!r} does not match repository head {expected_head!r}")
        missing_tables = sorted(set(COUNT_TABLES) - set(source_state["tables"]))
        if missing_tables:
            raise RuntimeError(f"source database is missing representative tables: {', '.join(missing_tables)}")

        target_tables = _target_tables(target)
        if target_tables:
            raise RuntimeError(f"target database is not empty; public tables found: {', '.join(sorted(target_tables))}")

        if source_container and target_container:
            dump_command, dump_env = _docker_tool_args(source_container, "pg_dump", source, interactive=False)
        else:
            assert pg_dump_bin is not None
            dump_command, dump_env = _tool_args(pg_dump_bin, source)
        dump_command.extend(["--format=plain", "--no-owner", "--no-acl"])
        with backup_path.open("xb") as backup_file:
            dumped = _run(dump_command, env=dump_env, stdout=backup_file)
        if dumped.returncode != 0:
            raise RuntimeError(f"pg_dump failed ({dumped.returncode}): {(dumped.stderr or '').strip()[:500]}")
        if backup_path.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty backup")

        if source_container and target_container:
            restore_command, restore_env = _docker_tool_args(target_container, "psql", target, interactive=True)
        else:
            assert psql_bin is not None
            restore_command, restore_env = _tool_args(psql_bin, target)
        restore_command.extend(["--single-transaction", "-v", "ON_ERROR_STOP=1", "-f", str(backup_path)])
        if source_container and target_container:
            # Feed the dump through stdin so the target container never needs
            # a host path mounted into it.
            with backup_path.open("rb") as backup_file:
                restore_command = [part for part in restore_command if part not in {"-f", str(backup_path)}]
                restored = _run(restore_command, env=restore_env, stdin=backup_file)
        else:
            restored = _run(restore_command, env=restore_env)
        if restored.returncode != 0:
            raise RuntimeError(f"psql restore failed ({restored.returncode}): {(restored.stderr or '').strip()[:500]}")

        restored_state = _state(target)
        if restored_state["migration"] != expected_head:
            raise RuntimeError(f"restored migration head mismatch: {restored_state['migration']!r}")
        if restored_state["row_counts"] != source_state["row_counts"]:
            raise RuntimeError(
                "representative table row counts differ: "
                f"source={source_state['row_counts']!r}, target={restored_state['row_counts']!r}"
            )

        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        return {
            "status": "passed",
            "source_database": source.database,
            "target_database": target.database,
            "migration_head": expected_head,
            "row_counts": source_state["row_counts"],
            "backup_path": str(backup_path),
            "backup_bytes": backup_path.stat().st_size,
            "backup_sha256": digest,
            "target_was_empty": True,
            "restore_used_single_transaction": True,
            "database_drop_or_volume_removal": False,
            "container_tools": bool(source_container and target_container),
            "container_data_mount_policy": "tmpfs_only" if source_container and target_container else None,
        }
    except Exception:
        # Keep a complete dump after restore failure for an operator to inspect;
        # remove only a zero-byte artifact from an interrupted dump attempt.
        if backup_path.exists() and backup_path.stat().st_size == 0:
            backup_path.unlink()
        raise


def main() -> int:
    args = _parse_args()
    try:
        report = run_rehearsal(
            args.source_database_url,
            args.target_database_url,
            args.backup,
            pg_dump=args.pg_dump,
            psql=args.psql,
            source_container=args.source_container,
            target_container=args.target_container,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
