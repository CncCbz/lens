"""channel rpm and usage limits

Revision ID: a8c0e2f4b6d8
Revises: 4d6e8f0a2b1c
Create Date: 2026-08-20 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8c0e2f4b6d8"
down_revision: Union[str, Sequence[str], None] = "4d6e8f0a2b1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.add_column(
            sa.Column("rpm_limit", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("token_limit", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("cost_limit_usd", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("spent_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("spent_cost_usd", sa.Float(), nullable=False, server_default="0")
        )

    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.alter_column("rpm_limit", server_default=None)
        batch_op.alter_column("token_limit", server_default=None)
        batch_op.alter_column("cost_limit_usd", server_default=None)
        batch_op.alter_column("spent_tokens", server_default=None)
        batch_op.alter_column("spent_cost_usd", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.drop_column("spent_cost_usd")
        batch_op.drop_column("spent_tokens")
        batch_op.drop_column("cost_limit_usd")
        batch_op.drop_column("token_limit")
        batch_op.drop_column("rpm_limit")
