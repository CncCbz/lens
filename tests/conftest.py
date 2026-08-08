from __future__ import annotations

import os

import pytest

_TEST_POSTGRES_URL = os.environ.get("LENS_TEST_POSTGRES_URL", "").strip()
os.environ.setdefault(
    "LENS_DATABASE_URL",
    _TEST_POSTGRES_URL or "postgresql+psycopg://lens:test@127.0.0.1:5432/lens_test",
)


@pytest.fixture
def postgres_url() -> str:
    return os.environ["LENS_TEST_POSTGRES_URL"].strip()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: requires LENS_TEST_POSTGRES_URL pointing at a disposable database",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("LENS_TEST_POSTGRES_URL", "").strip():
        return
    skip_postgres = pytest.mark.skip(
        reason="Set LENS_TEST_POSTGRES_URL to run PostgreSQL integration tests"
    )
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip_postgres)
