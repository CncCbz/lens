"""request log request headers

Revision ID: f7c2a9e1b4d8
Revises: e9f2a4c6d8b1
Create Date: 2026-07-17 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7c2a9e1b4d8"
down_revision: Union[str, Sequence[str], None] = "e9f2a4c6d8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("request_headers", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("request_headers")
