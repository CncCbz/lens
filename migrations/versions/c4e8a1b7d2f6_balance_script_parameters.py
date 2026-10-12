"""balance script parameters

Revision ID: c4e8a1b7d2f6
Revises: a9c3e7b1d5f4
Create Date: 2026-10-13 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4e8a1b7d2f6"
down_revision: Union[str, Sequence[str], None] = "a9c3e7b1d5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "balance_scripts",
        sa.Column(
            "parameters_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "site_credentials",
        sa.Column(
            "balance_params_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("site_credentials", "balance_params_json")
    op.drop_column("balance_scripts", "parameters_json")
