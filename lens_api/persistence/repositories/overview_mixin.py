from __future__ import annotations

import asyncio
import time

import time

from sqlalchemy import Float, and_, case, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..shared import (
    Any,
    AsyncSession,
    cast,
    GatewayApiKeyEntity,
    ImportedStatsDailyEntity,
    ImportedStatsTotalEntity,
    OverviewChannelAnalytics,
    OverviewChannelDailyStatsEntity,
    OverviewChannelHealthPoint,
    OverviewChannelMetricPoint,
    OverviewChannelTrendPoint,
    OverviewDailyPoint,
    OverviewDimensionDailyStatsEntity,
    OverviewDimensionTrendPoint,
    OverviewDimensionUsageAnalytics,
    OverviewDimensionUsagePoint,
    OverviewModelChannelUsagePoint,
    OverviewModelAnalytics,
    OverviewModelDailyStatsEntity,
    OverviewModelMetricPoint,
    OverviewModelTrendPoint,
    OverviewPerformanceAnalytics,
    OverviewPerformancePoint,
    OverviewPerformanceTrendPoint,
    OverviewSummary,
    OverviewSummaryMetric,
    RequestLogDailyStatsEntity,
    RequestLogEntity,
    RequestLogLifecycleStatus,
    SiteCredentialEntity,
    UTC,
    ZoneInfo,
    datetime,
    func,
    json,
    literal,
    select,
    timedelta,
)


class OverviewMixin:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_overview_summary(self, days: int = 7) -> OverviewSummary:
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        async with self._session_factory() as session:
            if days != 0:
                comparison_offset = 1 if days == -1 else days
                recent = await self._merged_period_totals(
                    session, days=days, time_zone=time_zone
                )
                previous = await self._merged_period_totals(
                    session,
                    days=days,
                    offset_days=comparison_offset,
                    time_zone=time_zone,
                )
            else:
                recent = await self._merged_period_totals(
                    session, days=0, time_zone=time_zone
                )
                previous = self._zero_totals()

        request_count = int(recent["request_count"])
        successful_requests = int(recent["successful_requests"])
        failed_requests = int(recent["failed_requests"])
        previous_request_count = float(previous["request_count"])
        previous_success_rate = self._ratio_percent(
            previous["successful_requests"], previous_request_count
        )
        success_rate = self._ratio_percent(successful_requests, request_count)
        wait_time_ms = int(recent["wait_time_ms"])
        previous_average_latency = self._safe_divide(
            previous["wait_time_ms"], previous_request_count
        )
        average_latency = self._safe_divide(wait_time_ms, request_count)
        input_tokens = int(recent["input_tokens"])
        cache_read_input_tokens = int(recent["cache_read_input_tokens"])
        cache_write_input_tokens = int(recent["cache_write_input_tokens"])
        output_tokens = int(recent["output_tokens"])
        total_cost_usd = float(recent["total_cost_usd"])
        input_cost_usd = float(recent["input_cost_usd"])
        output_cost_usd = float(recent["output_cost_usd"])

        return OverviewSummary(
            request_count=OverviewSummaryMetric(
                value=request_count,
                delta=self._delta_percent(request_count, previous["request_count"]),
            ),
            successful_requests=OverviewSummaryMetric(
                value=successful_requests,
                delta=self._delta_percent(
                    successful_requests, previous["successful_requests"]
                ),
            ),
            failed_requests=OverviewSummaryMetric(
                value=failed_requests,
                delta=self._delta_percent(failed_requests, previous["failed_requests"]),
            ),
            success_rate=OverviewSummaryMetric(
                value=success_rate,
                delta=round(success_rate - previous_success_rate, 2),
            ),
            wait_time_ms=OverviewSummaryMetric(
                value=wait_time_ms,
                delta=self._delta_percent(wait_time_ms, previous["wait_time_ms"]),
            ),
            average_latency_ms=OverviewSummaryMetric(
                value=average_latency,
                delta=self._delta_percent(average_latency, previous_average_latency),
            ),
            total_tokens=OverviewSummaryMetric(
                value=input_tokens + output_tokens,
                delta=self._delta_percent(
                    input_tokens + output_tokens,
                    previous["input_tokens"] + previous["output_tokens"],
                ),
            ),
            total_cost_usd=OverviewSummaryMetric(
                value=total_cost_usd,
                delta=self._delta_percent(total_cost_usd, previous["total_cost_usd"]),
            ),
            input_tokens=OverviewSummaryMetric(
                value=input_tokens,
                delta=self._delta_percent(input_tokens, previous["input_tokens"]),
            ),
            cache_read_input_tokens=OverviewSummaryMetric(
                value=cache_read_input_tokens,
                delta=self._delta_percent(
                    cache_read_input_tokens, previous["cache_read_input_tokens"]
                ),
            ),
            cache_write_input_tokens=OverviewSummaryMetric(
                value=cache_write_input_tokens,
                delta=self._delta_percent(
                    cache_write_input_tokens, previous["cache_write_input_tokens"]
                ),
            ),
            input_cost_usd=OverviewSummaryMetric(
                value=input_cost_usd,
                delta=self._delta_percent(input_cost_usd, previous["input_cost_usd"]),
            ),
            output_tokens=OverviewSummaryMetric(
                value=output_tokens,
                delta=self._delta_percent(output_tokens, previous["output_tokens"]),
            ),
            output_cost_usd=OverviewSummaryMetric(
                value=output_cost_usd,
                delta=self._delta_percent(output_cost_usd, previous["output_cost_usd"]),
            ),
        )

    async def list_overview_daily(self, days: int = 0) -> list[OverviewDailyPoint]:
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        async with self._session_factory() as session:
            return await self._merged_daily_points(
                session, days=days, time_zone=time_zone
            )

    async def get_model_analytics(
        self,
        days: int = 7,
        gateway_key_id: str | None = None,
        metric: str = "cost",
    ) -> OverviewModelAnalytics:
        model_metric = metric if metric in {"cost", "requests", "tokens"} else "cost"
        normalized_gateway_key_id = self._normalize_gateway_key_id(gateway_key_id)
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        async with self._session_factory() as session:
            if normalized_gateway_key_id is not None:
                archived_model_rows = []
                if days == -1:
                    live_model_rows = await self._request_log_model_hourly_rows(
                        session,
                        days=days,
                        gateway_key_id=normalized_gateway_key_id,
                        include_archived=True,
                        time_zone=time_zone,
                    )
                else:
                    live_model_rows = await self._request_log_model_daily_rows(
                        session,
                        days=days,
                        gateway_key_id=normalized_gateway_key_id,
                        include_archived=True,
                        time_zone=time_zone,
                    )
            elif days == -1:
                archived_model_rows = []
                live_model_rows = await self._request_log_model_hourly_rows(
                    session, days=days, time_zone=time_zone
                )
            else:
                window_start, window_end = self._resolve_imported_date_window(
                    days, time_zone=time_zone
                )
                archived_model_rows = await self._overview_model_daily_rows(
                    session,
                    start_at=window_start,
                    end_at=window_end,
                )
                live_model_rows = await self._request_log_model_daily_rows(
                    session, days=days, time_zone=time_zone
                )

        merged_rows: dict[tuple[str, str], dict[str, float | str]] = {}
        for date_value, model, requests, total_tokens, total_cost in [
            *archived_model_rows,
            *live_model_rows,
        ]:
            if not model:
                continue
            key = (str(date_value), str(model))
            current = merged_rows.get(key)
            if current is None:
                merged_rows[key] = {
                    "date": str(date_value),
                    "model": str(model),
                    "requests": float(requests),
                    "total_tokens": float(total_tokens),
                    "total_cost_usd": float(total_cost),
                }
                continue
            current["requests"] = float(current["requests"]) + float(requests)
            current["total_tokens"] = float(current["total_tokens"]) + float(
                total_tokens
            )
            current["total_cost_usd"] = float(current["total_cost_usd"]) + float(
                total_cost
            )

        trend_rows = sorted(
            merged_rows.values(),
            key=lambda item: (str(item["date"]), str(item["model"])),
        )

        model_rows: dict[str, dict[str, float | str]] = {}
        for item in merged_rows.values():
            model_key = str(item["model"])
            current = model_rows.get(model_key)
            if current is None:
                model_rows[model_key] = {
                    "model": model_key,
                    "requests": float(item["requests"]),
                    "total_tokens": float(item["total_tokens"]),
                    "total_cost_usd": float(item["total_cost_usd"]),
                }
                continue
            current["requests"] = float(current["requests"]) + float(item["requests"])
            current["total_tokens"] = float(current["total_tokens"]) + float(
                item["total_tokens"]
            )
            current["total_cost_usd"] = float(current["total_cost_usd"]) + float(
                item["total_cost_usd"]
            )

        def metric_value(item: dict[str, float | str]) -> float:
            if model_metric == "requests":
                return float(item["requests"])
            if model_metric == "tokens":
                return float(item["total_tokens"])
            return float(item["total_cost_usd"])

        def secondary_metric_value(item: dict[str, float | str]) -> float:
            if model_metric == "cost":
                return float(item["requests"])
            return float(item["total_cost_usd"])

        aggregated_models = list(model_rows.values())
        distribution_rows = sorted(
            aggregated_models,
            key=lambda item: (
                -metric_value(item),
                -secondary_metric_value(item),
                str(item["model"]),
            ),
        )

        distribution = [
            OverviewModelMetricPoint(
                model=str(item["model"]),
                requests=int(item["requests"]),
                total_tokens=int(item["total_tokens"]),
                total_cost_usd=float(item["total_cost_usd"]),
            )
            for item in distribution_rows[:12]
        ]

        trend = [
            OverviewModelTrendPoint(
                date=str(item["date"]),
                model=str(item["model"]),
                value=metric_value(item),
            )
            for item in trend_rows
        ]

        available_models = sorted(
            {item.model for item in distribution} | {item.model for item in trend}
        )
        return OverviewModelAnalytics(
            distribution=distribution,
            trend=trend,
            available_models=available_models,
        )

    async def get_channel_analytics(
        self,
        days: int = 7,
        gateway_key_id: str | None = None,
        metric: str = "cost",
    ) -> OverviewChannelAnalytics:
        channel_metric = metric if metric in {"cost", "requests", "tokens"} else "cost"
        normalized_gateway_key_id = self._normalize_gateway_key_id(gateway_key_id)
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        async with self._session_factory() as session:
            if normalized_gateway_key_id is not None:
                archived_channel_rows: list[tuple[str, str, str, int, int, float]] = []
                if days == -1:
                    live_channel_rows = await self._request_log_channel_hourly_rows(
                        session,
                        days=days,
                        gateway_key_id=normalized_gateway_key_id,
                        include_archived=True,
                        time_zone=time_zone,
                    )
                else:
                    live_channel_rows = await self._request_log_channel_daily_rows(
                        session,
                        days=days,
                        gateway_key_id=normalized_gateway_key_id,
                        include_archived=True,
                        time_zone=time_zone,
                    )
            elif days == -1:
                archived_channel_rows = []
                live_channel_rows = await self._request_log_channel_hourly_rows(
                    session, days=days, time_zone=time_zone
                )
            else:
                window_start, window_end = self._resolve_imported_date_window(
                    days, time_zone=time_zone
                )
                archived_channel_rows = await self._overview_channel_daily_rows(
                    session,
                    start_at=window_start,
                    end_at=window_end,
                )
                live_channel_rows = await self._request_log_channel_daily_rows(
                    session, days=days, time_zone=time_zone
                )

        merged_rows: dict[tuple[str, str], dict[str, float | str]] = {}
        for (
            date_value,
            channel_id,
            channel_name,
            requests,
            total_tokens,
            total_cost,
        ) in [*archived_channel_rows, *live_channel_rows]:
            if not channel_id:
                continue
            key = (str(date_value), str(channel_id))
            current = merged_rows.get(key)
            if current is None:
                merged_rows[key] = {
                    "date": str(date_value),
                    "channel_id": str(channel_id),
                    "channel_name": str(channel_name or channel_id),
                    "requests": float(requests),
                    "total_tokens": float(total_tokens),
                    "total_cost_usd": float(total_cost),
                }
                continue
            current["requests"] = float(current["requests"]) + float(requests)
            current["total_tokens"] = float(current["total_tokens"]) + float(
                total_tokens
            )
            current["total_cost_usd"] = float(current["total_cost_usd"]) + float(
                total_cost
            )
            if not current["channel_name"] or current["channel_name"] == str(
                channel_id
            ):
                if channel_name:
                    current["channel_name"] = str(channel_name)

        trend_rows = sorted(
            merged_rows.values(),
            key=lambda item: (str(item["date"]), str(item["channel_id"])),
        )

        channel_rows: dict[str, dict[str, float | str]] = {}
        for item in merged_rows.values():
            channel_key = str(item["channel_id"])
            current = channel_rows.get(channel_key)
            if current is None:
                channel_rows[channel_key] = {
                    "channel_id": channel_key,
                    "channel_name": str(item["channel_name"]),
                    "requests": float(item["requests"]),
                    "total_tokens": float(item["total_tokens"]),
                    "total_cost_usd": float(item["total_cost_usd"]),
                }
                continue
            current["requests"] = float(current["requests"]) + float(item["requests"])
            current["total_tokens"] = float(current["total_tokens"]) + float(
                item["total_tokens"]
            )
            current["total_cost_usd"] = float(current["total_cost_usd"]) + float(
                item["total_cost_usd"]
            )

        def channel_metric_value(item: dict[str, float | str]) -> float:
            if channel_metric == "requests":
                return float(item["requests"])
            if channel_metric == "tokens":
                return float(item["total_tokens"])
            return float(item["total_cost_usd"])

        def channel_secondary_metric_value(item: dict[str, float | str]) -> float:
            if channel_metric == "cost":
                return float(item["requests"])
            return float(item["total_cost_usd"])

        aggregated_channels = list(channel_rows.values())
        distribution_rows = sorted(
            aggregated_channels,
            key=lambda item: (
                -channel_metric_value(item),
                -channel_secondary_metric_value(item),
                str(item["channel_id"]),
            ),
        )

        distribution = [
            OverviewChannelMetricPoint(
                channel_id=str(item["channel_id"]),
                channel_name=str(item["channel_name"]),
                requests=int(item["requests"]),
                total_tokens=int(item["total_tokens"]),
                total_cost_usd=float(item["total_cost_usd"]),
            )
            for item in distribution_rows[:12]
        ]

        trend = [
            OverviewChannelTrendPoint(
                date=str(item["date"]),
                channel_id=str(item["channel_id"]),
                channel_name=str(item["channel_name"]),
                value=channel_metric_value(item),
            )
            for item in trend_rows
        ]

        available_channels = sorted(
            {item.channel_name for item in distribution}
            | {item.channel_name for item in trend}
        )
        return OverviewChannelAnalytics(
            distribution=distribution,
            trend=trend,
            available_channels=available_channels,
        )

    async def get_channel_health(
        self, days: int = 7, gateway_key_id: str | None = None
    ) -> list[OverviewChannelHealthPoint]:
        rows = await self._merged_dimension_rows(
            dimension_type="channel_attempt",
            days=days,
            gateway_key_id=gateway_key_id,
        )
        aggregated = self._aggregate_dimension_rows(rows)
        items: list[OverviewChannelHealthPoint] = []
        for item in aggregated.values():
            request_count = int(item["request_count"])
            successful_requests = int(item["successful_requests"])
            items.append(
                OverviewChannelHealthPoint(
                    channel_id=str(item["id"]),
                    channel_name=str(item["name"]),
                    request_count=request_count,
                    successful_requests=successful_requests,
                    failed_requests=int(item["failed_requests"]),
                    success_rate=self._ratio_percent(
                        successful_requests, request_count
                    ),
                    average_latency_ms=self._safe_divide(
                        float(item["latency_ms_sum"]), request_count
                    ),
                )
            )
        return sorted(
            items,
            key=lambda item: (
                -item.success_rate,
                -item.request_count,
                -item.failed_requests,
                item.channel_name,
            ),
        )[:30]

    async def get_dimension_usage(
        self,
        dimension_type: str,
        days: int = 7,
        gateway_key_id: str | None = None,
        metric: str = "cost",
    ) -> OverviewDimensionUsageAnalytics:
        normalized_type = self._normalize_overview_dimension_type(dimension_type)
        rows = await self._merged_dimension_rows(
            dimension_type=normalized_type,
            days=days,
            gateway_key_id=gateway_key_id,
        )
        aggregated = self._aggregate_dimension_rows(rows)

        channel_items_by_model = (
            await self._model_channel_usage_items_by_model(
                days=days,
                gateway_key_id=gateway_key_id,
                metric=metric,
                model_ids={str(item["id"]) for item in aggregated.values()},
            )
            if normalized_type == "model"
            else {}
        )

        def metric_value(item: dict[str, float | str]) -> float:
            if metric == "requests":
                return float(item["request_count"])
            if metric == "tokens":
                return float(item["total_tokens"])
            model_channel_items = channel_items_by_model.get(str(item["id"]), [])
            if model_channel_items:
                return sum(
                    channel_item.total_cost_usd for channel_item in model_channel_items
                )
            return float(item["total_cost_usd"])

        sorted_items = sorted(
            aggregated.values(),
            key=lambda current: (
                -metric_value(current),
                -float(current["request_count"]),
                str(current["name"]),
            ),
        )[:20]
        visible_ids = {str(item["id"]) for item in sorted_items}
        items = [
            self._dimension_usage_point(
                item,
                channel_items=channel_items_by_model.get(str(item["id"]), []),
            )
            for item in sorted_items
        ]
        trend = [
            OverviewDimensionTrendPoint(
                date=str(row["date"]),
                id=str(row["dimension_id"]),
                name=str(row["dimension_name"]),
                request_count=int(row["request_count"]),
                input_tokens=int(row["input_tokens"]),
                cache_read_input_tokens=int(row["cache_read_input_tokens"]),
                cache_write_input_tokens=int(row["cache_write_input_tokens"]),
                output_tokens=int(row["output_tokens"]),
                total_tokens=int(row["total_tokens"]),
                total_cost_usd=float(row["total_cost_usd"]),
                average_latency_ms=self._safe_divide(
                    float(row["latency_ms_sum"]), float(row["request_count"])
                ),
            )
            for row in sorted(
                rows, key=lambda row: (str(row["date"]), str(row["dimension_id"]))
            )
            if str(row["dimension_id"]) in visible_ids
        ]
        return OverviewDimensionUsageAnalytics(
            dimension_type=normalized_type, items=items, trend=trend
        )

    @classmethod
    def _dimension_usage_point(
        cls,
        item: dict[str, float | str],
        *,
        channel_items: list[OverviewModelChannelUsagePoint] | None = None,
    ) -> OverviewDimensionUsagePoint:
        resolved_channel_items = channel_items or []
        if resolved_channel_items:
            input_cost_usd = sum(
                channel_item.input_cost_usd for channel_item in resolved_channel_items
            )
            output_cost_usd = sum(
                channel_item.output_cost_usd for channel_item in resolved_channel_items
            )
            total_cost_usd = sum(
                channel_item.total_cost_usd for channel_item in resolved_channel_items
            )
        else:
            input_cost_usd = float(item["input_cost_usd"])
            output_cost_usd = float(item["output_cost_usd"])
            total_cost_usd = float(item["total_cost_usd"])
        return OverviewDimensionUsagePoint(
            id=str(item["id"]),
            name=str(item["name"]),
            request_count=int(item["request_count"]),
            successful_requests=int(item["successful_requests"]),
            failed_requests=int(item["failed_requests"]),
            input_tokens=int(item["input_tokens"]),
            cache_read_input_tokens=int(item["cache_read_input_tokens"]),
            cache_write_input_tokens=int(item["cache_write_input_tokens"]),
            output_tokens=int(item["output_tokens"]),
            total_tokens=int(item["total_tokens"]),
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            total_cost_usd=total_cost_usd,
            average_latency_ms=cls._safe_divide(
                float(item["latency_ms_sum"]), float(item["request_count"])
            ),
            channel_items=resolved_channel_items,
        )

    async def _model_channel_usage_items_by_model(
        self,
        *,
        days: int,
        gateway_key_id: str | None,
        metric: str,
        model_ids: set[str],
    ) -> dict[str, list[OverviewModelChannelUsagePoint]]:
        normalized_model_ids = {item.strip() for item in model_ids if item.strip()}
        if not normalized_model_ids:
            return {}
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        model_expr = func.nullif(func.trim(RequestLogEntity.upstream_model_name), "")

        async def fetch_rows() -> list[Any]:
            async with self._session_factory() as session:
                inner = (
                    select(
                        model_expr.label("model"),
                        self._primary_attempt_field_expr("credential_id").label(
                            "credential_id"
                        ),
                        self._primary_attempt_field_expr("credential_name").label(
                            "credential_name"
                        ),
                        RequestLogEntity.channel_id.label("channel_id"),
                        RequestLogEntity.channel_name.label("channel_name"),
                        RequestLogEntity.success,
                        RequestLogEntity.latency_ms,
                        RequestLogEntity.input_tokens,
                        RequestLogEntity.cache_read_input_tokens,
                        RequestLogEntity.cache_write_input_tokens,
                        RequestLogEntity.output_tokens,
                        RequestLogEntity.total_tokens,
                        RequestLogEntity.input_cost_usd,
                        RequestLogEntity.output_cost_usd,
                        RequestLogEntity.total_cost_usd,
                    )
                    .where(
                        RequestLogEntity.lifecycle_status.in_(
                            [
                                RequestLogLifecycleStatus.SUCCEEDED.value,
                                RequestLogLifecycleStatus.FAILED.value,
                            ]
                        )
                    )
                    .where(model_expr.in_(sorted(normalized_model_ids)))
                )
                inner = self._apply_request_log_window(
                    inner, days=days, time_zone=time_zone
                )
                inner = self._apply_gateway_key_filter(
                    inner, gateway_key_id=gateway_key_id
                )
                sub = inner.subquery()
                credential_id_col = sub.c.credential_id
                credential_name_col = sub.c.credential_name
                stmt = select(
                    sub.c.model,
                    credential_id_col.label("credential_id"),
                    sub.c.channel_id,
                    func.max(func.nullif(func.trim(sub.c.channel_name), "")).label(
                        "channel_name"
                    ),
                    func.max(func.nullif(func.trim(credential_name_col), "")).label(
                        "credential_name"
                    ),
                    func.count().label("request_count"),
                    func.sum(sub.c.success).label("successful_requests"),
                    func.sum(sub.c.latency_ms).label("latency_ms_sum"),
                    func.sum(sub.c.input_tokens).label("input_tokens"),
                    func.sum(sub.c.cache_read_input_tokens).label(
                        "cache_read_input_tokens"
                    ),
                    func.sum(sub.c.cache_write_input_tokens).label(
                        "cache_write_input_tokens"
                    ),
                    func.sum(sub.c.output_tokens).label("output_tokens"),
                    func.sum(sub.c.total_tokens).label("total_tokens"),
                    func.sum(sub.c.input_cost_usd).label("input_cost_usd"),
                    func.sum(sub.c.output_cost_usd).label("output_cost_usd"),
                    func.sum(sub.c.total_cost_usd).label("total_cost_usd"),
                ).group_by(sub.c.model, credential_id_col, sub.c.channel_id)
                rows = (await session.execute(stmt)).all()
                return list(rows)

        rows = await self._cached_live_rows(
            (
                "model_channel_usage",
                days,
                gateway_key_id,
                tuple(sorted(normalized_model_ids)),
                str(time_zone),
            ),
            fetch_rows,
        )
        async with self._session_factory() as session:
            cost_multipliers = await self._credential_cost_multipliers_by_id(session)

        buckets: dict[tuple[str, str], dict[str, float | str]] = {}
        for row in rows:
            normalized_model_id = str(row.model or "").strip()
            if not normalized_model_id:
                continue
            credential_id = str(row.credential_id or "").strip()
            channel_id = str(row.channel_id or "").strip()
            dimension_id = credential_id or channel_id
            if not dimension_id:
                continue
            channel_name = str(row.channel_name or channel_id)
            if credential_id:
                credential_name = str(row.credential_name or "")
                dimension_name = f"{channel_name} - {credential_name or credential_id}"
            else:
                dimension_name = channel_name or dimension_id
            multiplier = self._row_cost_multiplier(row.credential_id, cost_multipliers)
            key = (normalized_model_id, dimension_id)
            current = buckets.setdefault(
                key,
                {
                    "model_id": normalized_model_id,
                    "id": dimension_id,
                    "name": dimension_name,
                    "request_count": 0.0,
                    "successful_requests": 0.0,
                    "failed_requests": 0.0,
                    "latency_ms_sum": 0.0,
                    "input_tokens": 0.0,
                    "cache_read_input_tokens": 0.0,
                    "cache_write_input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "total_tokens": 0.0,
                    "input_cost_usd": 0.0,
                    "output_cost_usd": 0.0,
                    "total_cost_usd": 0.0,
                    "cost_multiplier": multiplier,
                },
            )
            request_count = int(row.request_count)
            successful_requests = int(row.successful_requests or 0)
            current["cost_multiplier"] = multiplier
            current["request_count"] = float(current["request_count"]) + float(
                request_count
            )
            current["successful_requests"] = float(
                current["successful_requests"]
            ) + float(successful_requests)
            current["failed_requests"] = float(current["failed_requests"]) + float(
                request_count - successful_requests
            )
            current["latency_ms_sum"] = float(current["latency_ms_sum"]) + float(
                row.latency_ms_sum or 0
            )
            current["input_tokens"] = float(current["input_tokens"]) + float(
                row.input_tokens or 0
            )
            current["cache_read_input_tokens"] = float(
                current["cache_read_input_tokens"]
            ) + float(row.cache_read_input_tokens or 0)
            current["cache_write_input_tokens"] = float(
                current["cache_write_input_tokens"]
            ) + float(row.cache_write_input_tokens or 0)
            current["output_tokens"] = float(current["output_tokens"]) + float(
                row.output_tokens or 0
            )
            current["total_tokens"] = float(current["total_tokens"]) + float(
                row.total_tokens or 0
            )
            current["input_cost_usd"] = (
                float(current["input_cost_usd"])
                + float(row.input_cost_usd or 0.0) * multiplier
            )
            current["output_cost_usd"] = (
                float(current["output_cost_usd"])
                + float(row.output_cost_usd or 0.0) * multiplier
            )
            current["total_cost_usd"] = (
                float(current["total_cost_usd"])
                + float(row.total_cost_usd or 0.0) * multiplier
            )

        def metric_value(item: dict[str, float | str]) -> float:
            if metric == "requests":
                return float(item["request_count"])
            if metric == "tokens":
                return float(item["total_tokens"])
            return float(item["total_cost_usd"])

        result: dict[str, list[OverviewModelChannelUsagePoint]] = {}
        for item in sorted(
            buckets.values(),
            key=lambda current: (
                str(current["model_id"]),
                -metric_value(current),
                -float(current["request_count"]),
                str(current["name"]),
            ),
        ):
            result.setdefault(str(item["model_id"]), []).append(
                self._model_channel_usage_point(item)
            )
        return result

    @classmethod
    def _model_channel_usage_point(
        cls, item: dict[str, float | str]
    ) -> OverviewModelChannelUsagePoint:
        return OverviewModelChannelUsagePoint(
            id=str(item["id"]),
            name=str(item["name"]),
            request_count=int(item["request_count"]),
            successful_requests=int(item["successful_requests"]),
            failed_requests=int(item["failed_requests"]),
            input_tokens=int(item["input_tokens"]),
            cache_read_input_tokens=int(item["cache_read_input_tokens"]),
            cache_write_input_tokens=int(item["cache_write_input_tokens"]),
            output_tokens=int(item["output_tokens"]),
            total_tokens=int(item["total_tokens"]),
            input_cost_usd=float(item["input_cost_usd"]),
            output_cost_usd=float(item["output_cost_usd"]),
            total_cost_usd=float(item["total_cost_usd"]),
            average_latency_ms=cls._safe_divide(
                float(item["latency_ms_sum"]), float(item["request_count"])
            ),
            cost_multiplier=max(float(item.get("cost_multiplier", 1.0)), 0.0),
        )

    async def get_performance_analytics(
        self,
        dimension_type: str,
        days: int = 7,
        gateway_key_id: str | None = None,
    ) -> OverviewPerformanceAnalytics:
        normalized_type = self._normalize_overview_dimension_type(dimension_type)
        rows = await self._merged_dimension_rows(
            dimension_type=normalized_type,
            days=days,
            gateway_key_id=gateway_key_id,
        )
        aggregated = self._aggregate_dimension_rows(rows)
        performance_items = [
            self._dimension_performance_point(item)
            for item in aggregated.values()
            if int(item["request_count"]) > 0
        ]
        performance_items = sorted(
            performance_items,
            key=lambda item: (
                item.average_latency_ms if item.average_latency_ms > 0 else 10**12,
                -item.request_count,
                item.name,
            ),
        )[:20]
        visible_ids = {item.id for item in performance_items}
        trend = [
            self._dimension_performance_trend_point(row)
            for row in sorted(
                rows, key=lambda row: (str(row["date"]), str(row["dimension_id"]))
            )
            if str(row["dimension_id"]) in visible_ids and int(row["request_count"]) > 0
        ]
        return OverviewPerformanceAnalytics(
            dimension_type=normalized_type, items=performance_items, trend=trend
        )

    async def _merged_dimension_rows(
        self,
        *,
        dimension_type: str,
        days: int,
        gateway_key_id: str | None = None,
    ) -> list[dict[str, float | str]]:
        normalized_gateway_key_id = self._normalize_gateway_key_id(gateway_key_id)
        time_zone = self._runtime_time_zone(
            await self._settings_repo.get_runtime_settings()
        )
        date_format = "%Y%m%d%H" if days == -1 else "%Y%m%d"
        async with self._session_factory() as session:
            archived_rows: list[dict[str, float | str]] = []
            if normalized_gateway_key_id is None and days != -1:
                start_at, end_at = self._resolve_imported_date_window(
                    days, time_zone=time_zone
                )
                archived_rows = await self._overview_dimension_daily_rows(
                    session,
                    dimension_type=dimension_type,
                    start_at=start_at,
                    end_at=end_at,
                )
            live_rows = await self._request_log_dimension_rows(
                session,
                dimension_type=dimension_type,
                days=days,
                gateway_key_id=normalized_gateway_key_id,
                include_archived=normalized_gateway_key_id is not None,
                date_format=date_format,
                time_zone=time_zone,
            )
        merged: dict[tuple[str, str, str], dict[str, float | str]] = {}
        for row in [*archived_rows, *live_rows]:
            key = (
                str(row["date"]),
                str(row["dimension_type"]),
                str(row["dimension_id"]),
            )
            current = merged.get(key)
            if current is None:
                merged[key] = dict(row)
                continue
            if (
                not current["dimension_name"]
                or current["dimension_name"] == current["dimension_id"]
            ):
                current["dimension_name"] = str(row["dimension_name"])
            for field in self._overview_dimension_numeric_fields():
                current[field] = float(current[field]) + float(row[field])
        return [merged[key] for key in sorted(merged)]

    async def _overview_dimension_daily_rows(
        self,
        session: AsyncSession,
        *,
        dimension_type: str,
        start_at: str | None,
        end_at: str | None,
    ) -> list[dict[str, float | str]]:
        stmt = select(OverviewDimensionDailyStatsEntity).where(
            OverviewDimensionDailyStatsEntity.dimension_type == dimension_type
        )
        if start_at is not None:
            stmt = stmt.where(OverviewDimensionDailyStatsEntity.date >= start_at)
        if end_at is not None:
            stmt = stmt.where(OverviewDimensionDailyStatsEntity.date < end_at)
        rows = (
            (
                await session.execute(
                    stmt.order_by(
                        OverviewDimensionDailyStatsEntity.date.asc(),
                        OverviewDimensionDailyStatsEntity.dimension_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        result = [self._dimension_entity_to_row(item) for item in rows]
        if dimension_type == "channel":
            cost_multipliers = await self._credential_cost_multipliers_by_id(session)
            return [
                self._with_channel_dimension_cost_multiplier(row, cost_multipliers)
                for row in result
            ]
        return result

    @classmethod
    def _dimension_sql_row_to_dict(cls, row: Any) -> dict[str, float | str]:
        request_count = int(row.request_count)
        successful_requests = int(row.successful_requests or 0)
        return {
            "date": str(row.date),
            "dimension_type": str(row.dimension_type),
            "dimension_id": str(row.dimension_id),
            "dimension_name": str(row.dimension_name or row.dimension_id),
            "credential_id": getattr(row, "credential_id", None),
            "request_count": float(request_count),
            "successful_requests": float(successful_requests),
            "failed_requests": float(request_count - successful_requests),
            "latency_ms_sum": float(row.latency_ms_sum or 0),
            "first_token_latency_ms_sum": float(row.first_token_latency_ms_sum or 0),
            "input_tokens": float(row.input_tokens or 0),
            "cache_read_input_tokens": float(row.cache_read_input_tokens or 0),
            "cache_write_input_tokens": float(row.cache_write_input_tokens or 0),
            "output_tokens": float(row.output_tokens or 0),
            "total_tokens": float(row.total_tokens or 0),
            "input_cost_usd": float(row.input_cost_usd or 0),
            "output_cost_usd": float(row.output_cost_usd or 0),
            "total_cost_usd": float(row.total_cost_usd or 0),
        }

    def _request_log_dimension_sums(self, sub: Any) -> tuple[Any, ...]:
        return (
            func.count().label("request_count"),
            func.sum(sub.c.success).label("successful_requests"),
            func.sum(sub.c.latency_ms).label("latency_ms_sum"),
            func.sum(sub.c.first_token_latency_ms).label("first_token_latency_ms_sum"),
            func.sum(sub.c.input_tokens).label("input_tokens"),
            func.sum(sub.c.cache_read_input_tokens).label("cache_read_input_tokens"),
            func.sum(sub.c.cache_write_input_tokens).label("cache_write_input_tokens"),
            func.sum(sub.c.output_tokens).label("output_tokens"),
            func.sum(sub.c.total_tokens).label("total_tokens"),
            func.sum(sub.c.input_cost_usd).label("input_cost_usd"),
            func.sum(sub.c.output_cost_usd).label("output_cost_usd"),
            func.sum(sub.c.total_cost_usd).label("total_cost_usd"),
        )

    async def _cached_live_rows(
        self, cache_key: tuple[Any, ...], miss_fn: Any
    ) -> list[Any]:
        cache = getattr(self, "_overview_live_cache", None)
        if cache is None:
            return await miss_fn()
        hit = cache.get(cache_key)
        now = time.monotonic()
        if hit is not None:
            stored_at, value = hit
            if isinstance(value, asyncio.Future):
                return [
                    dict(row) if isinstance(row, dict) else row
                    for row in (await asyncio.shield(value))
                ]
            if now - stored_at < 15.0:
                return [dict(row) if isinstance(row, dict) else row for row in value]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[Any]] = loop.create_future()
        cache[cache_key] = (now, future)
        if len(cache) > 64:
            cache.clear()
        try:
            result = await miss_fn()
        except Exception:
            cache.pop(cache_key, None)
            future.cancel()
            raise
        cache[cache_key] = (now, result)
        future.set_result(result)
        return result

    async def _request_log_primary_dimension_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        date_format: str,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[dict[str, float | str]]:
        bucket_expr = self._local_bucket_expr(date_format, time_zone)
        inner = select(
            bucket_expr.label("bucket"),
            self._primary_attempt_field_expr("credential_id").label("credential_id"),
            self._primary_attempt_field_expr("credential_name").label(
                "credential_name"
            ),
            self._primary_attempt_field_expr("attempt_channel_id").label(
                "attempt_channel_id"
            ),
            self._primary_attempt_field_expr("attempt_channel_name").label(
                "attempt_channel_name"
            ),
            RequestLogEntity.channel_id,
            RequestLogEntity.channel_name,
            func.nullif(func.trim(RequestLogEntity.upstream_model_name), "").label(
                "model"
            ),
            RequestLogEntity.gateway_key_id,
            RequestLogEntity.success,
            RequestLogEntity.latency_ms,
            RequestLogEntity.first_token_latency_ms,
            RequestLogEntity.input_tokens,
            RequestLogEntity.cache_read_input_tokens,
            RequestLogEntity.cache_write_input_tokens,
            RequestLogEntity.output_tokens,
            RequestLogEntity.total_tokens,
            RequestLogEntity.input_cost_usd,
            RequestLogEntity.output_cost_usd,
            RequestLogEntity.total_cost_usd,
        ).where(
            RequestLogEntity.lifecycle_status.in_(
                [
                    RequestLogLifecycleStatus.SUCCEEDED.value,
                    RequestLogLifecycleStatus.FAILED.value,
                ]
            )
        )
        if not include_archived:
            inner = inner.where(RequestLogEntity.stats_archived == 0)
        inner = self._apply_request_log_window(
            inner, days=days, offset_days=offset_days, time_zone=time_zone
        )
        inner = self._apply_gateway_key_filter(inner, gateway_key_id=gateway_key_id)
        cache_key = (
            "primary_dimension",
            days,
            offset_days,
            gateway_key_id,
            include_archived,
            date_format,
            str(time_zone),
        )

        async def run_query() -> list[dict[str, float | str]]:
            # ponytail: materialize the per-row jsonb work once; three GROUP BY
            # branches below share the scan instead of recomputing it 3x.
            live = inner.cte("live").prefix_with("MATERIALIZED")

            sums = self._request_log_dimension_sums(live)

            credential_id_col = live.c.credential_id
            credential_name_col = live.c.credential_name
            attempt_channel_id_col = live.c.attempt_channel_id
            attempt_channel_name_col = live.c.attempt_channel_name

            channel_id_col = func.nullif(func.trim(live.c.channel_id), "")
            channel_name_col = func.coalesce(
                func.nullif(func.trim(live.c.channel_name), ""), live.c.channel_id
            )
            resolved_attempt_channel_id = func.coalesce(
                func.nullif(func.trim(attempt_channel_id_col), ""), channel_id_col
            )
            channel_dimension_id = case(
                (
                    and_(
                        credential_id_col.is_not(None),
                        resolved_attempt_channel_id.is_not(None),
                    ),
                    resolved_attempt_channel_id.concat(":").concat(credential_id_col),
                ),
                (credential_id_col.is_not(None), credential_id_col),
                else_=channel_id_col,
            )
            channel_dimension_name = case(
                (
                    credential_id_col.is_not(None),
                    func.coalesce(
                        func.nullif(func.trim(attempt_channel_name_col), ""),
                        channel_name_col,
                        resolved_attempt_channel_id,
                    )
                    .concat(" - ")
                    .concat(
                        func.coalesce(
                            func.nullif(func.trim(credential_name_col), ""),
                            credential_id_col,
                        )
                    ),
                ),
                else_=channel_name_col,
            )
            gateway_key_id_col = func.nullif(
                func.trim(func.coalesce(live.c.gateway_key_id, "")), ""
            )

            channel_branch = (
                select(
                    live.c.bucket.label("date"),
                    literal("channel").label("dimension_type"),
                    channel_dimension_id.label("dimension_id"),
                    func.max(channel_dimension_name).label("dimension_name"),
                    credential_id_col.label("credential_id"),
                    *sums,
                )
                .where(channel_dimension_id.is_not(None))
                .group_by(live.c.bucket, channel_dimension_id, credential_id_col)
            )
            model_branch = (
                select(
                    live.c.bucket.label("date"),
                    literal("model").label("dimension_type"),
                    live.c.model.label("dimension_id"),
                    live.c.model.label("dimension_name"),
                    credential_id_col.label("credential_id"),
                    *sums,
                )
                .where(live.c.model.is_not(None))
                .group_by(live.c.bucket, live.c.model, credential_id_col)
            )
            gateway_key_branch = (
                select(
                    live.c.bucket.label("date"),
                    literal("gateway_key").label("dimension_type"),
                    gateway_key_id_col.label("dimension_id"),
                    literal("").label("dimension_name"),
                    credential_id_col.label("credential_id"),
                    *sums,
                )
                .where(gateway_key_id_col.is_not(None))
                .group_by(live.c.bucket, gateway_key_id_col, credential_id_col)
            )
            stmt = union_all(channel_branch, model_branch, gateway_key_branch)
            rows = (await session.execute(stmt)).all()
            return [self._dimension_sql_row_to_dict(row) for row in rows]

        return await self._cached_live_rows(cache_key, run_query)

    async def _request_log_channel_attempt_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        date_format: str,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[dict[str, float | str]]:
        return await self._cached_live_rows(
            (
                "channel_attempt",
                days,
                offset_days,
                gateway_key_id,
                include_archived,
                date_format,
                str(time_zone),
            ),
            lambda: self._request_log_channel_attempt_query(
                session,
                days=days,
                date_format=date_format,
                offset_days=offset_days,
                gateway_key_id=gateway_key_id,
                include_archived=include_archived,
                time_zone=time_zone,
            ),
        )

    async def _request_log_channel_attempt_query(
        self,
        session: AsyncSession,
        *,
        days: int,
        date_format: str,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[dict[str, float | str]]:
        bucket_expr = self._local_bucket_expr(date_format, time_zone)
        attempts_jsonb = cast(RequestLogEntity.attempts_json, JSONB)
        elements = (
            func.jsonb_array_elements(attempts_jsonb)
            .table_valued("value", with_ordinality="ordinality")
            .lateral()
        )
        attempt_inner = (
            select(
                bucket_expr.label("bucket"),
                func.nullif(
                    func.trim(elements.c.value.op("->>")(literal("channel_id"))),
                    "",
                ).label("dimension_id"),
                func.coalesce(
                    func.nullif(
                        func.trim(elements.c.value.op("->>")(literal("channel_name"))),
                        "",
                    ),
                    elements.c.value.op("->>")(literal("channel_id")),
                ).label("dimension_name"),
                case(
                    (
                        elements.c.value.op("->>")(literal("success"))
                        == literal("true"),
                        literal(1),
                    ),
                    else_=literal(0),
                ).label("success"),
                func.coalesce(
                    cast(
                        elements.c.value.op("->>")(literal("duration_ms")),
                        Float,
                    ),
                    0.0,
                ).label("latency_ms"),
            )
            .select_from(RequestLogEntity)
            .join(elements, literal(True))
            .where(
                RequestLogEntity.lifecycle_status.in_(
                    [
                        RequestLogLifecycleStatus.SUCCEEDED.value,
                        RequestLogLifecycleStatus.FAILED.value,
                    ]
                )
            )
            .where(RequestLogEntity.attempts_json.op("~")(literal(r"^\s*\[")))
        )
        if not include_archived:
            attempt_inner = attempt_inner.where(RequestLogEntity.stats_archived == 0)
        attempt_inner = self._apply_request_log_window(
            attempt_inner,
            days=days,
            offset_days=offset_days,
            time_zone=time_zone,
        )
        attempt_inner = self._apply_gateway_key_filter(
            attempt_inner, gateway_key_id=gateway_key_id
        )
        attempt_sub = attempt_inner.subquery()
        attempt_branch = (
            select(
                attempt_sub.c.bucket.label("date"),
                literal("channel_attempt").label("dimension_type"),
                attempt_sub.c.dimension_id.label("dimension_id"),
                func.max(attempt_sub.c.dimension_name).label("dimension_name"),
                func.count().label("request_count"),
                func.sum(attempt_sub.c.success).label("successful_requests"),
                func.sum(attempt_sub.c.latency_ms).label("latency_ms_sum"),
                literal(0).label("first_token_latency_ms_sum"),
                literal(0).label("input_tokens"),
                literal(0).label("cache_read_input_tokens"),
                literal(0).label("cache_write_input_tokens"),
                literal(0).label("output_tokens"),
                literal(0).label("total_tokens"),
                literal(0).label("input_cost_usd"),
                literal(0).label("output_cost_usd"),
                literal(0).label("total_cost_usd"),
            )
            .where(attempt_sub.c.dimension_id.is_not(None))
            .group_by(attempt_sub.c.bucket, attempt_sub.c.dimension_id)
        )

        fallback_inner = (
            select(
                bucket_expr.label("bucket"),
                func.nullif(func.trim(RequestLogEntity.channel_id), "").label(
                    "dimension_id"
                ),
                func.coalesce(
                    func.nullif(func.trim(RequestLogEntity.channel_name), ""),
                    RequestLogEntity.channel_id,
                ).label("dimension_name"),
                RequestLogEntity.success.label("success"),
                RequestLogEntity.latency_ms.label("latency_ms"),
                RequestLogEntity.first_token_latency_ms.label("first_token_latency_ms"),
            )
            .where(
                RequestLogEntity.lifecycle_status.in_(
                    [
                        RequestLogLifecycleStatus.SUCCEEDED.value,
                        RequestLogLifecycleStatus.FAILED.value,
                    ]
                )
            )
            .where(
                (RequestLogEntity.attempts_json.is_(None))
                | (RequestLogEntity.attempts_json == "[]")
            )
        )
        if not include_archived:
            fallback_inner = fallback_inner.where(RequestLogEntity.stats_archived == 0)
        fallback_inner = self._apply_request_log_window(
            fallback_inner,
            days=days,
            offset_days=offset_days,
            time_zone=time_zone,
        )
        fallback_inner = self._apply_gateway_key_filter(
            fallback_inner, gateway_key_id=gateway_key_id
        )
        fallback_sub = fallback_inner.subquery()
        fallback_branch = (
            select(
                fallback_sub.c.bucket.label("date"),
                literal("channel_attempt").label("dimension_type"),
                fallback_sub.c.dimension_id.label("dimension_id"),
                func.max(fallback_sub.c.dimension_name).label("dimension_name"),
                func.count().label("request_count"),
                func.sum(fallback_sub.c.success).label("successful_requests"),
                func.sum(fallback_sub.c.latency_ms).label("latency_ms_sum"),
                func.sum(fallback_sub.c.first_token_latency_ms).label(
                    "first_token_latency_ms_sum"
                ),
                literal(0).label("input_tokens"),
                literal(0).label("cache_read_input_tokens"),
                literal(0).label("cache_write_input_tokens"),
                literal(0).label("output_tokens"),
                literal(0).label("total_tokens"),
                literal(0).label("input_cost_usd"),
                literal(0).label("output_cost_usd"),
                literal(0).label("total_cost_usd"),
            )
            .where(fallback_sub.c.dimension_id.is_not(None))
            .group_by(fallback_sub.c.bucket, fallback_sub.c.dimension_id)
        )
        stmt = union_all(attempt_branch, fallback_branch)
        rows = (await session.execute(stmt)).all()
        return [self._dimension_sql_row_to_dict(row) for row in rows]

    async def _request_log_dimension_rows(
        self,
        session: AsyncSession,
        *,
        dimension_type: str,
        days: int,
        date_format: str,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[dict[str, float | str]]:
        if dimension_type == "channel_attempt":
            live_rows = await self._request_log_channel_attempt_rows(
                session,
                days=days,
                date_format=date_format,
                offset_days=offset_days,
                gateway_key_id=gateway_key_id,
                include_archived=include_archived,
                time_zone=time_zone,
            )
        else:
            live_rows = await self._request_log_primary_dimension_rows(
                session,
                days=days,
                date_format=date_format,
                offset_days=offset_days,
                gateway_key_id=gateway_key_id,
                include_archived=include_archived,
                time_zone=time_zone,
            )
            cost_multipliers = await self._credential_cost_multipliers_by_id(session)
            gateway_key_names = (
                await self._gateway_key_display_names_by_id(
                    session, [row["dimension_id"] for row in live_rows]
                )
                if dimension_type == "gateway_key"
                else {}
            )
            for row in live_rows:
                multiplier = self._row_cost_multiplier(
                    row.pop("credential_id", None), cost_multipliers
                )
                if multiplier != 1.0:
                    row["input_cost_usd"] = float(row["input_cost_usd"]) * multiplier
                    row["output_cost_usd"] = float(row["output_cost_usd"]) * multiplier
                    row["total_cost_usd"] = float(row["total_cost_usd"]) * multiplier
                if dimension_type == "gateway_key":
                    row["dimension_name"] = self._gateway_key_display_name(
                        row["dimension_id"],
                        gateway_key_names.get(str(row["dimension_id"]), ""),
                    )
        return [row for row in live_rows if row["dimension_type"] == dimension_type]

    @staticmethod
    def _overview_dimension_numeric_fields() -> tuple[str, ...]:
        return (
            "request_count",
            "successful_requests",
            "failed_requests",
            "latency_ms_sum",
            "first_token_latency_ms_sum",
            "input_tokens",
            "cache_read_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "total_tokens",
            "input_cost_usd",
            "output_cost_usd",
            "total_cost_usd",
        )

    @classmethod
    def _dimension_entity_to_row(
        cls, item: OverviewDimensionDailyStatsEntity
    ) -> dict[str, float | str]:
        return {
            "date": item.date,
            "dimension_type": item.dimension_type,
            "dimension_id": item.dimension_id,
            "dimension_name": item.dimension_name,
            "request_count": float(item.request_count),
            "successful_requests": float(item.successful_requests),
            "failed_requests": float(item.failed_requests),
            "latency_ms_sum": float(item.latency_ms_sum),
            "first_token_latency_ms_sum": float(item.first_token_latency_ms_sum),
            "input_tokens": float(item.input_tokens),
            "cache_read_input_tokens": float(item.cache_read_input_tokens),
            "cache_write_input_tokens": float(item.cache_write_input_tokens),
            "output_tokens": float(item.output_tokens),
            "total_tokens": float(item.total_tokens),
            "input_cost_usd": float(item.input_cost_usd),
            "output_cost_usd": float(item.output_cost_usd),
            "total_cost_usd": float(item.total_cost_usd),
        }

    async def _credential_cost_multipliers_by_id(
        self, session: AsyncSession
    ) -> dict[str, float]:
        rows = (
            await session.execute(
                select(SiteCredentialEntity.id, SiteCredentialEntity.cost_multiplier)
            )
        ).all()
        return {
            str(credential_id): max(
                float(1.0 if cost_multiplier is None else cost_multiplier), 0.0
            )
            for credential_id, cost_multiplier in rows
            if credential_id is not None
        }

    async def _adjusted_archived_channel_costs_by_date(
        self,
        session: AsyncSession,
        *,
        days: int,
        time_zone: ZoneInfo,
        offset_days: int = 0,
        exclude_dates: set[str] | None = None,
    ) -> dict[str, dict[str, float]]:
        stmt = select(OverviewDimensionDailyStatsEntity).where(
            OverviewDimensionDailyStatsEntity.dimension_type == "channel"
        )
        start_at, end_at = self._resolve_imported_date_window(
            days, offset_days=offset_days, time_zone=time_zone
        )
        if start_at is not None:
            stmt = stmt.where(OverviewDimensionDailyStatsEntity.date >= start_at)
        if end_at is not None:
            stmt = stmt.where(OverviewDimensionDailyStatsEntity.date < end_at)
        if exclude_dates:
            stmt = stmt.where(
                OverviewDimensionDailyStatsEntity.date.not_in(sorted(exclude_dates))
            )
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            return {}
        cost_multipliers = await self._credential_cost_multipliers_by_id(session)
        buckets: dict[str, dict[str, float]] = {}
        for item in rows:
            multiplier = self._channel_dimension_cost_multiplier(
                item.dimension_id, cost_multipliers
            )
            current = buckets.setdefault(
                item.date,
                {
                    "input_cost_usd": 0.0,
                    "output_cost_usd": 0.0,
                    "total_cost_usd": 0.0,
                },
            )
            current["input_cost_usd"] += float(item.input_cost_usd) * multiplier
            current["output_cost_usd"] += float(item.output_cost_usd) * multiplier
            current["total_cost_usd"] += float(item.total_cost_usd) * multiplier
        return buckets

    @classmethod
    def _aggregate_dimension_rows(
        cls, rows: list[dict[str, float | str]]
    ) -> dict[str, dict[str, float | str]]:
        aggregated: dict[str, dict[str, float | str]] = {}
        numeric_fields = cls._overview_dimension_numeric_fields()
        for row in rows:
            dimension_id = str(row["dimension_id"])
            current = aggregated.get(dimension_id)
            if current is None:
                current = {
                    "id": dimension_id,
                    "name": str(row["dimension_name"] or dimension_id),
                    **{field: 0.0 for field in numeric_fields},
                }
                aggregated[dimension_id] = current
            if not current["name"] or current["name"] == dimension_id:
                current["name"] = str(row["dimension_name"] or dimension_id)
            for field in numeric_fields:
                current[field] = float(current[field]) + float(row[field])
        return aggregated

    @classmethod
    def _dimension_performance_point(
        cls, item: dict[str, float | str]
    ) -> OverviewPerformancePoint:
        request_count = float(item["request_count"])
        latency_sum = float(item["latency_ms_sum"])
        first_token_sum = float(item["first_token_latency_ms_sum"])
        output_tokens = float(item["output_tokens"])
        return OverviewPerformancePoint(
            id=str(item["id"]),
            name=str(item["name"]),
            request_count=int(request_count),
            average_latency_ms=cls._safe_divide(latency_sum, request_count),
            average_first_token_latency_ms=cls._safe_divide(
                first_token_sum, request_count
            ),
            throughput_tokens_per_second=cls._safe_divide(
                output_tokens, latency_sum / 1000
            ),
            total_tokens=int(item["total_tokens"]),
            output_tokens=int(output_tokens),
        )

    @classmethod
    def _dimension_performance_trend_point(
        cls, row: dict[str, float | str]
    ) -> OverviewPerformanceTrendPoint:
        request_count = float(row["request_count"])
        latency_sum = float(row["latency_ms_sum"])
        first_token_sum = float(row["first_token_latency_ms_sum"])
        output_tokens = float(row["output_tokens"])
        return OverviewPerformanceTrendPoint(
            date=str(row["date"]),
            id=str(row["dimension_id"]),
            name=str(row["dimension_name"]),
            request_count=int(request_count),
            average_latency_ms=cls._safe_divide(latency_sum, request_count),
            average_first_token_latency_ms=cls._safe_divide(
                first_token_sum, request_count
            ),
            throughput_tokens_per_second=cls._safe_divide(
                output_tokens, latency_sum / 1000
            ),
        )

    @staticmethod
    def _normalize_overview_dimension_type(dimension_type: str) -> str:
        normalized = dimension_type.strip().lower()
        if normalized not in {"channel", "model", "gateway_key"}:
            raise ValueError(f"Unsupported overview dimension type: {dimension_type}")
        return normalized

    async def _merged_daily_points(
        self,
        session: AsyncSession,
        *,
        days: int,
        time_zone: ZoneInfo,
        offset_days: int = 0,
    ) -> list[OverviewDailyPoint]:
        imported_points = await self._imported_daily_points(
            session, days=days, offset_days=offset_days, time_zone=time_zone
        )
        imported_dates = {item.date for item in imported_points}
        archived_points = await self._archived_daily_points(
            session,
            days=days,
            offset_days=offset_days,
            exclude_dates=imported_dates,
            time_zone=time_zone,
        )
        request_log_points = await self._request_log_daily_points(
            session,
            days=days,
            offset_days=offset_days,
            exclude_dates=imported_dates,
            time_zone=time_zone,
        )
        merged = {item.date: item for item in imported_points}
        for item in archived_points:
            merged[item.date] = item
        for item in request_log_points:
            current = merged.get(item.date)
            if current is None:
                merged[item.date] = item
                continue
            merged[item.date] = OverviewDailyPoint(
                date=item.date,
                request_count=current.request_count + item.request_count,
                input_tokens=current.input_tokens + item.input_tokens,
                cache_read_input_tokens=current.cache_read_input_tokens
                + item.cache_read_input_tokens,
                cache_write_input_tokens=current.cache_write_input_tokens
                + item.cache_write_input_tokens,
                output_tokens=current.output_tokens + item.output_tokens,
                total_tokens=current.total_tokens + item.total_tokens,
                input_cost_usd=current.input_cost_usd + item.input_cost_usd,
                output_cost_usd=current.output_cost_usd + item.output_cost_usd,
                total_cost_usd=current.total_cost_usd + item.total_cost_usd,
                wait_time_ms=current.wait_time_ms + item.wait_time_ms,
                successful_requests=current.successful_requests
                + item.successful_requests,
                failed_requests=current.failed_requests + item.failed_requests,
            )
        return [merged[date] for date in sorted(merged)]

    async def _imported_daily_points(
        self,
        session: AsyncSession,
        *,
        days: int,
        time_zone: ZoneInfo,
        offset_days: int = 0,
    ) -> list[OverviewDailyPoint]:
        stmt = select(ImportedStatsDailyEntity).order_by(
            ImportedStatsDailyEntity.date.asc()
        )
        start_at, end_at = self._resolve_imported_date_window(
            days, offset_days=offset_days, time_zone=time_zone
        )
        if start_at is not None and end_at is not None:
            stmt = stmt.where(ImportedStatsDailyEntity.date >= start_at).where(
                ImportedStatsDailyEntity.date < end_at
            )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            OverviewDailyPoint(
                date=item.date,
                request_count=int(item.request_success + item.request_failed),
                input_tokens=int(item.input_token),
                output_tokens=int(item.output_token),
                total_tokens=int(item.input_token + item.output_token),
                input_cost_usd=float(item.input_cost),
                output_cost_usd=float(item.output_cost),
                total_cost_usd=float(item.input_cost + item.output_cost),
                wait_time_ms=int(item.wait_time),
                successful_requests=int(item.request_success),
                failed_requests=int(item.request_failed),
            )
            for item in rows
        ]

    async def _archived_daily_points(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        exclude_dates: set[str] | None = None,
        time_zone: ZoneInfo,
    ) -> list[OverviewDailyPoint]:
        stmt = select(RequestLogDailyStatsEntity).order_by(
            RequestLogDailyStatsEntity.date.asc()
        )
        start_at, end_at = self._resolve_imported_date_window(
            days, offset_days=offset_days, time_zone=time_zone
        )
        if start_at is not None and end_at is not None:
            stmt = stmt.where(RequestLogDailyStatsEntity.date >= start_at).where(
                RequestLogDailyStatsEntity.date < end_at
            )
        if exclude_dates:
            stmt = stmt.where(
                RequestLogDailyStatsEntity.date.not_in(sorted(exclude_dates))
            )
        rows = (await session.execute(stmt)).scalars().all()
        adjusted_costs_by_date = await self._adjusted_archived_channel_costs_by_date(
            session,
            days=days,
            offset_days=offset_days,
            exclude_dates=exclude_dates,
            time_zone=time_zone,
        )
        return [
            OverviewDailyPoint(
                date=item.date,
                request_count=int(item.request_count),
                input_tokens=int(item.input_tokens),
                cache_read_input_tokens=int(item.cache_read_input_tokens),
                cache_write_input_tokens=int(item.cache_write_input_tokens),
                output_tokens=int(item.output_tokens),
                total_tokens=int(item.total_tokens),
                input_cost_usd=float(
                    adjusted_costs_by_date.get(item.date, {}).get(
                        "input_cost_usd", item.input_cost_usd
                    )
                ),
                output_cost_usd=float(
                    adjusted_costs_by_date.get(item.date, {}).get(
                        "output_cost_usd", item.output_cost_usd
                    )
                ),
                total_cost_usd=float(
                    adjusted_costs_by_date.get(item.date, {}).get(
                        "total_cost_usd", item.total_cost_usd
                    )
                ),
                wait_time_ms=int(item.wait_time_ms),
                successful_requests=int(item.successful_requests),
                failed_requests=int(item.failed_requests),
            )
            for item in rows
        ]

    async def _request_log_daily_points(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        exclude_dates: set[str] | None = None,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[OverviewDailyPoint]:
        rows = await self._live_daily_sum_rows(
            session,
            days=days,
            offset_days=offset_days,
            gateway_key_id=gateway_key_id,
            include_archived=include_archived,
            time_zone=time_zone,
        )
        cost_multipliers = await self._credential_cost_multipliers_by_id(session)
        buckets: dict[str, dict[str, float]] = {}
        for row in rows:
            if exclude_dates and row["date"] in exclude_dates:
                continue
            multiplier = self._row_cost_multiplier(
                row["credential_id"], cost_multipliers
            )
            current = buckets.setdefault(row["date"], self._daily_bucket_zero())
            current["request_count"] += float(row["request_count"])
            current["successful_requests"] += float(row["successful_requests"])
            current["failed_requests"] += float(
                row["request_count"] - row["successful_requests"]
            )
            current["wait_time_ms"] += float(row["wait_time_ms"])
            current["input_tokens"] += float(row["input_tokens"])
            current["cache_read_input_tokens"] += float(row["cache_read_input_tokens"])
            current["cache_write_input_tokens"] += float(
                row["cache_write_input_tokens"]
            )
            current["output_tokens"] += float(row["output_tokens"])
            current["total_tokens"] += float(row["total_tokens"])
            current["input_cost_usd"] += float(row["input_cost_usd"]) * multiplier
            current["output_cost_usd"] += float(row["output_cost_usd"]) * multiplier
            current["total_cost_usd"] += float(row["total_cost_usd"]) * multiplier
        points: list[OverviewDailyPoint] = []
        for date_value, values in sorted(buckets.items()):
            total_value = int(values["request_count"])
            success_value = int(values["successful_requests"])
            points.append(
                OverviewDailyPoint(
                    date=date_value,
                    request_count=total_value,
                    input_tokens=int(values["input_tokens"]),
                    cache_read_input_tokens=int(values["cache_read_input_tokens"]),
                    cache_write_input_tokens=int(values["cache_write_input_tokens"]),
                    output_tokens=int(values["output_tokens"]),
                    total_tokens=int(values["total_tokens"]),
                    input_cost_usd=float(values["input_cost_usd"]),
                    output_cost_usd=float(values["output_cost_usd"]),
                    total_cost_usd=float(values["total_cost_usd"]),
                    wait_time_ms=int(values["wait_time_ms"]),
                    successful_requests=success_value,
                    failed_requests=max(total_value - success_value, 0),
                )
            )
        return points

    async def _request_log_totals_excluding_imported_days(
        self, session: AsyncSession, *, time_zone: ZoneInfo
    ) -> dict[str, float]:
        imported_dates = {
            row[0]
            for row in (
                await session.execute(select(ImportedStatsDailyEntity.date))
            ).all()
        }
        archived_totals = await self._archived_period_totals(
            session, days=0, exclude_dates=imported_dates, time_zone=time_zone
        )
        live_totals = await self._request_log_period_totals(
            session, days=0, exclude_dates=imported_dates, time_zone=time_zone
        )
        return {
            "request_count": archived_totals["request_count"]
            + live_totals["request_count"],
            "wait_time_ms": archived_totals["wait_time_ms"]
            + live_totals["wait_time_ms"],
            "input_tokens": archived_totals["input_tokens"]
            + live_totals["input_tokens"],
            "cache_read_input_tokens": archived_totals["cache_read_input_tokens"]
            + live_totals["cache_read_input_tokens"],
            "cache_write_input_tokens": archived_totals["cache_write_input_tokens"]
            + live_totals["cache_write_input_tokens"],
            "output_tokens": archived_totals["output_tokens"]
            + live_totals["output_tokens"],
            "input_cost_usd": archived_totals["input_cost_usd"]
            + live_totals["input_cost_usd"],
            "output_cost_usd": archived_totals["output_cost_usd"]
            + live_totals["output_cost_usd"],
            "total_cost_usd": archived_totals["total_cost_usd"]
            + live_totals["total_cost_usd"],
            "successful_requests": archived_totals["successful_requests"]
            + live_totals["successful_requests"],
            "failed_requests": archived_totals["failed_requests"]
            + live_totals["failed_requests"],
        }

    async def _archived_period_totals(
        self,
        session: AsyncSession,
        *,
        days: int,
        time_zone: ZoneInfo,
        offset_days: int = 0,
        exclude_dates: set[str] | None = None,
    ) -> dict[str, float]:
        stmt = select(RequestLogDailyStatsEntity).order_by(
            RequestLogDailyStatsEntity.date.asc()
        )
        start_at, end_at = self._resolve_imported_date_window(
            days, offset_days=offset_days, time_zone=time_zone
        )
        if start_at is not None:
            stmt = stmt.where(RequestLogDailyStatsEntity.date >= start_at)
        if end_at is not None:
            stmt = stmt.where(RequestLogDailyStatsEntity.date < end_at)
        if exclude_dates:
            stmt = stmt.where(
                RequestLogDailyStatsEntity.date.not_in(sorted(exclude_dates))
            )
        rows = (await session.execute(stmt)).scalars().all()
        adjusted_costs_by_date = await self._adjusted_archived_channel_costs_by_date(
            session,
            days=days,
            offset_days=offset_days,
            exclude_dates=exclude_dates,
            time_zone=time_zone,
        )
        totals = self._zero_totals()
        for item in rows:
            costs = adjusted_costs_by_date.get(item.date, {})
            totals["request_count"] += float(item.request_count)
            totals["wait_time_ms"] += float(item.wait_time_ms)
            totals["input_tokens"] += float(item.input_tokens)
            totals["cache_read_input_tokens"] += float(item.cache_read_input_tokens)
            totals["cache_write_input_tokens"] += float(item.cache_write_input_tokens)
            totals["output_tokens"] += float(item.output_tokens)
            totals["input_cost_usd"] += float(
                costs.get("input_cost_usd", item.input_cost_usd)
            )
            totals["output_cost_usd"] += float(
                costs.get("output_cost_usd", item.output_cost_usd)
            )
            totals["total_cost_usd"] += float(
                costs.get("total_cost_usd", item.total_cost_usd)
            )
            totals["successful_requests"] += float(item.successful_requests)
            totals["failed_requests"] += float(item.failed_requests)
        return totals

    async def _overview_model_daily_rows(
        self,
        session: AsyncSession,
        *,
        start_at: str | None,
        end_at: str | None,
    ) -> list[tuple[str, str, int, int, float]]:
        stmt = select(
            OverviewModelDailyStatsEntity.date,
            OverviewModelDailyStatsEntity.model,
            OverviewModelDailyStatsEntity.requests,
            OverviewModelDailyStatsEntity.total_tokens,
            OverviewModelDailyStatsEntity.total_cost_usd,
        )
        if start_at is not None:
            stmt = stmt.where(OverviewModelDailyStatsEntity.date >= start_at)
        if end_at is not None:
            stmt = stmt.where(OverviewModelDailyStatsEntity.date < end_at)
        rows = (
            await session.execute(
                stmt.order_by(OverviewModelDailyStatsEntity.date.asc())
            )
        ).all()
        return [
            (
                str(date_value),
                str(model),
                int(requests),
                int(total_tokens),
                float(total_cost),
            )
            for date_value, model, requests, total_tokens, total_cost in rows
        ]

    async def _request_log_model_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        format_text: str,
        time_zone: ZoneInfo,
    ) -> list[tuple[str, str, int, int, float]]:
        model_expr = func.nullif(func.trim(RequestLogEntity.upstream_model_name), "")
        bucket_expr = self._local_bucket_expr(format_text, time_zone)
        credential_id_expr = self._primary_attempt_credential_id_expr()
        inner = (
            select(
                bucket_expr.label("bucket"),
                model_expr.label("model"),
                credential_id_expr.label("credential_id"),
                RequestLogEntity.total_tokens,
                RequestLogEntity.total_cost_usd,
            )
            .where(RequestLogEntity.success == 1)
            .where(
                RequestLogEntity.lifecycle_status
                == RequestLogLifecycleStatus.SUCCEEDED.value
            )
            .where(model_expr.is_not(None))
        )
        if not include_archived:
            inner = inner.where(RequestLogEntity.stats_archived == 0)
        inner = self._apply_request_log_window(
            inner, days=days, offset_days=offset_days, time_zone=time_zone
        )
        inner = self._apply_gateway_key_filter(inner, gateway_key_id=gateway_key_id)
        sub = inner.subquery()
        stmt = select(
            sub.c.bucket,
            sub.c.model,
            sub.c.credential_id,
            func.count().label("requests"),
            func.sum(sub.c.total_tokens).label("total_tokens"),
            func.sum(sub.c.total_cost_usd).label("total_cost_usd"),
        ).group_by(sub.c.bucket, sub.c.model, sub.c.credential_id)
        rows = (await session.execute(stmt)).all()
        cost_multipliers = await self._credential_cost_multipliers_by_id(session)
        buckets: dict[tuple[str, str], list[float]] = {}
        for row in rows:
            multiplier = self._row_cost_multiplier(row.credential_id, cost_multipliers)
            key = (str(row.bucket), str(row.model))
            current = buckets.setdefault(key, [0.0, 0.0, 0.0])
            current[0] += float(row.requests)
            current[1] += float(row.total_tokens or 0)
            current[2] += float(row.total_cost_usd or 0) * multiplier
        return [
            (date_value, model, int(values[0]), int(values[1]), float(values[2]))
            for (date_value, model), values in sorted(buckets.items())
        ]

    async def _request_log_model_daily_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[tuple[str, str, int, int, float]]:
        return await self._request_log_model_rows(
            session,
            days=days,
            offset_days=offset_days,
            gateway_key_id=gateway_key_id,
            include_archived=include_archived,
            format_text="%Y%m%d",
            time_zone=time_zone,
        )

    async def _request_log_model_hourly_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[tuple[str, str, int, int, float]]:
        return await self._request_log_model_rows(
            session,
            days=days,
            offset_days=offset_days,
            gateway_key_id=gateway_key_id,
            include_archived=include_archived,
            format_text="%Y%m%d%H",
            time_zone=time_zone,
        )

    async def _overview_channel_daily_rows(
        self,
        session: AsyncSession,
        *,
        start_at: str | None,
        end_at: str | None,
    ) -> list[tuple[str, str, str, int, int, float]]:
        stmt = select(
            OverviewChannelDailyStatsEntity.date,
            OverviewChannelDailyStatsEntity.channel_id,
            OverviewChannelDailyStatsEntity.channel_name,
            OverviewChannelDailyStatsEntity.requests,
            OverviewChannelDailyStatsEntity.total_tokens,
            OverviewChannelDailyStatsEntity.total_cost_usd,
        )
        if start_at is not None:
            stmt = stmt.where(OverviewChannelDailyStatsEntity.date >= start_at)
        if end_at is not None:
            stmt = stmt.where(OverviewChannelDailyStatsEntity.date < end_at)
        rows = (
            await session.execute(
                stmt.order_by(OverviewChannelDailyStatsEntity.date.asc())
            )
        ).all()
        return [
            (
                str(date_value),
                str(channel_id),
                str(channel_name or ""),
                int(requests),
                int(total_tokens),
                float(total_cost),
            )
            for (
                date_value,
                channel_id,
                channel_name,
                requests,
                total_tokens,
                total_cost,
            ) in rows
        ]

    async def _request_log_channel_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        format_text: str,
        time_zone: ZoneInfo,
    ) -> list[tuple[str, str, str, int, int, float]]:
        channel_id_expr = func.coalesce(RequestLogEntity.channel_id, literal("n/a"))
        channel_name_expr = func.coalesce(
            func.nullif(func.trim(RequestLogEntity.channel_name), ""),
            RequestLogEntity.channel_id,
            literal("n/a"),
        )
        bucket_expr = self._local_bucket_expr(format_text, time_zone)
        credential_id_expr = self._primary_attempt_credential_id_expr()
        inner = (
            select(
                bucket_expr.label("bucket"),
                channel_id_expr.label("channel_id"),
                channel_name_expr.label("channel_name"),
                credential_id_expr.label("credential_id"),
                RequestLogEntity.total_tokens,
                RequestLogEntity.total_cost_usd,
            )
            .where(RequestLogEntity.success == 1)
            .where(
                RequestLogEntity.lifecycle_status
                == RequestLogLifecycleStatus.SUCCEEDED.value
            )
            .where(RequestLogEntity.channel_id.is_not(None))
        )
        if not include_archived:
            inner = inner.where(RequestLogEntity.stats_archived == 0)
        inner = self._apply_request_log_window(
            inner, days=days, offset_days=offset_days, time_zone=time_zone
        )
        inner = self._apply_gateway_key_filter(inner, gateway_key_id=gateway_key_id)
        sub = inner.subquery()
        stmt = select(
            sub.c.bucket,
            sub.c.channel_id,
            sub.c.credential_id,
            func.count().label("requests"),
            func.sum(sub.c.total_tokens).label("total_tokens"),
            func.sum(sub.c.total_cost_usd).label("total_cost_usd"),
            func.max(sub.c.channel_name).label("channel_name"),
        ).group_by(sub.c.bucket, sub.c.channel_id, sub.c.credential_id)
        rows = (await session.execute(stmt)).all()
        cost_multipliers = await self._credential_cost_multipliers_by_id(session)
        buckets: dict[tuple[str, str], list[Any]] = {}
        for row in rows:
            multiplier = self._row_cost_multiplier(row.credential_id, cost_multipliers)
            key = (str(row.bucket), str(row.channel_id))
            current = buckets.setdefault(key, [0.0, 0.0, 0.0, ""])
            current[0] += float(row.requests)
            current[1] += float(row.total_tokens or 0)
            current[2] += float(row.total_cost_usd or 0) * multiplier
            if row.channel_name and not current[3]:
                current[3] = str(row.channel_name)
        return [
            (
                date_value,
                channel_id,
                str(values[3]),
                int(values[0]),
                int(values[1]),
                float(values[2]),
            )
            for (date_value, channel_id), values in sorted(buckets.items())
        ]

    async def _request_log_channel_daily_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[tuple[str, str, str, int, int, float]]:
        return await self._request_log_channel_rows(
            session,
            days=days,
            offset_days=offset_days,
            gateway_key_id=gateway_key_id,
            include_archived=include_archived,
            format_text="%Y%m%d",
            time_zone=time_zone,
        )

    async def _request_log_channel_hourly_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[tuple[str, str, str, int, int, float]]:
        return await self._request_log_channel_rows(
            session,
            days=days,
            offset_days=offset_days,
            gateway_key_id=gateway_key_id,
            include_archived=include_archived,
            format_text="%Y%m%d%H",
            time_zone=time_zone,
        )

    async def _merged_period_totals(
        self,
        session: AsyncSession,
        *,
        days: int,
        time_zone: ZoneInfo,
        offset_days: int = 0,
    ) -> dict[str, float]:
        imported_totals = await self._imported_period_totals(
            session, days=days, offset_days=offset_days, time_zone=time_zone
        )
        archived_totals = await self._archived_period_totals(
            session,
            days=days,
            offset_days=offset_days,
            exclude_dates=imported_totals["covered_dates"],
            time_zone=time_zone,
        )
        request_log_totals = await self._request_log_period_totals(
            session,
            days=days,
            offset_days=offset_days,
            exclude_dates=imported_totals["covered_dates"],
            time_zone=time_zone,
        )
        return {
            "request_count": imported_totals["request_count"]
            + archived_totals["request_count"]
            + request_log_totals["request_count"],
            "wait_time_ms": imported_totals["wait_time_ms"]
            + archived_totals["wait_time_ms"]
            + request_log_totals["wait_time_ms"],
            "input_tokens": imported_totals["input_tokens"]
            + archived_totals["input_tokens"]
            + request_log_totals["input_tokens"],
            "cache_read_input_tokens": imported_totals["cache_read_input_tokens"]
            + archived_totals["cache_read_input_tokens"]
            + request_log_totals["cache_read_input_tokens"],
            "cache_write_input_tokens": imported_totals["cache_write_input_tokens"]
            + archived_totals["cache_write_input_tokens"]
            + request_log_totals["cache_write_input_tokens"],
            "output_tokens": imported_totals["output_tokens"]
            + archived_totals["output_tokens"]
            + request_log_totals["output_tokens"],
            "input_cost_usd": imported_totals["input_cost_usd"]
            + archived_totals["input_cost_usd"]
            + request_log_totals["input_cost_usd"],
            "output_cost_usd": imported_totals["output_cost_usd"]
            + archived_totals["output_cost_usd"]
            + request_log_totals["output_cost_usd"],
            "total_cost_usd": imported_totals["total_cost_usd"]
            + archived_totals["total_cost_usd"]
            + request_log_totals["total_cost_usd"],
            "successful_requests": imported_totals["successful_requests"]
            + archived_totals["successful_requests"]
            + request_log_totals["successful_requests"],
            "failed_requests": imported_totals["failed_requests"]
            + archived_totals["failed_requests"]
            + request_log_totals["failed_requests"],
        }

    async def _imported_period_totals(
        self,
        session: AsyncSession,
        *,
        days: int,
        time_zone: ZoneInfo,
        offset_days: int = 0,
    ) -> dict[str, float | set[str]]:
        if days == 0:
            imported_total = await session.get(ImportedStatsTotalEntity, 1)
            covered_dates = {
                row[0]
                for row in (
                    await session.execute(select(ImportedStatsDailyEntity.date))
                ).all()
            }
            if imported_total is None:
                return {
                    "request_count": 0.0,
                    "wait_time_ms": 0.0,
                    "input_tokens": 0.0,
                    "cache_read_input_tokens": 0.0,
                    "cache_write_input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "input_cost_usd": 0.0,
                    "output_cost_usd": 0.0,
                    "total_cost_usd": 0.0,
                    "successful_requests": 0.0,
                    "failed_requests": 0.0,
                    "covered_dates": covered_dates,
                }
            return {
                "request_count": float(
                    imported_total.request_success + imported_total.request_failed
                ),
                "wait_time_ms": float(imported_total.wait_time),
                "input_tokens": float(imported_total.input_token),
                "cache_read_input_tokens": 0.0,
                "cache_write_input_tokens": 0.0,
                "output_tokens": float(imported_total.output_token),
                "input_cost_usd": float(imported_total.input_cost),
                "output_cost_usd": float(imported_total.output_cost),
                "total_cost_usd": float(
                    imported_total.input_cost + imported_total.output_cost
                ),
                "successful_requests": float(imported_total.request_success),
                "failed_requests": float(imported_total.request_failed),
                "covered_dates": covered_dates,
            }

        start_at, end_at = self._resolve_imported_date_window(
            days, offset_days=offset_days, time_zone=time_zone
        )
        rows = (
            (
                await session.execute(
                    select(ImportedStatsDailyEntity)
                    .where(ImportedStatsDailyEntity.date >= start_at)
                    .where(ImportedStatsDailyEntity.date < end_at)
                )
            )
            .scalars()
            .all()
        )
        covered_dates = {item.date for item in rows}
        return {
            "request_count": float(
                sum(item.request_success + item.request_failed for item in rows)
            ),
            "wait_time_ms": float(sum(item.wait_time for item in rows)),
            "input_tokens": float(sum(item.input_token for item in rows)),
            "cache_read_input_tokens": 0.0,
            "cache_write_input_tokens": 0.0,
            "output_tokens": float(sum(item.output_token for item in rows)),
            "input_cost_usd": float(sum(item.input_cost for item in rows)),
            "output_cost_usd": float(sum(item.output_cost for item in rows)),
            "total_cost_usd": float(
                sum(item.input_cost + item.output_cost for item in rows)
            ),
            "successful_requests": float(sum(item.request_success for item in rows)),
            "failed_requests": float(sum(item.request_failed for item in rows)),
            "covered_dates": covered_dates,
        }

    @staticmethod
    def _local_bucket_expr(format_text: str, time_zone: ZoneInfo) -> Any:
        # ponytail: bucket by PostgreSQL tzdata instead of Python; exact for
        # fixed-offset zones, DST handled by the database. to_char uses
        # PostgreSQL templates, not strftime.
        pg_format = (
            format_text.replace("%Y", "YYYY")
            .replace("%m", "MM")
            .replace("%d", "DD")
            .replace("%H", "HH24")
        )
        utc_ts = RequestLogEntity.created_at.op("AT TIME ZONE")(literal("UTC"))
        local_ts = utc_ts.op("AT TIME ZONE")(literal(str(time_zone)))
        return func.to_char(local_ts, pg_format)

    @staticmethod
    def _primary_attempt_expr() -> Any:
        # ponytail: attempts_json is always json.dumps output, so the regex guard
        # only shields legacy rows; full JSON validation would need a SQL function.
        attempts_jsonb = cast(RequestLogEntity.attempts_json, JSONB)
        elements = func.jsonb_array_elements(attempts_jsonb).table_valued(
            "value", with_ordinality="ordinality"
        )
        last_success = (
            select(elements.c.value)
            .where(elements.c.value.op("->>")(literal("success")) == literal("true"))
            .order_by(elements.c.ordinality.desc())
            .limit(1)
            .scalar_subquery()
        )
        last_element = attempts_jsonb.op("->")(literal(-1))
        is_json_array = RequestLogEntity.attempts_json.op("~")(literal(r"^\s*\["))
        has_elements = and_(is_json_array, RequestLogEntity.attempts_json != "[]")
        return case(
            (has_elements, func.coalesce(last_success, last_element)),
            else_=None,
        )

    @classmethod
    def _primary_attempt_field_expr(cls, field: str) -> Any:
        # ponytail: precomputed column first; the jsonb fallback only evaluates
        # for legacy rows where the column is NULL (COALESCE short-circuits).
        column = getattr(RequestLogEntity, f"primary_{field}")
        jsonb_field = {
            "credential_id": "credential_id",
            "credential_name": "credential_name",
            "attempt_channel_id": "channel_id",
            "attempt_channel_name": "channel_name",
        }[field]
        fallback = cls._primary_attempt_expr().op("->>")(literal(jsonb_field))
        return func.coalesce(column, fallback)

    @classmethod
    def _primary_attempt_credential_id_expr(cls) -> Any:
        return cls._primary_attempt_field_expr("credential_id")

    @classmethod
    def _primary_attempt_credential_name_expr(cls) -> Any:
        return cls._primary_attempt_field_expr("credential_name")

    @staticmethod
    def _row_cost_multiplier(
        credential_id: object, cost_multipliers: dict[str, float]
    ) -> float:
        if not cost_multipliers or credential_id is None:
            return 1.0
        return max(float(cost_multipliers.get(str(credential_id).strip(), 1.0)), 0.0)

    async def _live_daily_sum_rows(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[dict[str, Any]]:
        return await self._cached_live_rows(
            (
                "daily",
                days,
                offset_days,
                gateway_key_id,
                include_archived,
                str(time_zone),
            ),
            lambda: self._live_daily_sum_query(
                session,
                days=days,
                offset_days=offset_days,
                gateway_key_id=gateway_key_id,
                include_archived=include_archived,
                time_zone=time_zone,
            ),
        )

    async def _live_daily_sum_query(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> list[dict[str, Any]]:
        """Aggregate live request-log rows per local day in SQL (never Python)."""
        bucket_expr = self._local_bucket_expr("%Y%m%d", time_zone)
        credential_expr = self._primary_attempt_credential_id_expr()
        inner = select(
            bucket_expr.label("date"),
            credential_expr.label("credential_id"),
            RequestLogEntity.success,
            RequestLogEntity.latency_ms,
            RequestLogEntity.input_tokens,
            RequestLogEntity.cache_read_input_tokens,
            RequestLogEntity.cache_write_input_tokens,
            RequestLogEntity.output_tokens,
            RequestLogEntity.total_tokens,
            RequestLogEntity.input_cost_usd,
            RequestLogEntity.output_cost_usd,
            RequestLogEntity.total_cost_usd,
        ).select_from(RequestLogEntity)
        if not include_archived:
            inner = inner.where(RequestLogEntity.stats_archived == 0)
        inner = self._apply_request_log_window(
            inner, days=days, offset_days=offset_days, time_zone=time_zone
        )
        inner = self._apply_gateway_key_filter(inner, gateway_key_id=gateway_key_id)
        sub = inner.subquery()
        stmt = select(
            sub.c.date,
            sub.c.credential_id,
            func.count().label("request_count"),
            func.sum(sub.c.success).label("successful_requests"),
            func.sum(sub.c.latency_ms).label("wait_time_ms"),
            func.sum(sub.c.input_tokens).label("input_tokens"),
            func.sum(sub.c.cache_read_input_tokens).label("cache_read_input_tokens"),
            func.sum(sub.c.cache_write_input_tokens).label("cache_write_input_tokens"),
            func.sum(sub.c.output_tokens).label("output_tokens"),
            func.sum(sub.c.total_tokens).label("total_tokens"),
            func.sum(sub.c.input_cost_usd).label("input_cost_usd"),
            func.sum(sub.c.output_cost_usd).label("output_cost_usd"),
            func.sum(sub.c.total_cost_usd).label("total_cost_usd"),
        ).group_by(sub.c.date, sub.c.credential_id)
        rows = (await session.execute(stmt)).all()
        return [
            {
                "date": str(row.date),
                "credential_id": row.credential_id,
                "request_count": int(row.request_count),
                "successful_requests": int(row.successful_requests or 0),
                "wait_time_ms": int(row.wait_time_ms or 0),
                "input_tokens": int(row.input_tokens or 0),
                "cache_read_input_tokens": int(row.cache_read_input_tokens or 0),
                "cache_write_input_tokens": int(row.cache_write_input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "input_cost_usd": float(row.input_cost_usd or 0),
                "output_cost_usd": float(row.output_cost_usd or 0),
                "total_cost_usd": float(row.total_cost_usd or 0),
            }
            for row in rows
        ]

    @classmethod
    def _daily_bucket_zero(cls) -> dict[str, float]:
        return {
            "request_count": 0.0,
            "successful_requests": 0.0,
            "failed_requests": 0.0,
            "wait_time_ms": 0.0,
            "input_tokens": 0.0,
            "cache_read_input_tokens": 0.0,
            "cache_write_input_tokens": 0.0,
            "output_tokens": 0.0,
            "total_tokens": 0.0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
        }

    async def _request_log_period_totals(
        self,
        session: AsyncSession,
        *,
        days: int,
        offset_days: int = 0,
        exclude_dates: set[str] | None = None,
        gateway_key_id: str | None = None,
        include_archived: bool = False,
        time_zone: ZoneInfo,
    ) -> dict[str, float]:
        rows = await self._live_daily_sum_rows(
            session,
            days=days,
            offset_days=offset_days,
            gateway_key_id=gateway_key_id,
            include_archived=include_archived,
            time_zone=time_zone,
        )
        cost_multipliers = await self._credential_cost_multipliers_by_id(session)
        totals = self._zero_totals()
        for row in rows:
            if exclude_dates and row["date"] in exclude_dates:
                continue
            multiplier = self._row_cost_multiplier(
                row["credential_id"], cost_multipliers
            )
            totals["request_count"] += float(row["request_count"])
            totals["wait_time_ms"] += float(row["wait_time_ms"])
            totals["input_tokens"] += float(row["input_tokens"])
            totals["cache_read_input_tokens"] += float(row["cache_read_input_tokens"])
            totals["cache_write_input_tokens"] += float(row["cache_write_input_tokens"])
            totals["output_tokens"] += float(row["output_tokens"])
            totals["input_cost_usd"] += float(row["input_cost_usd"]) * multiplier
            totals["output_cost_usd"] += float(row["output_cost_usd"]) * multiplier
            totals["total_cost_usd"] += float(row["total_cost_usd"]) * multiplier
            totals["successful_requests"] += float(row["successful_requests"])
            totals["failed_requests"] += float(
                row["request_count"] - row["successful_requests"]
            )
        return totals

    @staticmethod
    def _zero_totals() -> dict[str, float]:
        return {
            "request_count": 0.0,
            "wait_time_ms": 0.0,
            "input_tokens": 0.0,
            "cache_read_input_tokens": 0.0,
            "cache_write_input_tokens": 0.0,
            "output_tokens": 0.0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "total_cost_usd": 0.0,
            "successful_requests": 0.0,
            "failed_requests": 0.0,
        }

    @staticmethod
    def _to_utc_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _request_log_prune_cutoff(*, keep_days: int, time_zone: ZoneInfo) -> datetime:
        local_now = datetime.now(time_zone)
        local_cutoff = local_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=max(keep_days, 1) - 1)
        return local_cutoff.astimezone(UTC).replace(tzinfo=None)

    @classmethod
    def _daily_stats_by_local_bucket(
        cls,
        rows: list[Any],
        time_zone: ZoneInfo,
        *,
        cost_multipliers: dict[str, float] | None = None,
    ) -> dict[str, dict[str, float]]:
        buckets: dict[str, dict[str, float]] = {}
        for row in rows:
            (
                created_at,
                success,
                latency_ms,
                input_tokens,
                cache_read_input_tokens,
                cache_write_input_tokens,
                output_tokens,
                total_tokens,
                input_cost_usd,
                output_cost_usd,
                total_cost_usd,
                *extra_values,
            ) = tuple(row)
            utc_created_at = cls._to_utc_datetime(created_at)
            if utc_created_at is None:
                continue
            date_value = utc_created_at.astimezone(time_zone).strftime("%Y%m%d")
            current = buckets.setdefault(
                date_value,
                {
                    "request_count": 0.0,
                    "successful_requests": 0.0,
                    "failed_requests": 0.0,
                    "wait_time_ms": 0.0,
                    "input_tokens": 0.0,
                    "cache_read_input_tokens": 0.0,
                    "cache_write_input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "total_tokens": 0.0,
                    "input_cost_usd": 0.0,
                    "output_cost_usd": 0.0,
                    "total_cost_usd": 0.0,
                },
            )
            success_value = 1.0 if int(success) else 0.0
            current["request_count"] += 1.0
            current["successful_requests"] += success_value
            current["failed_requests"] += 0.0 if success_value else 1.0
            current["wait_time_ms"] += float(latency_ms)
            current["input_tokens"] += float(input_tokens)
            current["cache_read_input_tokens"] += float(cache_read_input_tokens)
            current["cache_write_input_tokens"] += float(cache_write_input_tokens)
            current["output_tokens"] += float(output_tokens)
            current["total_tokens"] += float(total_tokens)
            multiplier = cls._cost_multiplier_from_attempts_json(
                extra_values[0] if extra_values else None, cost_multipliers
            )
            current["input_cost_usd"] += float(input_cost_usd) * multiplier
            current["output_cost_usd"] += float(output_cost_usd) * multiplier
            current["total_cost_usd"] += float(total_cost_usd) * multiplier
        return buckets

    @classmethod
    def _model_rows_by_local_bucket(
        cls,
        rows: list[Any],
        format_text: str,
        time_zone: ZoneInfo,
        *,
        cost_multipliers: dict[str, float] | None = None,
    ) -> list[tuple[str, str, int, int, float]]:
        buckets: dict[tuple[str, str], list[float]] = {}
        for row in rows:
            created_at, model, total_tokens, total_cost, *extra_values = tuple(row)
            if not model or created_at is None:
                continue
            utc_created_at = cls._to_utc_datetime(created_at)
            if utc_created_at is None:
                continue
            bucket = utc_created_at.astimezone(time_zone).strftime(format_text)
            key = (bucket, str(model))
            current = buckets.setdefault(key, [0.0, 0.0, 0.0])
            multiplier = cls._cost_multiplier_from_attempts_json(
                extra_values[0] if extra_values else None, cost_multipliers
            )
            current[0] += 1
            current[1] += float(total_tokens)
            current[2] += float(total_cost) * multiplier
        return [
            (date_value, model, int(values[0]), int(values[1]), float(values[2]))
            for (date_value, model), values in sorted(buckets.items())
        ]

    @classmethod
    def _channel_rows_by_local_bucket(
        cls,
        rows: list[Any],
        format_text: str,
        time_zone: ZoneInfo,
        *,
        cost_multipliers: dict[str, float] | None = None,
    ) -> list[tuple[str, str, str, int, int, float]]:
        buckets: dict[tuple[str, str], list[Any]] = {}
        for row in rows:
            (
                created_at,
                channel_id,
                channel_name,
                total_tokens,
                total_cost,
                *extra_values,
            ) = tuple(row)
            if not channel_id or created_at is None:
                continue
            utc_created_at = cls._to_utc_datetime(created_at)
            if utc_created_at is None:
                continue
            bucket = utc_created_at.astimezone(time_zone).strftime(format_text)
            key = (bucket, str(channel_id))
            current = buckets.setdefault(key, [0.0, 0.0, 0.0, ""])
            multiplier = cls._cost_multiplier_from_attempts_json(
                extra_values[0] if extra_values else None, cost_multipliers
            )
            current[0] += 1
            current[1] += float(total_tokens)
            current[2] += float(total_cost) * multiplier
            if channel_name and not current[3]:
                current[3] = str(channel_name)
        return [
            (
                date_value,
                channel_id,
                str(values[3]),
                int(values[0]),
                int(values[1]),
                float(values[2]),
            )
            for (date_value, channel_id), values in sorted(buckets.items())
        ]

    @classmethod
    def _dimension_rows_by_local_bucket(
        cls,
        rows: list[Any],
        format_text: str,
        time_zone: ZoneInfo,
        *,
        gateway_key_names: dict[str, str] | None = None,
        cost_multipliers: dict[str, float] | None = None,
    ) -> list[dict[str, float | str]]:
        buckets: dict[tuple[str, str, str], dict[str, float | str]] = {}
        gateway_key_names = gateway_key_names or {}

        def add_bucket(
            *,
            bucket: str,
            dimension_type: str,
            dimension_id: Any,
            dimension_name: Any,
            success: bool,
            latency_ms: Any = 0,
            first_token_latency_ms: Any = 0,
            input_tokens: Any = 0,
            cache_read_input_tokens: Any = 0,
            cache_write_input_tokens: Any = 0,
            output_tokens: Any = 0,
            total_tokens: Any = 0,
            input_cost_usd: Any = 0.0,
            output_cost_usd: Any = 0.0,
            total_cost_usd: Any = 0.0,
        ) -> None:
            normalized_id = str(dimension_id or "").strip()
            if not normalized_id:
                return
            normalized_name = str(dimension_name or normalized_id).strip()
            if not normalized_name:
                normalized_name = normalized_id
            key = (bucket, dimension_type, normalized_id)
            current = buckets.setdefault(
                key,
                {
                    "date": bucket,
                    "dimension_type": dimension_type,
                    "dimension_id": normalized_id,
                    "dimension_name": normalized_name,
                    "request_count": 0.0,
                    "successful_requests": 0.0,
                    "failed_requests": 0.0,
                    "latency_ms_sum": 0.0,
                    "first_token_latency_ms_sum": 0.0,
                    "input_tokens": 0.0,
                    "cache_read_input_tokens": 0.0,
                    "cache_write_input_tokens": 0.0,
                    "output_tokens": 0.0,
                    "total_tokens": 0.0,
                    "input_cost_usd": 0.0,
                    "output_cost_usd": 0.0,
                    "total_cost_usd": 0.0,
                },
            )
            if (
                not current["dimension_name"]
                or current["dimension_name"] == normalized_id
            ):
                current["dimension_name"] = normalized_name
            current["request_count"] = float(current["request_count"]) + 1.0
            if success:
                current["successful_requests"] = (
                    float(current["successful_requests"]) + 1.0
                )
            else:
                current["failed_requests"] = float(current["failed_requests"]) + 1.0
            current["latency_ms_sum"] = float(current["latency_ms_sum"]) + float(
                latency_ms or 0
            )
            current["first_token_latency_ms_sum"] = float(
                current["first_token_latency_ms_sum"]
            ) + float(first_token_latency_ms or 0)
            current["input_tokens"] = float(current["input_tokens"]) + float(
                input_tokens or 0
            )
            current["cache_read_input_tokens"] = float(
                current["cache_read_input_tokens"]
            ) + float(cache_read_input_tokens or 0)
            current["cache_write_input_tokens"] = float(
                current["cache_write_input_tokens"]
            ) + float(cache_write_input_tokens or 0)
            current["output_tokens"] = float(current["output_tokens"]) + float(
                output_tokens or 0
            )
            current["total_tokens"] = float(current["total_tokens"]) + float(
                total_tokens or 0
            )
            current["input_cost_usd"] = float(current["input_cost_usd"]) + float(
                input_cost_usd or 0.0
            )
            current["output_cost_usd"] = float(current["output_cost_usd"]) + float(
                output_cost_usd or 0.0
            )
            current["total_cost_usd"] = float(current["total_cost_usd"]) + float(
                total_cost_usd or 0.0
            )

        for row in rows:
            (
                created_at,
                channel_id,
                channel_name,
                upstream_model_name,
                gateway_key_id,
                success,
                latency_ms,
                first_token_latency_ms,
                input_tokens,
                cache_read_input_tokens,
                cache_write_input_tokens,
                output_tokens,
                total_tokens,
                input_cost_usd,
                output_cost_usd,
                total_cost_usd,
                attempts_json,
            ) = row
            utc_created_at = cls._to_utc_datetime(created_at)
            if utc_created_at is None:
                continue
            bucket = utc_created_at.astimezone(time_zone).strftime(format_text)
            success_value = bool(int(success or 0))
            attempts = cls._safe_attempt_items(attempts_json)
            multiplier = cls._cost_multiplier_from_attempts(attempts, cost_multipliers)
            common_values = {
                "bucket": bucket,
                "success": success_value,
                "latency_ms": latency_ms,
                "first_token_latency_ms": first_token_latency_ms,
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "cache_write_input_tokens": cache_write_input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "input_cost_usd": float(input_cost_usd or 0.0) * multiplier,
                "output_cost_usd": float(output_cost_usd or 0.0) * multiplier,
                "total_cost_usd": float(total_cost_usd or 0.0) * multiplier,
            }
            channel_dimension_id, channel_dimension_name = cls._channel_usage_identity(
                channel_id, channel_name, attempts
            )
            add_bucket(
                dimension_type="channel",
                dimension_id=channel_dimension_id,
                dimension_name=channel_dimension_name,
                **common_values,
            )
            add_bucket(
                dimension_type="model",
                dimension_id=upstream_model_name,
                dimension_name=upstream_model_name,
                **common_values,
            )
            normalized_gateway_key_id = str(gateway_key_id or "").strip()
            add_bucket(
                dimension_type="gateway_key",
                dimension_id=gateway_key_id,
                dimension_name=cls._gateway_key_display_name(
                    normalized_gateway_key_id,
                    gateway_key_names.get(normalized_gateway_key_id, ""),
                ),
                **common_values,
            )

            if attempts:
                for attempt in attempts:
                    add_bucket(
                        bucket=bucket,
                        dimension_type="channel_attempt",
                        dimension_id=attempt.get("channel_id"),
                        dimension_name=attempt.get("channel_name"),
                        success=bool(attempt.get("success")),
                        latency_ms=attempt.get("duration_ms") or 0,
                    )
            else:
                add_bucket(
                    bucket=bucket,
                    dimension_type="channel_attempt",
                    dimension_id=channel_id,
                    dimension_name=channel_name,
                    success=success_value,
                    latency_ms=latency_ms,
                    first_token_latency_ms=first_token_latency_ms,
                )

        return [buckets[key] for key in sorted(buckets)]

    @classmethod
    def _cost_multiplier_from_attempts_json(
        cls, attempts_json: object, cost_multipliers: dict[str, float] | None
    ) -> float:
        return cls._cost_multiplier_from_attempts(
            cls._safe_attempt_items(attempts_json), cost_multipliers
        )

    @classmethod
    def _cost_multiplier_from_attempts(
        cls, attempts: list[dict[str, Any]], cost_multipliers: dict[str, float] | None
    ) -> float:
        if not cost_multipliers:
            return 1.0
        credential_id = cls._primary_attempt_credential_id(attempts)
        if not credential_id:
            return 1.0
        return max(float(cost_multipliers.get(credential_id, 1.0)), 0.0)

    @staticmethod
    def _primary_attempt_credential_id(attempts: list[dict[str, Any]]) -> str:
        primary_attempt: dict[str, Any] | None = None
        for attempt in reversed(attempts):
            if bool(attempt.get("success")):
                primary_attempt = attempt
                break
        if primary_attempt is None and attempts:
            primary_attempt = attempts[-1]
        if primary_attempt is None:
            return ""
        return str(primary_attempt.get("credential_id") or "").strip()

    @classmethod
    def _channel_dimension_cost_multiplier(
        cls, dimension_id: object, cost_multipliers: dict[str, float] | None
    ) -> float:
        if not cost_multipliers:
            return 1.0
        normalized_id = str(dimension_id or "").strip()
        if ":" not in normalized_id:
            return 1.0
        credential_id = normalized_id.rsplit(":", 1)[-1].strip()
        if not credential_id:
            return 1.0
        return max(float(cost_multipliers.get(credential_id, 1.0)), 0.0)

    @classmethod
    def _with_channel_dimension_cost_multiplier(
        cls, row: dict[str, float | str], cost_multipliers: dict[str, float]
    ) -> dict[str, float | str]:
        multiplier = cls._channel_dimension_cost_multiplier(
            row["dimension_id"], cost_multipliers
        )
        if multiplier == 1.0:
            return row
        adjusted = dict(row)
        adjusted["input_cost_usd"] = float(row["input_cost_usd"]) * multiplier
        adjusted["output_cost_usd"] = float(row["output_cost_usd"]) * multiplier
        adjusted["total_cost_usd"] = float(row["total_cost_usd"]) * multiplier
        return adjusted

    @classmethod
    async def _gateway_key_display_names_by_id(
        cls, session: AsyncSession, key_ids: list[str | None]
    ) -> dict[str, str]:
        unique_ids = [
            item
            for item in dict.fromkeys(
                str(key_id).strip() for key_id in key_ids if key_id
            )
            if item
        ]
        if not unique_ids:
            return {}
        rows = (
            await session.execute(
                select(GatewayApiKeyEntity.id, GatewayApiKeyEntity.remark).where(
                    GatewayApiKeyEntity.id.in_(unique_ids)
                )
            )
        ).all()
        return {
            str(key_id): cls._gateway_key_display_name(key_id, remark)
            for key_id, remark in rows
            if key_id is not None
        }

    @staticmethod
    def _gateway_key_display_name(key_id: object, remark: object = "") -> str:
        normalized_id = str(key_id or "").strip()
        if not normalized_id:
            return ""
        if normalized_id == "n/a":
            return "未使用 API Key"
        normalized_remark = str(remark or "").strip()
        return normalized_remark or "未命名密钥"

    @classmethod
    def _channel_usage_identity(
        cls, channel_id: Any, channel_name: Any, attempts: list[dict[str, Any]]
    ) -> tuple[str, str]:
        primary_attempt = cls._primary_attempt(attempts)
        normalized_channel_id = str(channel_id or "").strip()
        normalized_channel_name = str(channel_name or normalized_channel_id).strip()
        if primary_attempt is not None:
            attempt_channel_id = str(
                primary_attempt.get("channel_id") or normalized_channel_id
            ).strip()
            attempt_channel_name = str(
                primary_attempt.get("channel_name")
                or normalized_channel_name
                or attempt_channel_id
            ).strip()
            credential_id = str(primary_attempt.get("credential_id") or "").strip()
            if credential_id:
                credential_name = str(
                    primary_attempt.get("credential_name") or ""
                ).strip()
                dimension_id = (
                    f"{attempt_channel_id}:{credential_id}"
                    if attempt_channel_id
                    else credential_id
                )
                credential_label = credential_name or credential_id
                label_parts = [
                    attempt_channel_name or attempt_channel_id,
                    credential_label,
                ]
                return (
                    dimension_id,
                    " - ".join(part for part in label_parts if part) or dimension_id,
                )
        return normalized_channel_id, normalized_channel_name or normalized_channel_id

    @staticmethod
    def _primary_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
        for attempt in reversed(attempts):
            if bool(attempt.get("success")):
                return attempt
        if attempts:
            return attempts[-1]
        return None

    @staticmethod
    def _safe_attempt_items(raw_value: Any) -> list[dict[str, Any]]:
        try:
            parsed = json.loads(raw_value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    @staticmethod
    def _safe_divide(numerator: float, denominator: float) -> float:
        if denominator <= 0:
            return 0.0
        return float(numerator) / float(denominator)

    @classmethod
    def _ratio_percent(cls, numerator: float, denominator: float) -> float:
        return round(cls._safe_divide(numerator, denominator) * 100, 2)

    @staticmethod
    def _delta_percent(current: float, previous: float) -> float:
        if previous <= 0:
            return 0.0
        return round(((current - previous) / previous) * 100, 2)
