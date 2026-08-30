from __future__ import annotations

import pytest

from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.models import GatewayApiKeyCreate, RequestLogLifecycleStatus
from lens_api.persistence.entities import SiteProtocolConfigEntity
from lens_api.persistence.repositories import (
    GatewayApiKeyRepository,
    RequestLogStore,
    SettingsRepository,
)

pytestmark = pytest.mark.postgres


async def test_request_log_increments_protocol_usage(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    settings_repo = SettingsRepository(session_factory)
    gateway_key_repo = GatewayApiKeyRepository(session_factory)
    request_log_store = RequestLogStore(
        session_factory,
        settings_repo=settings_repo,
        gateway_key_repo=gateway_key_repo,
    )
    async with session_factory() as session:
        session.add(
            SiteProtocolConfigEntity(
                id="pcfg-1",
                site_id="site-1",
                name="combo",
                protocols_json='["openai_chat"]',
                credential_id="cred-1",
                enabled=1,
                headers_json="{}",
                proxy_mode="inherit",
                channel_proxy="",
                concurrency_limit=0,
                rpm_limit=0,
                token_limit=0,
                cost_limit_usd=0.0,
                spent_tokens=0,
                spent_cost_usd=0.0,
                param_override="",
                match_regex="",
                router_error_policy_config="",
                base_url_id="base-1",
            )
        )
        await session.commit()

    key = await gateway_key_repo.create_gateway_api_key(
        GatewayApiKeyCreate(
            remark="Key",
            enabled=True,
            allowed_models=[],
            max_cost_usd=0,
            expires_at=None,
        )
    )
    await request_log_store.create_request_log(
        protocol="openai_chat",
        user_agent="",
        requested_group_name="gpt-test",
        resolved_group_name="gpt-test",
        upstream_model_name="gpt-test",
        channel_id="pcfg-1_openai_chat",
        channel_name="Channel",
        gateway_key_id=key.id,
        status_code=200,
        success=True,
        lifecycle_status=RequestLogLifecycleStatus.SUCCEEDED,
        is_stream=False,
        first_token_latency_ms=0,
        latency_ms=10,
        input_tokens=3,
        output_tokens=5,
        total_tokens=8,
        input_cost_usd=0.1,
        output_cost_usd=0.2,
        total_cost_usd=0.3,
    )

    async with session_factory() as session:
        entity = await session.get(SiteProtocolConfigEntity, "pcfg-1")
        assert entity is not None
        assert entity.spent_tokens == 8
        assert entity.spent_cost_usd == pytest.approx(0.3)

    await engine.dispose()
