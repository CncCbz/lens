"""model group pi config auto sync flag

Revision ID: e7f9b1d3c5e7
Revises: e6f8a0c2d4b6
Create Date: 2026-06-02 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7f9b1d3c5e7"
down_revision: Union[str, Sequence[str], None] = "e6f8a0c2d4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        # Existing pi configs were written by the sync task, so they stay
        # auto-managed until the user edits them.
        batch_op.add_column(
            sa.Column(
                "pi_config_auto",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.alter_column("pi_config_auto", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("pi_config_auto")
