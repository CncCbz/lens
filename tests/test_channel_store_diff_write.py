from __future__ import annotations

import pytest

from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.models import (
    ChannelProxyMode,
    ProtocolKind,
    SiteBaseUrlInput,
    SiteCreate,
    SiteCredentialInput,
    SiteModelInput,
    SiteProtocolConfigInput,
    SiteUpdate,
)
from lens_api.persistence.channel_store.store import ChannelStore
from lens_api.persistence.entities import SiteDiscoveredModelEntity
from sqlalchemy import select


@pytest.mark.asyncio
async def test_update_site_preserves_model_ids(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'sites.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store = ChannelStore(create_session_factory(engine))

    created = await store.create_site(
        SiteCreate(
            name="Alpha",
            base_urls=[
                SiteBaseUrlInput(
                    id="base-1",
                    url="https://example.com",
                    name="main",
                    enabled=True,
                    supported_protocols=[ProtocolKind.OPENAI_CHAT],
                )
            ],
            credentials=[
                SiteCredentialInput(
                    id="cred-1",
                    name="key",
                    api_key="sk-test",
                    enabled=True,
                )
            ],
            protocols=[
                SiteProtocolConfigInput(
                    id="pcfg-1",
                    name="combo",
                    protocols=[ProtocolKind.OPENAI_CHAT],
                    credential_id="cred-1",
                    enabled=True,
                    headers={},
                    proxy_mode=ChannelProxyMode.INHERIT,
                    channel_proxy="",
                    param_override="",
                    match_regex="",
                    base_url_id="base-1",
                    models=[
                        SiteModelInput(
                            id="model-1",
                            credential_id="cred-1",
                            model_name="gpt-test",
                            enabled=True,
                            protocol=ProtocolKind.OPENAI_CHAT,
                        )
                    ],
                )
            ],
        )
    )
    assert created.protocols[0].models[0].id == "model-1"

    updated = await store.update_site(
        created.id,
        SiteUpdate(
            name="Alpha",
            base_urls=[
                SiteBaseUrlInput(
                    id="base-1",
                    url="https://example.com",
                    name="main",
                    enabled=True,
                    supported_protocols=[ProtocolKind.OPENAI_CHAT],
                )
            ],
            credentials=[
                SiteCredentialInput(
                    id="cred-1",
                    name="key",
                    api_key="sk-test",
                    enabled=True,
                )
            ],
            protocols=[
                SiteProtocolConfigInput(
                    id="pcfg-1",
                    name="combo",
                    protocols=[ProtocolKind.OPENAI_CHAT],
                    credential_id="cred-1",
                    enabled=True,
                    headers={"x": "1"},
                    proxy_mode=ChannelProxyMode.INHERIT,
                    channel_proxy="",
                    param_override="",
                    match_regex="",
                    base_url_id="base-1",
                    models=[
                        SiteModelInput(
                            id="model-1",
                            credential_id="cred-1",
                            model_name="gpt-test",
                            enabled=True,
                            protocol=ProtocolKind.OPENAI_CHAT,
                        )
                    ],
                )
            ],
        ),
    )
    assert updated.protocols[0].models[0].id == "model-1"
    assert updated.protocols[0].headers == {"x": "1"}

    async with create_session_factory(engine)() as session:
        rows = (
            (await session.execute(select(SiteDiscoveredModelEntity.id)))
            .scalars()
            .all()
        )
        assert rows == ["model-1"]

    await engine.dispose()
