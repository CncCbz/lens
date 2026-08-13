from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.models import RequestLogLifecycleStatus
from lens_api.persistence.entities import RequestLogEntity
from lens_api.persistence.repositories.overview_mixin import OverviewMixin
from lens_api.persistence.repositories.request_log_filters_mixin import (
    RequestLogFilterMixin,
)
from lens_api.persistence.repositories.request_log_store import RequestLogStore

TIME_ZONE = ZoneInfo("Asia/Shanghai")

NUMERIC_FIELDS = (
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


class _Store(OverviewMixin, RequestLogFilterMixin):
    _apply_gateway_key_filter = staticmethod(RequestLogStore._apply_gateway_key_filter)
    _runtime_time_zone = staticmethod(RequestLogStore._runtime_time_zone)


def _make_log(
    i: int, created_at: datetime, *, success: int, attempts: list[dict]
) -> RequestLogEntity:
    tokens = i * 10
    return RequestLogEntity(
        request_id=f"agg-{i}",
        protocol="openai",
        user_agent="ua",
        success=success,
        lifecycle_status=(
            RequestLogLifecycleStatus.SUCCEEDED.value
            if success
            else RequestLogLifecycleStatus.FAILED.value
        ),
        first_token_latency_ms=i,
        latency_ms=i * 100,
        input_tokens=tokens,
        cache_read_input_tokens=tokens,
        cache_write_input_tokens=tokens,
        output_tokens=tokens * 2,
        total_tokens=tokens * 3,
        input_cost_usd=0.01 * i,
        output_cost_usd=0.02 * i,
        total_cost_usd=0.03 * i,
        attempts_json=json.dumps(attempts),
        channel_id="ch-a" if i % 2 else "ch-b",
        channel_name="Channel A" if i % 2 else "Channel B",
        upstream_model_name="gpt-4o" if i % 2 else "claude-3",
        gateway_key_id="key-1",
        created_at=created_at,
    )


def _merge_dimension_rows(rows: list[dict]) -> dict[tuple, dict]:
    merged: dict[tuple, dict] = {}
    for row in rows:
        key = (row["date"], row["dimension_type"], row["dimension_id"])
        current = merged.get(key)
        if current is None:
            merged[key] = dict(row)
            continue
        for field in NUMERIC_FIELDS:
            current[field] = float(current[field]) + float(row[field])
    return merged


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_sql_aggregation_matches_python_bucketing(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    store = _Store(session_factory)

    midnight = datetime(2026, 8, 13, 0, 0, 0, tzinfo=TIME_ZONE)
    base_utc = midnight.astimezone(UTC).replace(tzinfo=None)
    attempts_ok = [
        {
            "channel_id": "ch-a",
            "channel_name": "Channel A",
            "credential_id": "cred-x",
            "credential_name": "Cred X",
            "success": False,
            "duration_ms": 150,
        },
        {
            "channel_id": "ch-b",
            "channel_name": "Channel B",
            "credential_id": "cred-x",
            "credential_name": "Cred X",
            "success": True,
            "duration_ms": 300,
        },
    ]
    attempts_fail = [
        {
            "channel_id": "ch-a",
            "channel_name": "Channel A",
            "credential_id": "cred-y",
            "credential_name": "Cred Y",
            "success": False,
            "duration_ms": 200,
        },
    ]
    logs = [
        _make_log(1, base_utc - timedelta(minutes=2), success=1, attempts=[]),
        _make_log(2, base_utc - timedelta(minutes=1), success=0, attempts=[]),
        _make_log(3, base_utc - timedelta(seconds=30), success=1, attempts=attempts_ok),
        _make_log(
            4, base_utc + timedelta(seconds=30), success=0, attempts=attempts_fail
        ),
        _make_log(5, base_utc + timedelta(minutes=1), success=1, attempts=[]),
        _make_log(6, base_utc + timedelta(minutes=2), success=1, attempts=[]),
        _make_log(7, base_utc + timedelta(minutes=3), success=0, attempts=[]),
    ]
    logs[6].channel_id = None
    logs[6].channel_name = None

    async with session_factory() as session:
        session.add_all(logs)
        await session.commit()

    async with session_factory() as session:
        from sqlalchemy import func, select

        # daily points: SQL aggregation vs python bucketing
        sql_daily = await store._request_log_daily_points(
            session, days=0, time_zone=TIME_ZONE
        )
        raw_daily = (
            await session.execute(
                select(
                    RequestLogEntity.created_at,
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
                    RequestLogEntity.attempts_json,
                ).where(RequestLogEntity.stats_archived == 0)
            )
        ).all()
        py_daily_buckets = OverviewMixin._daily_stats_by_local_bucket(
            raw_daily, TIME_ZONE
        )
        assert {p.date for p in sql_daily} == set(py_daily_buckets)
        for point in sql_daily:
            bucket = py_daily_buckets[point.date]
            assert point.request_count == int(bucket["request_count"])
            assert point.successful_requests == int(bucket["successful_requests"])
            assert point.failed_requests == int(bucket["failed_requests"])
            assert point.total_tokens == int(bucket["total_tokens"])
            assert float(point.total_cost_usd) == pytest.approx(
                float(bucket["total_cost_usd"])
            )

        # model rows
        sql_models = await store._request_log_model_rows(
            session, days=0, format_text="%Y%m%d", time_zone=TIME_ZONE
        )
        model_expr = func.nullif(func.trim(RequestLogEntity.upstream_model_name), "")
        raw_models = (
            await session.execute(
                select(
                    RequestLogEntity.created_at,
                    model_expr,
                    RequestLogEntity.total_tokens,
                    RequestLogEntity.total_cost_usd,
                    RequestLogEntity.attempts_json,
                )
                .where(RequestLogEntity.success == 1)
                .where(
                    RequestLogEntity.lifecycle_status
                    == RequestLogLifecycleStatus.SUCCEEDED.value
                )
                .where(model_expr.is_not(None))
                .where(RequestLogEntity.stats_archived == 0)
            )
        ).all()
        assert sql_models == OverviewMixin._model_rows_by_local_bucket(
            raw_models, "%Y%m%d", TIME_ZONE
        )

        # channel rows
        sql_channels = await store._request_log_channel_rows(
            session, days=0, format_text="%Y%m%d", time_zone=TIME_ZONE
        )
        channel_id_expr = func.coalesce(RequestLogEntity.channel_id, "n/a")
        channel_name_expr = func.coalesce(
            func.nullif(func.trim(RequestLogEntity.channel_name), ""),
            RequestLogEntity.channel_id,
            "n/a",
        )
        raw_channels = (
            await session.execute(
                select(
                    RequestLogEntity.created_at,
                    channel_id_expr,
                    channel_name_expr,
                    RequestLogEntity.total_tokens,
                    RequestLogEntity.total_cost_usd,
                    RequestLogEntity.attempts_json,
                )
                .where(RequestLogEntity.success == 1)
                .where(
                    RequestLogEntity.lifecycle_status
                    == RequestLogLifecycleStatus.SUCCEEDED.value
                )
                .where(RequestLogEntity.channel_id.is_not(None))
                .where(RequestLogEntity.stats_archived == 0)
            )
        ).all()
        assert sql_channels == OverviewMixin._channel_rows_by_local_bucket(
            raw_channels, "%Y%m%d", TIME_ZONE
        )

        # dimension rows (channel/model/gateway_key/channel_attempt)
        raw_dims = (
            await session.execute(
                select(
                    RequestLogEntity.created_at,
                    RequestLogEntity.channel_id,
                    RequestLogEntity.channel_name,
                    RequestLogEntity.upstream_model_name,
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
                    RequestLogEntity.attempts_json,
                )
                .where(
                    RequestLogEntity.lifecycle_status.in_(
                        [
                            RequestLogLifecycleStatus.SUCCEEDED.value,
                            RequestLogLifecycleStatus.FAILED.value,
                        ]
                    )
                )
                .where(RequestLogEntity.stats_archived == 0)
            )
        ).all()
        py_dims = _merge_dimension_rows(
            OverviewMixin._dimension_rows_by_local_bucket(raw_dims, "%Y%m%d", TIME_ZONE)
        )
        sql_dims = await store._request_log_primary_dimension_rows(
            session, days=0, date_format="%Y%m%d", time_zone=TIME_ZONE
        )
        sql_dims += await store._request_log_channel_attempt_rows(
            session, days=0, date_format="%Y%m%d", time_zone=TIME_ZONE
        )
        sql_dims = _merge_dimension_rows(sql_dims)

        assert set(py_dims) == set(sql_dims)
        for key, py_row in py_dims.items():
            sql_row = sql_dims[key]
            for field in NUMERIC_FIELDS:
                assert float(py_row[field]) == pytest.approx(float(sql_row[field]))
            if key[1] != "gateway_key":
                assert str(py_row["dimension_name"]) == str(sql_row["dimension_name"])

    await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_precomputed_primary_attempt_columns(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    store = _Store(session_factory)

    created_at = datetime.now(UTC).replace(tzinfo=None)
    attempts = [
        {
            "channel_id": "ch-a",
            "channel_name": "Channel A",
            "credential_id": "cred-x",
            "credential_name": "Cred X",
            "success": True,
            "duration_ms": 100,
        }
    ]
    # Row with precomputed columns; credential deliberately differs from
    # attempts_json to prove the column wins over the jsonb fallback.
    precomputed = _make_log(1, created_at, success=1, attempts=attempts)
    precomputed.primary_credential_id = "cred-precomputed"
    precomputed.primary_credential_name = "Cred Precomputed"
    precomputed.primary_attempt_channel_id = "ch-precomputed"
    precomputed.primary_attempt_channel_name = "Channel Precomputed"
    # Row without precomputed columns falls back to jsonb parsing.
    legacy = _make_log(2, created_at, success=1, attempts=attempts)

    async with session_factory() as session:
        session.add_all([precomputed, legacy])
        await session.commit()

    async with session_factory() as session:
        rows = await store._request_log_primary_dimension_rows(
            session, days=0, date_format="%Y%m%d", time_zone=TIME_ZONE
        )
        by_date = {(r["date"], r["dimension_id"]): r for r in rows}
        today = created_at.replace(tzinfo=UTC).astimezone(TIME_ZONE).strftime("%Y%m%d")
        # channel dimension id uses attempt channel + credential
        precomputed_row = by_date[(today, "ch-precomputed:cred-precomputed")]
        assert precomputed_row["dimension_name"] == (
            "Channel Precomputed - Cred Precomputed"
        )
        legacy_row = by_date[(today, "ch-a:cred-x")]
        assert legacy_row["dimension_name"] == "Channel A - Cred X"
        # both rows counted
        assert precomputed_row["request_count"] + legacy_row["request_count"] == 2

    await engine.dispose()
