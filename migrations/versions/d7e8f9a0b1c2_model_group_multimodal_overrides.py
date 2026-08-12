"""model group multimodal overrides

Revision ID: d7e8f9a0b1c2
Revises: c5d6e7f8a9b1
Create Date: 2026-05-21 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "multimodal_overrides_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )

    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.alter_column("multimodal_overrides_json", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("multimodal_overrides_json")
