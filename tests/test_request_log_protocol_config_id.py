from __future__ import annotations

import pytest

from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.core.runtime_channel_ids import protocol_config_id_from_runtime_channel_id
from lens_api.models import RequestLogLifecycleStatus
from lens_api.persistence.entities import RequestLogEntity
from lens_api.persistence.repositories.request_log_writes_mixin import (
    _protocol_config_id_for_channel,
)


def test_protocol_config_id_parser() -> None:
    assert _protocol_config_id_for_channel("abc-123_openai_responses") == "abc-123"
    assert _protocol_config_id_for_channel("preview") is None
    assert _protocol_config_id_for_channel(None) is None


@pytest.mark.asyncio
async def test_request_log_entity_stores_protocol_config_id(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'logs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)

    channel_id = "pcfg-1_openai_chat"
    async with session_factory() as session:
        entity = RequestLogEntity(
            request_id="rid",
            protocol="openai_chat",
            channel_id=channel_id,
            protocol_config_id=_protocol_config_id_for_channel(channel_id),
            lifecycle_status=RequestLogLifecycleStatus.SUCCEEDED.value,
            success=1,
        )
        session.add(entity)
        await session.commit()
        await session.refresh(entity)
        assert entity.protocol_config_id == "pcfg-1"
        assert (
            protocol_config_id_from_runtime_channel_id(channel_id)
            == entity.protocol_config_id
        )

    await engine.dispose()
