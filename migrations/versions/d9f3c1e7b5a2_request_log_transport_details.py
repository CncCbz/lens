"""store request log transport details

Revision ID: d9f3c1e7b5a2
Revises: c7e4a1b9d2f6
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d9f3c1e7b5a2"
down_revision: Union[str, Sequence[str], None] = "c7e4a1b9d2f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column("client_request_content", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("upstream_request_content", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("upstream_response_headers", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("upstream_response_content", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("client_response_headers", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("upstream_protocol", sa.String(length=40), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("upstream_protocol")
        batch_op.drop_column("client_response_headers")
        batch_op.drop_column("upstream_response_content")
        batch_op.drop_column("upstream_response_headers")
        batch_op.drop_column("upstream_request_content")
        batch_op.drop_column("client_request_content")
