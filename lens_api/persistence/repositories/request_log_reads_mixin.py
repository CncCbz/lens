from __future__ import annotations

import time

from sqlalchemy import true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from ..shared import (
    Any,
    CHANNEL_HEALTH_BUCKET_COUNT,
    CHANNEL_HEALTH_BUCKET_SECONDS,
    ProtocolKind,
    REQUEST_LOG_TERMINAL_STATUSES,
    RequestLogDetail,
    RequestLogEntity,
    RequestLogFilterOption,
    RequestLogItem,
    RequestLogPage,
    RequestLogSortMode,
    RequestLogSource,
    RequestLogStatusFilter,
    SiteChannelHealthBucket,
    SiteChannelRuntimeSummary,
    SiteEntity,
    SiteProtocolConfigEntity,
    SiteRuntimeSummary,
    UTC,
    _parse_supported_protocols_json,
    _runtime_channel_id,
    datetime,
    func,
    literal,
    select,
    timedelta,
)
from ..site_loader import load_channel_labels, load_known_model_names

# Exact totals on a filterable log table need a full scan; 100 pages of history
# is more than anyone scrolls, and the UI renders "10000+" past this cap.
REQUEST_LOG_COUNT_CAP = 10_001


class RequestLogReadMixin:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_request_log_page(
        self,
        limit: int = 100,
        days: int = 0,
        offset: int = 0,
        gateway_key_id: str | None = None,
        model_prefix: str | None = None,
        status_filter: RequestLogStatusFilter | None = None,
        protocol: ProtocolKind | None = None,
        channel: str | None = None,
        keyword: str | None = None,
        source: RequestLogSource | None = None,
        sort: RequestLogSortMode = RequestLogSortMode.LATEST,
    ) -> RequestLogPage:
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        async with self._session_factory() as session:
            items_stmt = select(RequestLogEntity).options(
                load_only(
                    RequestLogEntity.id,
                    RequestLogEntity.request_id,
                    RequestLogEntity.protocol,
                    RequestLogEntity.user_agent,
                    RequestLogEntity.requested_group_name,
                    RequestLogEntity.resolved_group_name,
                    RequestLogEntity.upstream_model_name,
                    RequestLogEntity.channel_id,
                    RequestLogEntity.channel_name,
                    RequestLogEntity.gateway_key_id,
                    RequestLogEntity.status_code,
                    RequestLogEntity.success,
                    RequestLogEntity.lifecycle_status,
                    RequestLogEntity.is_stream,
                    RequestLogEntity.first_token_latency_ms,
                    RequestLogEntity.latency_ms,
                    RequestLogEntity.input_tokens,
                    RequestLogEntity.cache_read_input_tokens,
                    RequestLogEntity.cache_write_input_tokens,
                    RequestLogEntity.output_tokens,
                    RequestLogEntity.total_tokens,
                    RequestLogEntity.input_cost_usd,
                    RequestLogEntity.output_cost_usd,
                    RequestLogEntity.total_cost_usd,
                    RequestLogEntity.attempt_count,
                    RequestLogEntity.reasoning_effort,
                    RequestLogEntity.primary_credential_id,
                    RequestLogEntity.primary_credential_name,
                    RequestLogEntity.error_message,
                    RequestLogEntity.created_at,
                )
            )
            items_stmt = self._apply_request_log_filters(
                items_stmt,
                days=days,
                time_zone=time_zone,
                gateway_key_id=gateway_key_id,
                model_prefix=model_prefix,
                status_filter=status_filter,
                protocol=protocol,
                channel=channel,
                keyword=keyword,
                source=source,
            )
            items_stmt = self._apply_request_log_sort(items_stmt, sort=sort)
            items_stmt = items_stmt.offset(max(offset, 0)).limit(max(limit, 0))
            items_result = await session.execute(items_stmt)
            entities = items_result.scalars().all()

            # The count and the filter options are the expensive part of this
            # endpoint and it is polled every 5s, so both stay off the log table:
            # the count is bounded and the options come from the config tables.
            aux_key = (
                gateway_key_id,
                model_prefix,
                status_filter,
                protocol,
                channel,
                self._normalize_request_log_keyword(keyword),
                source,
            )
            now_mono = time.monotonic()
            cached = self._log_page_aux_cache.get(aux_key)
            if cached is not None and now_mono - cached[0] < 15.0:
                (
                    total,
                    total_capped,
                    channels,
                    gateway_keys,
                    gateway_has_multiple_keys,
                    model_names,
                ) = cached[1]
            else:
                count_stmt = self._apply_request_log_filters(
                    select(RequestLogEntity.id),
                    days=days,
                    time_zone=time_zone,
                    gateway_key_id=gateway_key_id,
                    model_prefix=model_prefix,
                    status_filter=status_filter,
                    protocol=protocol,
                    channel=channel,
                    keyword=keyword,
                    source=source,
                ).limit(REQUEST_LOG_COUNT_CAP)
                total = int(
                    await session.scalar(
                        select(func.count()).select_from(count_stmt.subquery())
                    )
                    or 0
                )
                total_capped = total >= REQUEST_LOG_COUNT_CAP
                channels = await self._request_log_channel_options(session)
                (
                    gateway_keys,
                    gateway_has_multiple_keys,
                ) = await self._request_log_key_options(session)
                model_names = await load_known_model_names(session)
                self._log_page_aux_cache[aux_key] = (
                    now_mono,
                    (
                        total,
                        total_capped,
                        channels,
                        gateway_keys,
                        gateway_has_multiple_keys,
                        model_names,
                    ),
                )
                if len(self._log_page_aux_cache) > 64:
                    self._log_page_aux_cache.clear()

            return RequestLogPage(
                items=await self._hydrate_request_logs(
                    session,
                    entities,
                    gateway_has_multiple_keys=gateway_has_multiple_keys,
                ),
                total=int(total),
                total_capped=total_capped,
                limit=max(limit, 0),
                offset=max(offset, 0),
                channels=channels,
                gateway_keys=gateway_keys,
                gateway_has_multiple_keys=gateway_has_multiple_keys,
                model_names=model_names,
            )

    @staticmethod
    async def _request_log_channel_options(
        session: AsyncSession,
    ) -> list[RequestLogFilterOption]:
        labels = await load_channel_labels(session)
        options = [
            RequestLogFilterOption(id=channel_id, label=label or channel_id)
            for channel_id, label in labels
        ]
        if await session.scalar(
            select(literal(1)).where(RequestLogEntity.channel_id.is_(None)).limit(1)
        ):
            options.insert(0, RequestLogFilterOption(id="n/a", label="n/a"))
        return sorted(options, key=lambda item: (item.label.lower(), item.id))

    async def _request_log_key_options(
        self, session: AsyncSession
    ) -> tuple[list[RequestLogFilterOption], bool]:
        rows = await self._gateway_key_repo.list_key_options(session)
        has_nulls = bool(
            await session.scalar(
                select(literal(1))
                .where(RequestLogEntity.gateway_key_id.is_(None))
                .limit(1)
            )
        )
        options = [
            RequestLogFilterOption(id=key_id, label=remark or key_id)
            for key_id, remark in rows
        ]
        if has_nulls:
            options.append(RequestLogFilterOption(id="n/a", label="n/a"))
        return (
            sorted(options, key=lambda item: (item.label.lower(), item.id)),
            len(rows) + int(has_nulls) > 1,
        )

    async def list_site_runtime_summaries(self) -> list[SiteRuntimeSummary]:
        async with self._session_factory() as session:
            site_rows = (
                (
                    await session.execute(
                        select(SiteEntity).order_by(SiteEntity.name.asc())
                    )
                )
                .scalars()
                .all()
            )
            if not site_rows:
                return []

            channel_rows = await session.execute(
                select(
                    SiteProtocolConfigEntity.site_id.label("site_id"),
                    SiteProtocolConfigEntity.id.label("protocol_config_id"),
                    SiteProtocolConfigEntity.protocols_json.label("protocols_json"),
                ).order_by(
                    SiteProtocolConfigEntity.site_id.asc(),
                    SiteProtocolConfigEntity.id.asc(),
                )
            )
            channel_ids_by_site: dict[str, list[str]] = {
                site.id: [] for site in site_rows
            }
            for row in channel_rows.all():
                site_id = str(row.site_id)
                protocol_config_id = str(row.protocol_config_id)
                for protocol in _parse_supported_protocols_json(row.protocols_json):
                    channel_ids_by_site.setdefault(site_id, []).append(
                        _runtime_channel_id(protocol_config_id, protocol)
                    )

            recent_request_logs = (
                select(RequestLogEntity.protocol_config_id.label("protocol_config_id"))
                .where(RequestLogEntity.protocol_config_id.is_not(None))
                .where(
                    RequestLogEntity.lifecycle_status.in_(REQUEST_LOG_TERMINAL_STATUSES)
                )
                .order_by(
                    RequestLogEntity.created_at.desc(), RequestLogEntity.id.desc()
                )
                .limit(100)
                .subquery()
            )
            recent_count_rows = await session.execute(
                select(
                    SiteProtocolConfigEntity.site_id.label("site_id"),
                    func.count().label("recent_request_count"),
                )
                .select_from(recent_request_logs)
                .join(
                    SiteProtocolConfigEntity,
                    recent_request_logs.c.protocol_config_id
                    == SiteProtocolConfigEntity.id,
                )
                .group_by(SiteProtocolConfigEntity.site_id)
            )
            recent_request_count_by_site = {
                str(row.site_id): int(row.recent_request_count)
                for row in recent_count_rows.all()
            }

            # One index lookup per combo instead of a window over every log row:
            # this snapshot runs every 5s while the channel page is open.
            latest_log = (
                select(
                    RequestLogEntity.id.label("id"),
                    RequestLogEntity.channel_id.label("channel_id"),
                    RequestLogEntity.channel_name.label("channel_name"),
                    RequestLogEntity.status_code.label("status_code"),
                    RequestLogEntity.success.label("success"),
                    RequestLogEntity.error_message.label("error_message"),
                    RequestLogEntity.created_at.label("created_at"),
                )
                .where(
                    RequestLogEntity.protocol_config_id == SiteProtocolConfigEntity.id
                )
                .where(
                    RequestLogEntity.lifecycle_status.in_(REQUEST_LOG_TERMINAL_STATUSES)
                )
                .order_by(
                    RequestLogEntity.created_at.desc(), RequestLogEntity.id.desc()
                )
                .limit(1)
                .lateral("latest_log")
            )
            latest_rows = await session.execute(
                select(SiteProtocolConfigEntity.site_id, latest_log)
                .outerjoin(latest_log, true())
                .order_by(SiteProtocolConfigEntity.site_id.asc())
            )
            latest_by_site: dict[str, Any] = {}
            latest_sort_key: dict[str, tuple[datetime, int]] = {}
            for row in latest_rows.all():
                if row.channel_id is None and row.created_at is None:
                    continue
                site_id = str(row.site_id)
                sort_key = (row.created_at, int(row.id or 0))
                if site_id in latest_sort_key and sort_key <= latest_sort_key[site_id]:
                    continue
                latest_sort_key[site_id] = sort_key
                latest_by_site[site_id] = row

            bucket_anchor = datetime.now(UTC).replace(second=0, microsecond=0)
            bucket_anchor -= timedelta(minutes=bucket_anchor.minute % 5)
            bucket_start = bucket_anchor - timedelta(
                seconds=CHANNEL_HEALTH_BUCKET_SECONDS
                * (CHANNEL_HEALTH_BUCKET_COUNT - 1)
            )
            bucket_end = bucket_anchor + timedelta(
                seconds=CHANNEL_HEALTH_BUCKET_SECONDS
            )
            bucket_ranges = [
                (
                    bucket_start
                    + timedelta(seconds=CHANNEL_HEALTH_BUCKET_SECONDS * index),
                    bucket_start
                    + timedelta(seconds=CHANNEL_HEALTH_BUCKET_SECONDS * (index + 1)),
                )
                for index in range(CHANNEL_HEALTH_BUCKET_COUNT)
            ]
            bucket_counts_by_channel = {
                channel_id: [
                    {"success_count": 0, "total_count": 0}
                    for _ in range(CHANNEL_HEALTH_BUCKET_COUNT)
                ]
                for channel_ids in channel_ids_by_site.values()
                for channel_id in channel_ids
            }

            bucket_start_naive = bucket_start.replace(tzinfo=None)
            bucket_end_naive = bucket_end.replace(tzinfo=None)
            bucket_index_expr = func.floor(
                (
                    func.extract(
                        "epoch",
                        RequestLogEntity.created_at - literal(bucket_start_naive),
                    )
                )
                / CHANNEL_HEALTH_BUCKET_SECONDS
            ).label("bucket_index")
            bucket_rows = await session.execute(
                select(
                    RequestLogEntity.channel_id.label("channel_id"),
                    bucket_index_expr,
                    func.coalesce(func.sum(RequestLogEntity.success), 0).label(
                        "success_count"
                    ),
                    func.count().label("total_count"),
                )
                .where(
                    RequestLogEntity.channel_id.is_not(None),
                    RequestLogEntity.lifecycle_status.in_(
                        REQUEST_LOG_TERMINAL_STATUSES
                    ),
                    RequestLogEntity.created_at >= bucket_start_naive,
                    RequestLogEntity.created_at < bucket_end_naive,
                )
                .group_by(RequestLogEntity.channel_id, bucket_index_expr)
            )
            for row in bucket_rows.all():
                if row.channel_id is None:
                    continue
                channel_id = str(row.channel_id)
                counts = bucket_counts_by_channel.get(channel_id)
                if counts is None:
                    continue
                bucket_index = int(row.bucket_index)
                if bucket_index < 0 or bucket_index >= CHANNEL_HEALTH_BUCKET_COUNT:
                    continue
                counts[bucket_index]["total_count"] = int(row.total_count)
                counts[bucket_index]["success_count"] = int(row.success_count or 0)

            items: list[SiteRuntimeSummary] = []
            for site in site_rows:
                latest = latest_by_site.get(site.id)
                channel_summaries: list[SiteChannelRuntimeSummary] = []
                for channel_id in channel_ids_by_site.get(site.id, []):
                    bucket_counts = bucket_counts_by_channel.get(channel_id) or [
                        {"success_count": 0, "total_count": 0}
                        for _ in range(CHANNEL_HEALTH_BUCKET_COUNT)
                    ]
                    channel_summaries.append(
                        SiteChannelRuntimeSummary(
                            channel_id=channel_id,
                            health_buckets=[
                                SiteChannelHealthBucket(
                                    started_at=start.isoformat(),
                                    ended_at=end.isoformat(),
                                    success_count=bucket_counts[index]["success_count"],
                                    total_count=bucket_counts[index]["total_count"],
                                )
                                for index, (start, end) in enumerate(bucket_ranges)
                            ],
                        )
                    )
                items.append(
                    SiteRuntimeSummary(
                        site_id=site.id,
                        site_name=site.name,
                        recent_request_count=recent_request_count_by_site.get(
                            site.id, 0
                        ),
                        latest_request_at=(
                            latest.created_at.replace(tzinfo=UTC).isoformat()
                            if latest is not None and latest.created_at is not None
                            else None
                        ),
                        latest_success=(
                            bool(latest.success)
                            if latest is not None and latest.success is not None
                            else None
                        ),
                        latest_status_code=(
                            int(latest.status_code)
                            if latest is not None and latest.status_code is not None
                            else None
                        ),
                        latest_error_message=(
                            str(latest.error_message)
                            if latest is not None and latest.error_message is not None
                            else None
                        ),
                        latest_channel_id=(
                            str(latest.channel_id)
                            if latest is not None and latest.channel_id is not None
                            else None
                        ),
                        latest_channel_name=(
                            str(latest.channel_name)
                            if latest is not None and latest.channel_name is not None
                            else None
                        ),
                        channel_summaries=channel_summaries,
                    )
                )
            return items

    async def get_request_log(self, log_id: int) -> RequestLogDetail:
        async with self._session_factory() as session:
            entity = await session.get(RequestLogEntity, log_id)
            if entity is None:
                raise KeyError(log_id)
            remarks = await self._gateway_key_repo._gateway_key_remarks_by_id(
                session, [entity.gateway_key_id]
            )
            gateway_has_multiple_keys = await self._gateway_has_multiple_keys(session)
            credential_counts = await self._request_log_channel_credential_counts(
                session, [entity.channel_id]
            )
            return self._to_request_log_detail(
                entity,
                gateway_key_remark=remarks.get(entity.gateway_key_id or ""),
                gateway_has_multiple_keys=gateway_has_multiple_keys,
                channel_has_multiple_credentials=(
                    credential_counts.get(entity.channel_id or "", 0) > 1
                ),
            )
