"""site model verifications

Revision ID: b5e9f3a1c7d2
Revises: a2d5e8f1c4b7
Create Date: 2026-11-01 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b5e9f3a1c7d2"
down_revision: Union[str, Sequence[str], None] = "a2d5e8f1c4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "site_model_verifications",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("site_id", sa.String(length=80), nullable=False),
        sa.Column("protocol_config_id", sa.String(length=80), nullable=False),
        sa.Column("credential_id", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("channel_id", sa.String(length=120), nullable=False),
        sa.Column("protocol", sa.String(length=40), nullable=False),
        sa.Column(
            "reasoning_effort",
            sa.String(length=20),
            nullable=False,
            server_default="",
        ),
        sa.Column("challenge_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision", sa.String(length=20), nullable=False, server_default=""),
        sa.Column(
            "prediction", sa.String(length=200), nullable=False, server_default=""
        ),
        sa.Column(
            "prediction_name", sa.String(length=200), nullable=False, server_default=""
        ),
        sa.Column(
            "family_prediction",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "family_prediction_name",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
        sa.Column("ranking_score", sa.Float(), nullable=True),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column(
            "probability_status",
            sa.String(length=40),
            nullable=False,
            server_default="",
        ),
        sa.Column("used_outputs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_like", sa.Integer(), nullable=True),
        sa.Column("matches_expected", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("results_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("samples_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "detector_built_at",
            sa.String(length=40),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "request_id", sa.String(length=80), nullable=False, server_default=""
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_site_model_verifications_site_id",
        "site_model_verifications",
        ["site_id"],
    )
    op.create_index(
        "ix_site_model_verifications_protocol_config_id",
        "site_model_verifications",
        ["protocol_config_id"],
    )
    op.create_index(
        "ix_site_model_verifications_created_at",
        "site_model_verifications",
        ["created_at"],
    )
    op.create_index(
        "ix_site_model_verifications_model_key",
        "site_model_verifications",
        ["protocol_config_id", "credential_id", "model_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_model_verifications_model_key",
        table_name="site_model_verifications",
    )
    op.drop_index(
        "ix_site_model_verifications_created_at",
        table_name="site_model_verifications",
    )
    op.drop_index(
        "ix_site_model_verifications_protocol_config_id",
        table_name="site_model_verifications",
    )
    op.drop_index(
        "ix_site_model_verifications_site_id",
        table_name="site_model_verifications",
    )
    op.drop_table("site_model_verifications")
