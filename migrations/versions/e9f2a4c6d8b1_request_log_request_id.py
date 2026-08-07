"""request log request id

Revision ID: e9f2a4c6d8b1
Revises: a7b8c9d0e1f2
Create Date: 2026-07-06 08:45:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e9f2a4c6d8b1"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "request_id",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )

    op.execute(
        "UPDATE request_logs SET request_id = 'legacy-' || CAST(id AS VARCHAR) "
        "WHERE request_id = ''"
    )

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.alter_column("request_id", server_default=None)
        batch_op.create_index(
            batch_op.f("ix_request_logs_request_id"),
            ["request_id"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_index(batch_op.f("ix_request_logs_request_id"))
        batch_op.drop_column("request_id")
