"""site credential cost multiplier

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-02 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("site_credentials")}
    if "cost_multiplier" not in columns:
        op.add_column(
            "site_credentials",
            sa.Column(
                "cost_multiplier",
                sa.Float(),
                nullable=False,
                server_default="1.0",
            ),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("site_credentials")}
    if "cost_multiplier" in columns:
        with op.batch_alter_table("site_credentials") as batch_op:
            batch_op.drop_column("cost_multiplier")
