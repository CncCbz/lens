"""gateway key usage names

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _gateway_key_display_name(
    gateway_key_id: str | None, gateway_key_names: dict[str, str]
) -> str:
    normalized_id = str(gateway_key_id or "").strip()
    if normalized_id == "n/a":
        return "未使用 API Key"
    return gateway_key_names.get(normalized_id, "").strip() or "未命名密钥"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "overview_dimension_daily_stats" not in table_names:
        return

    gateway_key_names: dict[str, str] = {}
    if "gateway_api_keys" in table_names:
        gateway_key_names = {
            str(key_id): str(remark or "").strip()
            for key_id, remark in bind.execute(
                sa.text("SELECT id, remark FROM gateway_api_keys")
            ).all()
            if key_id is not None
        }

    rows = bind.execute(
        sa.text(
            "SELECT DISTINCT dimension_id FROM overview_dimension_daily_stats "
            "WHERE dimension_type = 'gateway_key'"
        )
    ).all()
    for (dimension_id,) in rows:
        bind.execute(
            sa.text(
                "UPDATE overview_dimension_daily_stats "
                "SET dimension_name = :dimension_name "
                "WHERE dimension_type = 'gateway_key' "
                "AND dimension_id = :dimension_id"
            ),
            {
                "dimension_id": dimension_id,
                "dimension_name": _gateway_key_display_name(
                    dimension_id, gateway_key_names
                ),
            },
        )


def downgrade() -> None:
    pass
