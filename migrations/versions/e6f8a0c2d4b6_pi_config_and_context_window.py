"""pi config and context window support

Revision ID: e6f8a0c2d4b6
Revises: f2a6c8e4d1b9
Create Date: 2026-06-01 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e6f8a0c2d4b6"
down_revision: Union[str, Sequence[str], None] = "3a5b7c9d1e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pi_config_json",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )
        batch_op.alter_column("pi_config_json", server_default=None)

    with op.batch_alter_table("model_prices") as batch_op:
        batch_op.add_column(sa.Column("context_window", sa.Integer(), nullable=True))

    op.create_table(
        "pi_model_catalog",
        sa.Column("model_key", sa.String(length=300), nullable=False),
        sa.Column("provider", sa.String(length=120), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("api", sa.String(length=60), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("reasoning", sa.Integer(), nullable=False),
        sa.Column("input_modalities_json", sa.Text(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("input_price_per_million", sa.Float(), nullable=False),
        sa.Column("output_price_per_million", sa.Float(), nullable=False),
        sa.Column("cache_read_price_per_million", sa.Float(), nullable=False),
        sa.Column("cache_write_price_per_million", sa.Float(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("model_key"),
    )
    with op.batch_alter_table("pi_model_catalog", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_pi_model_catalog_model_id"), ["model_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_pi_model_catalog_provider"), ["provider"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("pi_model_catalog", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pi_model_catalog_provider"))
        batch_op.drop_index(batch_op.f("ix_pi_model_catalog_model_id"))
    op.drop_table("pi_model_catalog")

    with op.batch_alter_table("model_prices") as batch_op:
        batch_op.drop_column("context_window")

    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("pi_config_json")
