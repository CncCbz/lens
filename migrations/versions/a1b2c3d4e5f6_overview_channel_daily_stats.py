"""overview channel daily stats

Revision ID: a1b2c3d4e5f6
Revises: b6f9c4e8d2a7
Create Date: 2026-07-01 00:00:00.000000

"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence, Union
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b6f9c4e8d2a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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


def upgrade() -> None:
    op.create_table(
        "overview_channel_daily_stats",
        sa.Column("date", sa.String(length=8), nullable=False),
        sa.Column("channel_id", sa.String(length=160), nullable=False),
        sa.Column(
            "channel_name", sa.String(length=120), nullable=False, server_default=""
        ),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.PrimaryKeyConstraint("date", "channel_id"),
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "request_logs" not in table_names:
        return

    time_zone = _resolve_time_zone(bind)

    rows = bind.execute(
        sa.text(
            "SELECT created_at, channel_id, channel_name, total_tokens, total_cost_usd "
            "FROM request_logs "
            "WHERE success = 1 "
            "AND lifecycle_status = 'succeeded' "
            "AND channel_id IS NOT NULL "
            "ORDER BY created_at ASC"
        )
    ).all()

    buckets: dict[tuple[str, str], list] = {}
    for created_at, channel_id, channel_name, total_tokens, total_cost in rows:
        if created_at is None or channel_id is None:
            continue
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                continue
        if created_at.tzinfo is None:
            utc_dt = created_at.replace(tzinfo=UTC)
        else:
            utc_dt = created_at.astimezone(UTC)
        date_key = utc_dt.astimezone(time_zone).strftime("%Y%m%d")
        key = (date_key, str(channel_id))
        current = buckets.setdefault(key, [0, 0, 0.0, ""])
        current[0] += 1
        current[1] += int(total_tokens)
        current[2] += float(total_cost)
        if channel_name and not current[3]:
            current[3] = str(channel_name)

    if not buckets:
        return

    channel_daily = sa.table(
        "overview_channel_daily_stats",
        sa.column("date", sa.String),
        sa.column("channel_id", sa.String),
        sa.column("channel_name", sa.String),
        sa.column("requests", sa.Integer),
        sa.column("total_tokens", sa.Integer),
        sa.column("total_cost_usd", sa.Float),
    )

    batch: list[dict] = []
    for (date_value, channel_id), values in sorted(buckets.items()):
        batch.append(
            {
                "date": date_value,
                "channel_id": channel_id,
                "channel_name": values[3] or channel_id,
                "requests": values[0],
                "total_tokens": values[1],
                "total_cost_usd": values[2],
            }
        )

    op.bulk_insert(channel_daily, batch)


def downgrade() -> None:
    op.drop_table("overview_channel_daily_stats")
