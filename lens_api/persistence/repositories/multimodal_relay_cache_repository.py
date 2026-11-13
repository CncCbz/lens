from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..entities import MultimodalRelayCacheEntity


class MultimodalRelayCacheRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(
        self, resource_hash: str, kind: str, helper_group_id: str
    ) -> str | None:
        now = datetime.now(UTC).replace(tzinfo=None)
        statement = select(MultimodalRelayCacheEntity.result_text).where(
            MultimodalRelayCacheEntity.resource_hash == resource_hash,
            MultimodalRelayCacheEntity.media_kind == kind,
            MultimodalRelayCacheEntity.helper_group_id == helper_group_id,
            MultimodalRelayCacheEntity.expires_at > now,
        )
        async with self._session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none()

    async def put(
        self, resource_hash: str, kind: str, helper_group_id: str, text: str
    ) -> None:
        if not text.strip():
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        statement = insert(MultimodalRelayCacheEntity).values(
            resource_hash=resource_hash,
            media_kind=kind,
            helper_group_id=helper_group_id,
            result_text=text,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[
                MultimodalRelayCacheEntity.resource_hash,
                MultimodalRelayCacheEntity.media_kind,
                MultimodalRelayCacheEntity.helper_group_id,
            ],
            set_={
                "result_text": statement.excluded.result_text,
                "created_at": statement.excluded.created_at,
                "expires_at": statement.excluded.expires_at,
            },
            where=MultimodalRelayCacheEntity.expires_at <= now,
        )
        async with self._session_factory() as session:
            await session.execute(statement)
            await session.commit()

    async def prune_expired(self) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with self._session_factory() as session:
            await session.execute(
                delete(MultimodalRelayCacheEntity).where(
                    MultimodalRelayCacheEntity.expires_at <= now
                )
            )
            await session.commit()
