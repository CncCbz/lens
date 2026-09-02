"""model group match overrides

Revision ID: a4f8c2e6b0d1
Revises: d5f9b2c8e1a3
Create Date: 2026-04-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a4f8c2e6b0d1"
down_revision: Union[str, Sequence[str], None] = "d5f9b2c8e1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "match_overrides_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("match_overrides_json")
