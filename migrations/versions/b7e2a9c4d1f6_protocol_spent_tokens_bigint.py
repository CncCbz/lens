"""widen site protocol spent_tokens to bigint

Revision ID: b7e2a9c4d1f6
Revises: f8c1a3e5b7d9
Create Date: 2026-10-08 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b7e2a9c4d1f6"
down_revision: Union[str, Sequence[str], None] = "f8c1a3e5b7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.alter_column(
            "spent_tokens",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
            postgresql_using="spent_tokens::bigint",
        )


def downgrade() -> None:
    with op.batch_alter_table("site_protocol_configs") as batch_op:
        batch_op.alter_column(
            "spent_tokens",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
            postgresql_using="spent_tokens::integer",
        )
