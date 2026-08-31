"""model group restrict keys flag

Revision ID: d5f9b2c8e1a3
Revises: c4e8a1b7d2f0
Create Date: 2026-07-12 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d5f9b2c8e1a3"
down_revision: Union[str, Sequence[str], None] = "c4e8a1b7d2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "restrict_keys",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    op.execute(
        sa.text(
            "UPDATE model_groups SET restrict_keys = 1 "
            "WHERE allowed_key_ids_json IS NOT NULL "
            "AND allowed_key_ids_json NOT IN ('[]', '')"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("restrict_keys")
