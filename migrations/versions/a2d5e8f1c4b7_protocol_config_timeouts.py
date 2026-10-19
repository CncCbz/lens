"""protocol config timeouts

Revision ID: a2d5e8f1c4b7
Revises: c4e8a1b7d2f6
Create Date: 2026-10-14 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a2d5e8f1c4b7"
down_revision: Union[str, Sequence[str], None] = "c4e8a1b7d2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "first_token_timeout_seconds",
                sa.Float(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "stream_idle_timeout_seconds",
                sa.Float(),
                nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.alter_column("first_token_timeout_seconds", server_default=None)
        batch_op.alter_column("stream_idle_timeout_seconds", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.drop_column("stream_idle_timeout_seconds")
        batch_op.drop_column("first_token_timeout_seconds")
