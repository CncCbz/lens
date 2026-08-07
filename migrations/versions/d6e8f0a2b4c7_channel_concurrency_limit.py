"""channel concurrency limit

Revision ID: d6e8f0a2b4c7
Revises: b5d1e7f3a9c4
Create Date: 2026-07-20 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d6e8f0a2b4c7"
down_revision: Union[str, Sequence[str], None] = "b5d1e7f3a9c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "concurrency_limit",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.alter_column("concurrency_limit", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.drop_column("concurrency_limit")
