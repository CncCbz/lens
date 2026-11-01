"""add trigger to site model verifications

Revision ID: c7f1a2b8d3e4
Revises: b5e9f3a1c7d2
Create Date: 2026-11-01 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7f1a2b8d3e4"
down_revision: Union[str, Sequence[str], None] = "b5e9f3a1c7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rows written before this column existed were all started by hand.
    op.add_column(
        "site_model_verifications",
        sa.Column(
            "trigger",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_model_verifications", "trigger")
