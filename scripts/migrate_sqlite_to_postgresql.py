from __future__ import annotations

import argparse
import base64
from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, func, insert, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from lens_api.core.db import normalize_sync_database_url
from lens_api.persistence.entities import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUSINESS_TABLES = tuple(Base.metadata.sorted_tables)
SEQUENCE_TABLES = ("admin_users", "model_group_items", "request_logs")
STRICT_RELATIONSHIPS = (
    ("site_base_urls", "site_id", "sites", "id"),
    ("site_credentials", "site_id", "sites", "id"),
    ("site_protocol_configs", "site_id", "sites", "id"),
    ("site_protocol_configs", "base_url_id", "site_base_urls", "id"),
    ("site_protocol_configs", "credential_id", "site_credentials", "id"),
    (
        "site_discovered_models",
        "protocol_config_id",
        "site_protocol_configs",
        "id",
    ),
    ("site_discovered_models", "credential_id", "site_credentials", "id"),
    ("model_group_items", "group_id", "model_groups", "id"),
    ("model_group_items", "credential_id", "site_credentials", "id"),
)


class MigrationError(RuntimeError):
    pass


def _project_head() -> str:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise MigrationError(f"Expected one Alembic head, found {len(heads)}")
    return heads[0]


def _database_revision(connection: Connection, label: str) -> str:
    try:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    except SQLAlchemyError as exc:
        raise MigrationError(f"{label} database has no Alembic schema") from exc
    if len(rows) != 1 or not rows[0][0]:
        raise MigrationError(f"{label} database must contain exactly one revision")
    return str(rows[0][0])


def _sync_url(raw_url: str, *, expected_backend: str) -> str:
    if not raw_url.strip():
        raise MigrationError(f"Missing {expected_backend} database URL")
    normalized = normalize_sync_database_url(raw_url)
    try:
        backend = make_url(normalized).get_backend_name()
    except Exception as exc:
        raise MigrationError(f"Invalid {expected_backend} database URL") from exc
    if backend != expected_backend:
        raise MigrationError(
            f"Expected {expected_backend} database URL, received {backend or 'unknown'}"
        )
    return normalized


def _source_engine(raw_url: str) -> Engine:
    engine = create_engine(
        _sync_url(raw_url, expected_backend="sqlite"),
        connect_args={"timeout": 30},
    )
    return engine


def _target_engine(raw_url: str) -> Engine:
    return create_engine(
        _sync_url(raw_url, expected_backend="postgresql"),
        pool_pre_ping=True,
    )


def _set_source_read_only(connection: Connection) -> None:
    connection.execute(text("PRAGMA query_only=ON"))
    if int(connection.execute(text("PRAGMA query_only")).scalar_one()) != 1:
        raise MigrationError("Could not open source SQLite in query-only mode")


def _validate_revisions(source: Connection, target: Connection) -> str:
    head = _project_head()
    source_revision = _database_revision(source, "Source")
    target_revision = _database_revision(target, "Target")
    if source_revision != head:
        raise MigrationError(
            f"Source revision {source_revision} does not match project head {head}"
        )
    if target_revision != head:
        raise MigrationError(
            f"Target revision {target_revision} does not match project head {head}"
        )
    return head


def _table_count(connection: Connection, table: Any) -> int:
    return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def _clear_target_business_tables(connection: Connection) -> None:
    """Drop Alembic seed rows (cronjobs / default API keys) before copy.

    Fresh `lens db upgrade` intentionally inserts built-in cron jobs and may seed
    a default gateway key. Those must be replaced by the SQLite source data.
    alembic_version is left untouched so both DBs stay on the same head.
    """
    table_names = ", ".join(f'"{table.name}"' for table in BUSINESS_TABLES)
    connection.execute(
        text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
    )


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return {"float": format(value, ".17g")}
    if isinstance(value, Decimal):
        return {"decimal": format(value, "f")}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat(timespec="microseconds")}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, time):
        return {"time": value.isoformat(timespec="microseconds")}
    if isinstance(value, uuid.UUID):
        return {"uuid": str(value)}
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return {"bytes": base64.b64encode(value).decode("ascii")}
    return {"text": str(value)}


def _row_bytes(table: Any, row: Any) -> bytes:
    payload = [_canonical_value(row._mapping[column.name]) for column in table.columns]
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _ordered_select(table: Any) -> Any:
    primary_key = list(table.primary_key.columns)
    statement = select(table)
    return statement.order_by(*primary_key) if primary_key else statement


def _checksum_table(
    connection: Connection, table: Any, *, batch_size: int
) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    result = connection.execution_options(stream_results=True).execute(
        _ordered_select(table)
    )
    while rows := result.fetchmany(batch_size):
        for row in rows:
            digest.update(_row_bytes(table, row))
            count += 1
    return count, digest.hexdigest()


def _copy_table(
    source: Connection,
    target: Connection,
    table: Any,
    *,
    batch_size: int,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    result = source.execution_options(stream_results=True).execute(
        _ordered_select(table)
    )
    while rows := result.fetchmany(batch_size):
        mappings: list[dict[str, Any]] = []
        for row in rows:
            digest.update(_row_bytes(table, row))
            mappings.append(dict(row._mapping))
        if mappings:
            target.execute(insert(table), mappings)
            count += len(mappings)
    return count, digest.hexdigest()


def _reset_sequences(connection: Connection) -> None:
    tables = Base.metadata.tables
    for table_name in SEQUENCE_TABLES:
        table = tables[table_name]
        sequence_name = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if not sequence_name:
            raise MigrationError(f"Missing PostgreSQL sequence for {table_name}.id")
        maximum = connection.execute(select(func.max(table.c.id))).scalar_one()
        connection.execute(
            text("SELECT setval(CAST(:sequence_name AS regclass), :value, :called)"),
            {
                "sequence_name": sequence_name,
                "value": int(maximum) if maximum is not None else 1,
                "called": maximum is not None,
            },
        )


def _strict_orphans(connection: Connection) -> list[str]:
    tables = Base.metadata.tables
    findings: list[str] = []
    for child_name, child_column, parent_name, parent_column in STRICT_RELATIONSHIPS:
        child = tables[child_name]
        parent = tables[parent_name]
        count = int(
            connection.execute(
                select(func.count())
                .select_from(
                    child.outerjoin(
                        parent,
                        child.c[child_column] == parent.c[parent_column],
                    )
                )
                .where(
                    child.c[child_column].is_not(None),
                    parent.c[parent_column].is_(None),
                )
            ).scalar_one()
        )
        if count:
            findings.append(
                f"{child_name}.{child_column}->{parent_name}.{parent_column}: {count}"
            )
    return findings


def _verify_sequences(connection: Connection) -> None:
    tables = Base.metadata.tables
    for table_name in SEQUENCE_TABLES:
        table = tables[table_name]
        sequence_name = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": table_name},
        ).scalar_one_or_none()
        if not sequence_name:
            raise MigrationError(f"Missing PostgreSQL sequence for {table_name}.id")
        maximum = connection.execute(select(func.max(table.c.id))).scalar_one()
        row = connection.execute(
            text("SELECT last_value, is_called FROM " + str(sequence_name))
        ).one()
        expected = int(maximum) if maximum is not None else 1
        if int(row.last_value) != expected or bool(row.is_called) != (maximum is not None):
            raise MigrationError(f"Unexpected sequence state for {table_name}.id")


def _verify_data(
    source: Connection,
    target: Connection,
    expected: dict[str, tuple[int, str]] | None,
    *,
    batch_size: int,
) -> dict[str, tuple[int, str]]:
    verified: dict[str, tuple[int, str]] = {}
    for table in BUSINESS_TABLES:
        source_result = (
            expected[table.name]
            if expected is not None
            else _checksum_table(source, table, batch_size=batch_size)
        )
        target_result = _checksum_table(target, table, batch_size=batch_size)
        if source_result != target_result:
            raise MigrationError(
                f"Verification failed for {table.name}: "
                f"source rows={source_result[0]}, target rows={target_result[0]}"
            )
        verified[table.name] = target_result
        print(
            f"VERIFIED {table.name}: rows={target_result[0]} "
            f"sha256={target_result[1]}"
        )

    orphans = _strict_orphans(target)
    if orphans:
        raise MigrationError("Strict orphan associations found: " + "; ".join(orphans))
    _verify_sequences(target)
    return verified


def migrate_databases(
    source_url: str,
    target_url: str,
    *,
    batch_size: int = 500,
    verify_only: bool = False,
    fail_after_table: str | None = None,
) -> dict[str, tuple[int, str]]:
    if batch_size < 1:
        raise MigrationError("batch_size must be at least 1")

    source_engine = _source_engine(source_url)
    target_engine = _target_engine(target_url)
    try:
        with source_engine.connect() as source:
            _set_source_read_only(source)
            if verify_only:
                with target_engine.connect() as target:
                    revision = _validate_revisions(source, target)
                    print(f"Alembic revision: {revision}")
                    return _verify_data(
                        source,
                        target,
                        None,
                        batch_size=batch_size,
                    )

            with target_engine.begin() as target:
                revision = _validate_revisions(source, target)
                print(f"Alembic revision: {revision}")
                _clear_target_business_tables(target)
                print("CLEARED target business tables (kept alembic_version)")
                expected: dict[str, tuple[int, str]] = {}
                for table in BUSINESS_TABLES:
                    result = _copy_table(
                        source,
                        target,
                        table,
                        batch_size=batch_size,
                    )
                    expected[table.name] = result
                    print(
                        f"COPIED {table.name}: rows={result[0]} sha256={result[1]}"
                    )
                    if fail_after_table == table.name:
                        raise MigrationError(f"Injected failure after {table.name}")
                _reset_sequences(target)
                return _verify_data(
                    source,
                    target,
                    expected,
                    batch_size=batch_size,
                )
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy an upgraded Lens SQLite database into empty PostgreSQL tables."
    )
    parser.add_argument(
        "--source-url",
        default=os.environ.get("LENS_MIGRATION_SOURCE_URL", ""),
        help="Source SQLite URL (prefer LENS_MIGRATION_SOURCE_URL).",
    )
    parser.add_argument(
        "--target-url",
        default=os.environ.get("LENS_MIGRATION_TARGET_URL")
        or os.environ.get("LENS_DATABASE_URL", ""),
        help=(
            "Target PostgreSQL URL "
            "(prefer LENS_MIGRATION_TARGET_URL, else LENS_DATABASE_URL)."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Compare an already migrated target without copying rows.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        migrate_databases(
            args.source_url,
            args.target_url,
            batch_size=args.batch_size,
            verify_only=args.verify_only,
        )
    except MigrationError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("FAILED: database operation failed; no data was committed", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAILED: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1

    print("OK: verification completed" if args.verify_only else "OK: migration committed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
