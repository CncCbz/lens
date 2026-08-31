"""model group allowed gateway key ids

Revision ID: c4e8a1b7d2f0
Revises: a8c0e2f4b6d8
Create Date: 2026-07-12 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4e8a1b7d2f0"
down_revision: Union[str, Sequence[str], None] = "a8c0e2f4b6d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "allowed_key_ids_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("allowed_key_ids_json")
