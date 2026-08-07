"""gateway api key excluded models

Revision ID: e1a3c5b7d9f2
Revises: d6e8f0a2b4c7
Create Date: 2026-06-10 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1a3c5b7d9f2"
down_revision: Union[str, Sequence[str], None] = "d6e8f0a2b4c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_api_keys") as batch_op:
        batch_op.add_column(
            sa.Column(
                "excluded_models_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("gateway_api_keys") as batch_op:
        batch_op.drop_column("excluded_models_json")
