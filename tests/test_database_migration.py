from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from lens_api.core.db import Base, normalize_sync_database_url

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "migrate_sqlite_to_postgresql.py"
)
_SPEC = importlib.util.spec_from_file_location("migrate_sqlite_to_postgresql", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_migration)
MigrationError = _migration.MigrationError
_project_head = _migration._project_head
migrate_databases = _migration.migrate_databases


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _prepare_sqlite(path: Path) -> str:
    url = _sqlite_url(path)
    engine = create_engine(url)
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": _project_head()},
        )
        conn.execute(
            text("INSERT INTO sites (id, name) VALUES ('site-1', 'Demo Site')")
        )
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('site_name', 'Demo')")
        )
    engine.dispose()
    return url


def test_migration_rejects_non_sqlite_source() -> None:
    with pytest.raises(MigrationError, match="Expected sqlite"):
        migrate_databases(
            "postgresql+psycopg://lens:x@localhost/lens",
            "postgresql+psycopg://lens:x@localhost/lens",
        )


def test_migration_rejects_non_postgres_target(tmp_path: Path) -> None:
    source = _prepare_sqlite(tmp_path / "source.db")
    with pytest.raises(MigrationError, match="Expected postgresql"):
        migrate_databases(source, source)


@pytest.mark.postgres
def test_migration_copy_and_rerun_after_seed(tmp_path: Path) -> None:
    target_url = os.environ["LENS_TEST_POSTGRES_URL"].strip()
    source = _prepare_sqlite(tmp_path / "source.db")
    target_sync = normalize_sync_database_url(target_url)

    target_engine = create_engine(target_sync, pool_pre_ping=True)
    with target_engine.begin() as conn:
        Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": _project_head()},
        )
        # Simulate Alembic seed rows that a fresh upgrade inserts.
        conn.execute(
            text(
                "INSERT INTO cronjobs "
                "(id, enabled, schedule_type, interval_hours, weekdays_json, "
                "status, last_error, lease_owner, created_at, updated_at) "
                "VALUES ('seed', 1, 'interval', 1, '[]', 'idle', '', '', NOW(), NOW())"
            )
        )

    result = migrate_databases(source, target_url, batch_size=50)
    assert result["sites"][0] == 1
    assert result["settings"][0] == 1
    # Re-run should truncate seed/target data and copy again successfully.
    result_again = migrate_databases(source, target_url, batch_size=50)
    assert result_again["sites"][0] == 1

    with target_engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            conn.execute(text(f'DELETE FROM "{table.name}"'))
    target_engine.dispose()
