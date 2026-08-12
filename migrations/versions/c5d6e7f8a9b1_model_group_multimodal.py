"""model group multimodal capability

Revision ID: c5d6e7f8a9b1
Revises: 88001bf35e79
Create Date: 2026-05-20 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c5d6e7f8a9b1"
down_revision: Union[str, Sequence[str], None] = "88001bf35e79"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "multimodal",
                sa.String(length=16),
                nullable=False,
                server_default="auto",
            )
        )
        batch_op.add_column(
            sa.Column(
                "multimodal_resolved_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )

    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.alter_column("multimodal", server_default=None)
        batch_op.alter_column("multimodal_resolved_json", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("multimodal_resolved_json")
        batch_op.drop_column("multimodal")
