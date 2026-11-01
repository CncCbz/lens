from __future__ import annotations

from .shared import (
    AsyncSession,
    SiteConfig,
    SiteEntity,
    SiteProtocolConfigSummary,
    SiteSummary,
    select,
)
from ..site_loader import (
    fetch_model_stats_by_protocol_config,
    fetch_site_definition_rows,
    fetch_site_rows,
)
from ..shared import _parse_supported_protocols_json, json


class ChannelLoadersMixin:
    async def _load_sites(
        self, session: AsyncSession, site_ids: list[str] | None = None
    ) -> list[SiteConfig]:
        (
            site_rows,
            base_url_rows,
            credential_rows,
            protocol_rows,
            model_rows,
        ) = await fetch_site_rows(session, site_ids=site_ids)
        if not site_rows:
            return []

        base_urls_by_site = self._group_base_urls(base_url_rows)
        credentials_by_site, credentials_by_id = self._group_credentials(
            credential_rows
        )
        models_by_protocol_config = self._group_models(model_rows, credentials_by_id)
        protocols_by_site = self._group_protocols(
            protocol_rows, models_by_protocol_config
        )

        return [
            SiteConfig(
                id=row.id,
                name=row.name,
                base_urls=base_urls_by_site.get(row.id, []),
                credentials=credentials_by_site.get(row.id, []),
                protocols=protocols_by_site.get(row.id, []),
            )
            for row in site_rows
        ]

    async def _load_site_summaries(
        self, session: AsyncSession, site_ids: list[str] | None = None
    ) -> list[SiteSummary]:
        (
            site_rows,
            base_url_rows,
            credential_rows,
            protocol_rows,
        ) = await fetch_site_definition_rows(session, site_ids=site_ids)
        if not site_rows:
            return []

        model_stats = await fetch_model_stats_by_protocol_config(
            session, [row.id for row in protocol_rows]
        )
        base_urls_by_site = self._group_base_urls(base_url_rows)
        credentials_by_site, _ = self._group_credentials(credential_rows)
        protocols_by_site: dict[str, list[SiteProtocolConfigSummary]] = {}
        for row in protocol_rows:
            model_count, enabled_model_count, model_names = model_stats.get(
                row.id, (0, 0, [])
            )
            protocols_by_site.setdefault(row.site_id, []).append(
                SiteProtocolConfigSummary(
                    id=row.id,
                    name=row.name,
                    protocols=_parse_supported_protocols_json(row.protocols_json),
                    enabled=bool(row.enabled),
                    headers=json.loads(row.headers_json),
                    proxy_mode=row.proxy_mode,
                    channel_proxy=row.channel_proxy,
                    concurrency_limit=row.concurrency_limit,
                    rpm_limit=row.rpm_limit,
                    token_limit=row.token_limit,
                    cost_limit_usd=row.cost_limit_usd,
                    first_token_timeout_seconds=row.first_token_timeout_seconds,
                    stream_idle_timeout_seconds=row.stream_idle_timeout_seconds,
                    spent_tokens=row.spent_tokens,
                    spent_cost_usd=row.spent_cost_usd,
                    param_override=row.param_override,
                    match_regex=row.match_regex,
                    router_error_policy_config=row.router_error_policy_config,
                    base_url_id=row.base_url_id,
                    credential_id=row.credential_id,
                    model_count=model_count,
                    enabled_model_count=enabled_model_count,
                    model_names=model_names,
                )
            )

        return [
            SiteSummary(
                id=row.id,
                name=row.name,
                base_urls=base_urls_by_site.get(row.id, []),
                credentials=credentials_by_site.get(row.id, []),
                protocols=protocols_by_site.get(row.id, []),
            )
            for row in site_rows
        ]

    async def _load_sites_by_ids(self, site_ids: list[str]) -> list[SiteConfig]:
        if not site_ids:
            return []
        async with self._session_factory() as session:
            sites = await self._load_sites(session, site_ids=site_ids)
        order = {site_id: index for index, site_id in enumerate(site_ids)}
        return sorted(sites, key=lambda item: order.get(item.id, len(order)))

    async def _load_site_summaries_by_ids(
        self, site_ids: list[str]
    ) -> list[SiteSummary]:
        if not site_ids:
            return []
        async with self._session_factory() as session:
            sites = await self._load_site_summaries(session, site_ids=site_ids)
        order = {site_id: index for index, site_id in enumerate(site_ids)}
        return sorted(sites, key=lambda item: order.get(item.id, len(order)))

    async def _site_name_keys(self, session: AsyncSession) -> set[str]:
        rows = (await session.execute(select(SiteEntity.name))).scalars().all()
        return {row.strip().lower() for row in rows if row.strip()}
