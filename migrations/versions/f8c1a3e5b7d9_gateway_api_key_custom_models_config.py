"""gateway api key custom models config

Revision ID: f8c1a3e5b7d9
Revises: c6b8d0e2f4a1
Create Date: 2026-06-11 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f8c1a3e5b7d9"
down_revision: Union[str, Sequence[str], None] = "c6b8d0e2f4a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("gateway_api_keys") as batch_op:
        batch_op.add_column(
            sa.Column("custom_models_config_json", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("gateway_api_keys") as batch_op:
        batch_op.drop_column("custom_models_config_json")
