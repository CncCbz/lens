from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.models import RequestLogLifecycleStatus
from lens_api.persistence.entities import (
    RequestLogEntity,
    SiteEntity,
    SiteProtocolConfigEntity,
)
from lens_api.persistence.repositories.request_log_reads_mixin import (
    RequestLogReadMixin,
)


class _Store(RequestLogReadMixin):
    def _runtime_time_zone(self, runtime):  # noqa: ANN001
        return UTC


@pytest.mark.asyncio
async def test_runtime_summaries_use_protocol_config_id(tmp_path) -> None:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    store = _Store(session_factory)

    async with session_factory() as session:
        session.add(SiteEntity(id="site-1", name="Alpha"))
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
                param_override="",
                match_regex="",
                base_url_id="base-1",
            )
        )
        session.add(
            RequestLogEntity(
                request_id="r1",
                protocol="openai_chat",
                channel_id="pcfg-1_openai_chat",
                protocol_config_id="pcfg-1",
                channel_name="Alpha",
                success=1,
                lifecycle_status=RequestLogLifecycleStatus.SUCCEEDED.value,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()

    summaries = await store.list_site_runtime_summaries()
    assert len(summaries) == 1
    assert summaries[0].site_id == "site-1"
    assert summaries[0].recent_request_count == 1
    assert summaries[0].latest_channel_id == "pcfg-1_openai_chat"
    assert summaries[0].channel_summaries[0].channel_id == "pcfg-1_openai_chat"

    await engine.dispose()
