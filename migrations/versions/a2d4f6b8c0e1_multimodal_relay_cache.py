"""add persistent multimodal relay cache

Revision ID: a2d4f6b8c0e1
Revises: d8f2a4c6e9b1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "a2d4f6b8c0e1"
down_revision: str | None = "d8f2a4c6e9b1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "multimodal_relay_cache",
        sa.Column("resource_hash", sa.String(length=64), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("helper_group_id", sa.String(length=80), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("resource_hash", "media_kind", "helper_group_id"),
    )
    op.create_index(
        "ix_multimodal_relay_cache_expires_at",
        "multimodal_relay_cache",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_multimodal_relay_cache_expires_at", table_name="multimodal_relay_cache"
    )
    op.drop_table("multimodal_relay_cache")
