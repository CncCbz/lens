from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.runtime_channel_ids import (
    compose_runtime_channel_id,
    format_runtime_channel_name,
)
from .entities import (
    ModelGroupEntity,
    SiteBaseUrlEntity,
    SiteCredentialEntity,
    SiteDiscoveredModelEntity,
    SiteEntity,
    SiteProtocolConfigEntity,
)
from .shared import _parse_supported_protocols_json


async def fetch_site_rows(
    session: AsyncSession, site_ids: list[str] | None = None
) -> tuple[
    list[SiteEntity],
    list[SiteBaseUrlEntity],
    list[SiteCredentialEntity],
    list[SiteProtocolConfigEntity],
    list[SiteDiscoveredModelEntity],
]:
    site_rows, base_url_rows, credential_rows, protocol_rows = (
        await fetch_site_definition_rows(session, site_ids=site_ids)
    )
    if not site_rows:
        return [], [], [], [], []

    protocol_config_ids = [item.id for item in protocol_rows]
    model_rows: list[SiteDiscoveredModelEntity] = []
    if protocol_config_ids:
        model_rows = (
            (
                await session.execute(
                    select(SiteDiscoveredModelEntity)
                    .where(
                        SiteDiscoveredModelEntity.protocol_config_id.in_(
                            protocol_config_ids
                        )
                    )
                    .order_by(
                        SiteDiscoveredModelEntity.protocol_config_id.asc(),
                        SiteDiscoveredModelEntity.sort_order.asc(),
                        SiteDiscoveredModelEntity.id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

    return site_rows, base_url_rows, credential_rows, protocol_rows, model_rows


async def fetch_model_stats_by_protocol_config(
    session: AsyncSession, protocol_config_ids: list[str]
) -> dict[str, tuple[int, int, list[str]]]:
    """Aggregate discovered models instead of loading every row."""

    if not protocol_config_ids:
        return {}
    stmt = (
        select(
            SiteDiscoveredModelEntity.protocol_config_id,
            func.count().label("model_count"),
            func.coalesce(func.sum(SiteDiscoveredModelEntity.enabled), 0).label(
                "enabled_model_count"
            ),
            func.array_agg(func.distinct(SiteDiscoveredModelEntity.model_name)).label(
                "model_names"
            ),
        )
        .where(SiteDiscoveredModelEntity.protocol_config_id.in_(protocol_config_ids))
        .group_by(SiteDiscoveredModelEntity.protocol_config_id)
    )
    rows = (await session.execute(stmt)).all()
    return {
        str(row.protocol_config_id): (
            int(row.model_count),
            int(row.enabled_model_count),
            sorted(str(name) for name in (row.model_names or [])),
        )
        for row in rows
    }


async def fetch_site_definition_rows(
    session: AsyncSession, site_ids: list[str] | None = None
) -> tuple[
    list[SiteEntity],
    list[SiteBaseUrlEntity],
    list[SiteCredentialEntity],
    list[SiteProtocolConfigEntity],
]:
    """Sites, base URLs, credentials and combos — everything except models."""

    site_query = select(SiteEntity).order_by(SiteEntity.name.asc())
    if site_ids is not None:
        site_query = site_query.where(SiteEntity.id.in_(site_ids))
    site_rows = (await session.execute(site_query)).scalars().all()
    if not site_rows:
        return [], [], [], []

    ids = [item.id for item in site_rows]
    base_url_rows = (
        (
            await session.execute(
                select(SiteBaseUrlEntity)
                .where(SiteBaseUrlEntity.site_id.in_(ids))
                .order_by(
                    SiteBaseUrlEntity.site_id.asc(),
                    SiteBaseUrlEntity.sort_order.asc(),
                    SiteBaseUrlEntity.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    credential_rows = (
        (
            await session.execute(
                select(SiteCredentialEntity)
                .where(SiteCredentialEntity.site_id.in_(ids))
                .order_by(
                    SiteCredentialEntity.site_id.asc(),
                    SiteCredentialEntity.sort_order.asc(),
                    SiteCredentialEntity.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    protocol_rows = (
        (
            await session.execute(
                select(SiteProtocolConfigEntity)
                .where(SiteProtocolConfigEntity.site_id.in_(ids))
                .order_by(
                    SiteProtocolConfigEntity.site_id.asc(),
                    SiteProtocolConfigEntity.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return site_rows, base_url_rows, credential_rows, protocol_rows


async def load_channel_labels(session: AsyncSession) -> list[tuple[str, str]]:
    """(runtime channel id, display name) for every configured channel."""

    rows = (
        await session.execute(
            select(
                SiteProtocolConfigEntity.id,
                SiteProtocolConfigEntity.name,
                SiteProtocolConfigEntity.protocols_json,
                SiteEntity.id,
                SiteEntity.name,
            )
            .join(SiteEntity, SiteEntity.id == SiteProtocolConfigEntity.site_id)
            .order_by(
                SiteEntity.name.asc(),
                SiteProtocolConfigEntity.site_id.asc(),
                SiteProtocolConfigEntity.id.asc(),
            )
        )
    ).all()
    combo_counts: dict[str, int] = {}
    for *_rest, site_id, _site_name in rows:
        combo_counts[str(site_id)] = combo_counts.get(str(site_id), 0) + 1
    combo_indexes: dict[str, int] = {}
    labels: list[tuple[str, str]] = []
    for protocol_config_id, combo_name, protocols_json, site_id, site_name in rows:
        protocol_config_id = str(protocol_config_id)
        site_id = str(site_id)
        combo_index = combo_indexes.get(site_id, 0)
        combo_indexes[site_id] = combo_index + 1
        label = format_runtime_channel_name(
            str(site_name),
            str(combo_name or ""),
            multi_combo=combo_counts.get(site_id, 0) > 1,
            combo_index=combo_index,
        )
        for protocol in _parse_supported_protocols_json(protocols_json):
            labels.append(
                (compose_runtime_channel_id(protocol_config_id, protocol), label)
            )
    return labels


async def load_known_model_names(session: AsyncSession) -> list[str]:
    """Model names the gateway can route to, independent of log retention."""

    discovered = (
        (await session.execute(select(SiteDiscoveredModelEntity.model_name).distinct()))
        .scalars()
        .all()
    )
    group_names = (
        (await session.execute(select(ModelGroupEntity.name).distinct()))
        .scalars()
        .all()
    )
    return sorted(
        {
            str(name).strip()
            for name in [*discovered, *group_names]
            if name is not None and str(name).strip()
        }
    )
