"""store request log distilled and raw client response

Revision ID: e4b7a2c9f6d3
Revises: d9f3c1e7b5a2
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e4b7a2c9f6d3"
down_revision: Union[str, Sequence[str], None] = "d9f3c1e7b5a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column("upstream_response_distilled", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("client_response_raw_content", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("client_response_raw_content")
        batch_op.drop_column("upstream_response_distilled")
