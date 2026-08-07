"""request log upstream headers

Revision ID: a3c8e1f5b7d2
Revises: f7c2a9e1b4d8
Create Date: 2026-07-17 14:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3c8e1f5b7d2"
down_revision: Union[str, Sequence[str], None] = "f7c2a9e1b4d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("upstream_headers", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("upstream_headers")
