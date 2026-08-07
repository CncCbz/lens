"""overview dimension daily stats

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Sequence, Union
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TERMINAL_STATUSES = {"succeeded", "failed"}


def _resolve_time_zone(bind) -> ZoneInfo:
    row = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'time_zone'")
    ).first()
    if row is None:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(str(row[0]))
    except Exception:
        return ZoneInfo("UTC")


def _coerce_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _date_key(created_at, time_zone: ZoneInfo) -> str | None:
    parsed = _coerce_datetime(created_at)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        utc_dt = parsed.replace(tzinfo=UTC)
    else:
        utc_dt = parsed.astimezone(UTC)
    return utc_dt.astimezone(time_zone).strftime("%Y%m%d")


def _parse_attempts(raw_value: str | None) -> list[dict]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _gateway_key_display_name(
    gateway_key_id: str | None, gateway_key_names: dict[str, str]
) -> str | None:
    normalized_id = str(gateway_key_id or "").strip()
    if not normalized_id:
        return None
    if normalized_id == "n/a":
        return "未使用 API Key"
    return gateway_key_names.get(normalized_id, "").strip() or "未命名密钥"


def _channel_usage_identity(
    channel_id: str | None, channel_name: str | None, attempts: list[dict]
) -> tuple[str | None, str | None]:
    primary_attempt: dict | None = None
    for attempt in reversed(attempts):
        if bool(attempt.get("success")):
            primary_attempt = attempt
            break
    if primary_attempt is None and attempts:
        primary_attempt = attempts[-1]
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
            credential_name = str(primary_attempt.get("credential_name") or "").strip()
            dimension_id = (
                f"{attempt_channel_id}:{credential_id}"
                if attempt_channel_id
                else credential_id
            )
            credential_label = credential_name or credential_id
            label_parts = [attempt_channel_name or attempt_channel_id, credential_label]
            return (
                dimension_id,
                " - ".join(part for part in label_parts if part) or dimension_id,
            )
    return (
        normalized_channel_id or None,
        normalized_channel_name or normalized_channel_id or None,
    )


def _new_bucket(name: str) -> dict[str, object]:
    return {
        "dimension_name": name,
        "request_count": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "latency_ms_sum": 0,
        "first_token_latency_ms_sum": 0,
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_cost_usd": 0.0,
        "output_cost_usd": 0.0,
        "total_cost_usd": 0.0,
    }


def _add_bucket(
    buckets: dict[tuple[str, str, str], dict[str, object]],
    *,
    date: str,
    dimension_type: str,
    dimension_id: str | None,
    dimension_name: str | None,
    success: bool,
    latency_ms: int | None = 0,
    first_token_latency_ms: int | None = 0,
    input_tokens: int | None = 0,
    cache_read_input_tokens: int | None = 0,
    cache_write_input_tokens: int | None = 0,
    output_tokens: int | None = 0,
    total_tokens: int | None = 0,
    input_cost_usd: float | None = 0.0,
    output_cost_usd: float | None = 0.0,
    total_cost_usd: float | None = 0.0,
) -> None:
    normalized_id = str(dimension_id or "").strip()
    if not normalized_id:
        return
    normalized_name = str(dimension_name or normalized_id).strip() or normalized_id
    key = (date, dimension_type, normalized_id)
    current = buckets.setdefault(key, _new_bucket(normalized_name))
    if not current["dimension_name"] or current["dimension_name"] == normalized_id:
        current["dimension_name"] = normalized_name
    current["request_count"] = int(current["request_count"]) + 1
    if success:
        current["successful_requests"] = int(current["successful_requests"]) + 1
    else:
        current["failed_requests"] = int(current["failed_requests"]) + 1
    current["latency_ms_sum"] = int(current["latency_ms_sum"]) + int(latency_ms or 0)
    current["first_token_latency_ms_sum"] = int(
        current["first_token_latency_ms_sum"]
    ) + int(first_token_latency_ms or 0)
    current["input_tokens"] = int(current["input_tokens"]) + int(input_tokens or 0)
    current["cache_read_input_tokens"] = int(current["cache_read_input_tokens"]) + int(
        cache_read_input_tokens or 0
    )
    current["cache_write_input_tokens"] = int(
        current["cache_write_input_tokens"]
    ) + int(cache_write_input_tokens or 0)
    current["output_tokens"] = int(current["output_tokens"]) + int(output_tokens or 0)
    current["total_tokens"] = int(current["total_tokens"]) + int(total_tokens or 0)
    current["input_cost_usd"] = float(current["input_cost_usd"]) + float(
        input_cost_usd or 0.0
    )
    current["output_cost_usd"] = float(current["output_cost_usd"]) + float(
        output_cost_usd or 0.0
    )
    current["total_cost_usd"] = float(current["total_cost_usd"]) + float(
        total_cost_usd or 0.0
    )


def upgrade() -> None:
    op.create_table(
        "overview_dimension_daily_stats",
        sa.Column("date", sa.String(length=8), nullable=False),
        sa.Column("dimension_type", sa.String(length=32), nullable=False),
        sa.Column("dimension_id", sa.String(length=220), nullable=False),
        sa.Column(
            "dimension_name", sa.String(length=220), nullable=False, server_default=""
        ),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "successful_requests", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("failed_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms_sum", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "first_token_latency_ms_sum",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cache_read_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cache_write_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("output_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.PrimaryKeyConstraint("date", "dimension_type", "dimension_id"),
    )
    op.create_index(
        "ix_overview_dimension_daily_type_date",
        "overview_dimension_daily_stats",
        ["dimension_type", "date"],
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "request_logs" not in table_names:
        return

    time_zone = _resolve_time_zone(bind)
    gateway_key_names: dict[str, str] = {}
    if "gateway_api_keys" in table_names:
        gateway_key_names = {
            str(row[0]): str(row[1] or "").strip()
            for row in bind.execute(
                sa.text("SELECT id, remark FROM gateway_api_keys")
            ).all()
        }

    rows = bind.execute(
        sa.text(
            "SELECT created_at, channel_id, channel_name, gateway_key_id, "
            "upstream_model_name, success, lifecycle_status, latency_ms, "
            "first_token_latency_ms, input_tokens, cache_read_input_tokens, "
            "cache_write_input_tokens, output_tokens, total_tokens, input_cost_usd, "
            "output_cost_usd, total_cost_usd, attempts_json "
            "FROM request_logs "
            "WHERE lifecycle_status IN ('succeeded', 'failed') "
            "ORDER BY created_at ASC"
        )
    ).all()

    buckets: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        (
            created_at,
            channel_id,
            channel_name,
            gateway_key_id,
            upstream_model_name,
            success,
            lifecycle_status,
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
        if str(lifecycle_status) not in TERMINAL_STATUSES:
            continue
        date = _date_key(created_at, time_zone)
        if date is None:
            continue
        success_value = bool(int(success or 0))
        common = {
            "date": date,
            "success": success_value,
            "latency_ms": latency_ms,
            "first_token_latency_ms": first_token_latency_ms,
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cache_write_input_tokens": cache_write_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "input_cost_usd": input_cost_usd,
            "output_cost_usd": output_cost_usd,
            "total_cost_usd": total_cost_usd,
        }
        attempts = _parse_attempts(attempts_json)
        channel_dimension_id, channel_dimension_name = _channel_usage_identity(
            channel_id, channel_name, attempts
        )
        _add_bucket(
            buckets,
            dimension_type="channel",
            dimension_id=channel_dimension_id,
            dimension_name=channel_dimension_name,
            **common,
        )
        _add_bucket(
            buckets,
            dimension_type="model",
            dimension_id=upstream_model_name,
            dimension_name=upstream_model_name,
            **common,
        )
        _add_bucket(
            buckets,
            dimension_type="gateway_key",
            dimension_id=gateway_key_id,
            dimension_name=_gateway_key_display_name(gateway_key_id, gateway_key_names),
            **common,
        )
        for attempt in attempts:
            _add_bucket(
                buckets,
                date=date,
                dimension_type="channel_attempt",
                dimension_id=attempt.get("channel_id"),
                dimension_name=attempt.get("channel_name"),
                success=bool(attempt.get("success")),
                latency_ms=int(attempt.get("duration_ms") or 0),
            )

    if not buckets:
        return

    dimension_daily = sa.table(
        "overview_dimension_daily_stats",
        sa.column("date", sa.String),
        sa.column("dimension_type", sa.String),
        sa.column("dimension_id", sa.String),
        sa.column("dimension_name", sa.String),
        sa.column("request_count", sa.Integer),
        sa.column("successful_requests", sa.Integer),
        sa.column("failed_requests", sa.Integer),
        sa.column("latency_ms_sum", sa.Integer),
        sa.column("first_token_latency_ms_sum", sa.Integer),
        sa.column("input_tokens", sa.Integer),
        sa.column("cache_read_input_tokens", sa.Integer),
        sa.column("cache_write_input_tokens", sa.Integer),
        sa.column("output_tokens", sa.Integer),
        sa.column("total_tokens", sa.Integer),
        sa.column("input_cost_usd", sa.Float),
        sa.column("output_cost_usd", sa.Float),
        sa.column("total_cost_usd", sa.Float),
    )

    batch: list[dict[str, object]] = []
    for (date, dimension_type, dimension_id), values in sorted(buckets.items()):
        batch.append(
            {
                "date": date,
                "dimension_type": dimension_type,
                "dimension_id": dimension_id,
                **values,
            }
        )

    op.bulk_insert(dimension_daily, batch)


def downgrade() -> None:
    op.drop_index(
        "ix_overview_dimension_daily_type_date",
        table_name="overview_dimension_daily_stats",
    )
    op.drop_table("overview_dimension_daily_stats")
