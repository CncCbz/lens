from __future__ import annotations

import pytest

from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.models import RequestLogDetail, RequestLogLifecycleStatus
from lens_api.persistence.entities import RequestLogEntity
from lens_api.persistence.repositories.request_log_channel_resolution_mixin import (
    RequestLogChannelResolutionMixin,
)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_request_log_transport_details_roundtrip(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        entity = RequestLogEntity(
            request_id="rid-transport",
            protocol="openai_chat",
            upstream_protocol="openai_responses",
            channel_id="pcfg-1_openai_responses",
            lifecycle_status=RequestLogLifecycleStatus.SUCCEEDED.value,
            success=1,
            request_content="{}",
            client_request_content='{"model":"deepseek-v4-flash","messages":[]}',
            upstream_request_content='{"model":"deepseek-v4-flash","input":[]}',
            request_headers='{"authorization":"Bearer secret"}',
            upstream_headers='{"x-api-key":"secret"}',
            upstream_response_headers='{"content-type":"text/event-stream"}',
            response_content='{"object":"chat.completion"}',
            upstream_response_content='{"type":"response.completed"}',
            upstream_response_distilled='{"object":"response"}',
            client_response_raw_content='data: {"object":"chat.completion.chunk"}',
            client_response_headers='{"content-type":"text/event-stream"}',
        )
        session.add(entity)
        await session.commit()
        await session.refresh(entity)

        assert entity.client_request_content == '{"model":"deepseek-v4-flash","messages":[]}'
        assert entity.upstream_request_content == '{"model":"deepseek-v4-flash","input":[]}'
        assert entity.upstream_response_headers == '{"content-type":"text/event-stream"}'
        assert entity.upstream_response_content == '{"type":"response.completed"}'
        assert entity.upstream_response_distilled == '{"object":"response"}'
        assert entity.client_response_raw_content == 'data: {"object":"chat.completion.chunk"}'
        assert entity.client_response_headers == '{"content-type":"text/event-stream"}'
        assert entity.upstream_protocol == "openai_responses"

        detail = RequestLogChannelResolutionMixin._to_request_log_detail(entity)
        assert isinstance(detail, RequestLogDetail)
        assert detail.client_request_content == entity.client_request_content
        assert detail.upstream_request_content == entity.upstream_request_content
        assert detail.upstream_response_headers == entity.upstream_response_headers
        assert detail.upstream_response_content == entity.upstream_response_content
        assert detail.upstream_response_distilled == entity.upstream_response_distilled
        assert detail.client_response_raw_content == entity.client_response_raw_content
        assert detail.client_response_headers == entity.client_response_headers
        assert detail.upstream_protocol is not None
        assert detail.upstream_protocol.value == "openai_responses"
        # Sensitive headers are redacted.
        assert "secret" not in (detail.request_headers or "")
        assert "secret" not in (detail.upstream_headers or "")
        assert "secret" not in (detail.upstream_response_headers or "")

    await engine.dispose()
