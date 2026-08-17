"""site protocol config router error policy config

Revision ID: c9d8e7f6a5b4
Revises: ffb1f20c2bd8
Create Date: 2026-07-10 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, Sequence[str], None] = "e7f9b1d3c5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "router_error_policy_config",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )

    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.alter_column("router_error_policy_config", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.drop_column("router_error_policy_config")
