"""add model group upstream overrides and remove global override settings

Revision ID: c7e4a1b9d2f6
Revises: e1a3c5b7d9f2
Create Date: 2026-05-08 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e4a1b9d2f6"
down_revision: Union[str, Sequence[str], None] = "e1a3c5b7d9f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column("headers_json", sa.Text(), nullable=False, server_default="{}")
        )
        batch_op.add_column(
            sa.Column(
                "param_override_json", sa.Text(), nullable=False, server_default="{}"
            )
        )

    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.alter_column("headers_json", server_default=None)
        batch_op.alter_column("param_override_json", server_default=None)

    op.execute(
        sa.delete(sa.table("settings", sa.column("key", sa.String()))).where(
            sa.column("key").in_(
                ["upstream_headers_config", "upstream_param_override_config"]
            )
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("param_override_json")
        batch_op.drop_column("headers_json")
