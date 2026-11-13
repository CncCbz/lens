from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lens_api.core.db import create_engine
from lens_api.persistence.entities import MultimodalRelayCacheEntity
from lens_api.persistence.repositories import multimodal_relay_cache_repository
from lens_api.persistence.repositories.multimodal_relay_cache_repository import (
    MultimodalRelayCacheRepository,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
RESOURCE_HASH = sha256(b"multimodal-resource").hexdigest()
OTHER_RESOURCE_HASH = sha256(b"other-resource").hexdigest()
NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    clock = Mock(wraps=datetime)
    clock.now.return_value = NOW
    monkeypatch.setattr(multimodal_relay_cache_repository, "datetime", clock)
    return clock


@pytest_asyncio.fixture
async def session_factory(
    postgres_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(postgres_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.run_sync(
                    lambda conn: MultimodalRelayCacheEntity.__table__.create(
                        conn, checkfirst=True
                    )
                )
                yield async_sessionmaker(
                    connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_get_isolates_resource_kind_and_group(
    session_factory: async_sessionmaker[AsyncSession], clock: Mock
) -> None:
    repository = MultimodalRelayCacheRepository(session_factory)
    assert await repository.get(RESOURCE_HASH, "image", "helper-a") is None
    await repository.put(RESOURCE_HASH, "image", "helper-a", "image result")
    await repository.put(RESOURCE_HASH, "audio", "helper-a", "audio result")
    await repository.put(RESOURCE_HASH, "image", "helper-b", "other helper")
    await repository.put(OTHER_RESOURCE_HASH, "image", "helper-a", "other resource")

    assert await repository.get(RESOURCE_HASH, "image", "helper-a") == "image result"
    assert await repository.get(RESOURCE_HASH, "audio", "helper-a") == "audio result"
    assert await repository.get(RESOURCE_HASH, "image", "helper-b") == "other helper"
    assert (
        await repository.get(OTHER_RESOURCE_HASH, "image", "helper-a")
        == "other resource"
    )
    assert await repository.get(RESOURCE_HASH, "video", "helper-a") is None
    assert await repository.get(RESOURCE_HASH, "image", "helper-c") is None


async def test_get_does_not_extend_seven_day_expiry(
    session_factory: async_sessionmaker[AsyncSession], clock: Mock
) -> None:
    repository = MultimodalRelayCacheRepository(session_factory)
    await repository.put(RESOURCE_HASH, "image", "helper-a", "  result  ")
    clock.now.return_value = NOW + timedelta(days=7) - timedelta(microseconds=1)
    assert await repository.get(RESOURCE_HASH, "image", "helper-a") == "  result  "

    async with session_factory() as session:
        row = await session.get(
            MultimodalRelayCacheEntity, (RESOURCE_HASH, "image", "helper-a")
        )
        assert row is not None
        assert row.created_at == NOW.replace(tzinfo=None)
        assert row.expires_at == (NOW + timedelta(days=7)).replace(tzinfo=None)
        assert row.created_at.tzinfo is None
        assert row.expires_at.tzinfo is None

    clock.now.return_value = NOW + timedelta(days=7)
    assert await repository.get(RESOURCE_HASH, "image", "helper-a") is None


async def test_expired_rows_are_ignored_and_pruned(
    session_factory: async_sessionmaker[AsyncSession], clock: Mock
) -> None:
    repository = MultimodalRelayCacheRepository(session_factory)
    now = NOW.replace(tzinfo=None)
    async with session_factory() as session:
        for group_id, expiry in (
            ("past", now - timedelta(microseconds=1)),
            ("boundary", now),
            ("valid", now + timedelta(microseconds=1)),
        ):
            session.add(
                MultimodalRelayCacheEntity(
                    resource_hash=RESOURCE_HASH,
                    media_kind="image",
                    helper_group_id=group_id,
                    result_text=group_id,
                    created_at=now - timedelta(days=7),
                    expires_at=expiry,
                )
            )
        await session.commit()

    assert await repository.get(RESOURCE_HASH, "image", "past") is None
    assert await repository.get(RESOURCE_HASH, "image", "boundary") is None
    assert await repository.get(RESOURCE_HASH, "image", "valid") == "valid"
    async with session_factory() as session:
        assert (
            await session.get(
                MultimodalRelayCacheEntity, (RESOURCE_HASH, "image", "past")
            )
            is not None
        )

    await repository.prune_expired()
    await repository.prune_expired()
    async with session_factory() as session:
        groups = (
            await session.scalars(
                select(MultimodalRelayCacheEntity.helper_group_id).where(
                    MultimodalRelayCacheEntity.resource_hash == RESOURCE_HASH
                )
            )
        ).all()
        assert groups == ["valid"]


@pytest.mark.parametrize("elapsed_days", [1, 7, 8])
async def test_upsert_only_replaces_expired_rows(
    session_factory: async_sessionmaker[AsyncSession], clock: Mock, elapsed_days: int
) -> None:
    repository = MultimodalRelayCacheRepository(session_factory)
    await repository.put(RESOURCE_HASH, "image", "helper-a", "old result")
    clock.now.return_value = NOW + timedelta(days=elapsed_days)
    expired = elapsed_days >= 7
    if expired:
        assert await repository.get(RESOURCE_HASH, "image", "helper-a") is None
    await repository.put(RESOURCE_HASH, "image", "helper-a", "new result")

    assert await repository.get(RESOURCE_HASH, "image", "helper-a") == (
        "new result" if expired else "old result"
    )
    written_at = clock.now.return_value if expired else NOW
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(MultimodalRelayCacheEntity).where(
                    MultimodalRelayCacheEntity.resource_hash == RESOURCE_HASH
                )
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].created_at == written_at.replace(tzinfo=None)
        assert rows[0].expires_at == (written_at + timedelta(days=7)).replace(
            tzinfo=None
        )


async def test_concurrent_misses_keep_first_success_and_expiry(
    postgres_url: str, clock: Mock
) -> None:
    engine = create_engine(postgres_url)
    resource_hash = sha256(uuid4().bytes).hexdigest()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: MultimodalRelayCacheEntity.__table__.create(
                    conn, checkfirst=True
                )
            )
        async with (
            engine.connect() as first_connection,
            engine.connect() as second_connection,
        ):
            first_pid = await first_connection.scalar(text("select pg_backend_pid()"))
            second_pid = await second_connection.scalar(text("select pg_backend_pid()"))
            assert first_pid != second_pid
            await first_connection.rollback()
            await second_connection.rollback()
            first = MultimodalRelayCacheRepository(
                async_sessionmaker(first_connection, expire_on_commit=False)
            )
            second = MultimodalRelayCacheRepository(
                async_sessionmaker(second_connection, expire_on_commit=False)
            )
            assert await asyncio.gather(
                first.get(resource_hash, "image", "concurrent"),
                second.get(resource_hash, "image", "concurrent"),
            ) == [None, None]
            await first.put(resource_hash, "image", "concurrent", "first success")
            clock.now.return_value = NOW + timedelta(hours=1)
            await second.put(resource_hash, "image", "concurrent", "late success")
            assert (
                await second.get(resource_hash, "image", "concurrent")
                == "first success"
            )
            async with async_sessionmaker(second_connection)() as session:
                row = await session.get(
                    MultimodalRelayCacheEntity, (resource_hash, "image", "concurrent")
                )
                assert row is not None
                assert row.created_at == NOW.replace(tzinfo=None)
                assert row.expires_at == (NOW + timedelta(days=7)).replace(tzinfo=None)
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(MultimodalRelayCacheEntity).where(
                        MultimodalRelayCacheEntity.resource_hash == resource_hash
                    )
                )
        finally:
            await engine.dispose()


@pytest.mark.parametrize("text", ["", " \t\n"])
async def test_blank_put_does_not_insert_or_overwrite(
    session_factory: async_sessionmaker[AsyncSession], clock: Mock, text: str
) -> None:
    repository = MultimodalRelayCacheRepository(session_factory)
    await repository.put(RESOURCE_HASH, "image", "helper-a", text)
    async with session_factory() as session:
        assert (
            await session.get(
                MultimodalRelayCacheEntity, (RESOURCE_HASH, "image", "helper-a")
            )
            is None
        )

    await repository.put(RESOURCE_HASH, "image", "helper-a", "success")
    clock.now.return_value = NOW + timedelta(days=1)
    await repository.put(RESOURCE_HASH, "image", "helper-a", text)
    assert await repository.get(RESOURCE_HASH, "image", "helper-a") == "success"
    async with session_factory() as session:
        row = await session.get(
            MultimodalRelayCacheEntity, (RESOURCE_HASH, "image", "helper-a")
        )
        assert row is not None
        assert row.created_at == NOW.replace(tzinfo=None)
        assert row.expires_at == (NOW + timedelta(days=7)).replace(tzinfo=None)
