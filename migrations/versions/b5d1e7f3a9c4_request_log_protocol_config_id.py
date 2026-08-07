"""Add request log protocol config ownership.

Revision ID: b5d1e7f3a9c4
Revises: a3c8e1f5b7d2
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b5d1e7f3a9c4"
down_revision: str | Sequence[str] | None = "a3c8e1f5b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROTOCOLS = (
    "openai_chat",
    "openai_responses",
    "openai_embedding",
    "openai_image",
    "rerank",
    "anthropic",
    "gemini",
)


def _backfill_protocol_config_id() -> None:
    dialect = op.get_bind().dialect.name
    for protocol in _PROTOCOLS:
        suffix = f"_{protocol}"
        suffix_length = len(suffix)
        if dialect == "sqlite":
            op.execute(
                sa.text(
                    "UPDATE request_logs "
                    "SET protocol_config_id = "
                    "substr(channel_id, 1, length(channel_id) - :suffix_length) "
                    "WHERE protocol_config_id IS NULL "
                    "AND channel_id IS NOT NULL "
                    "AND substr(channel_id, -:suffix_length) = :suffix"
                ).bindparams(suffix_length=suffix_length, suffix=suffix)
            )
        elif dialect == "postgresql":
            op.execute(
                sa.text(
                    "UPDATE request_logs "
                    "SET protocol_config_id = "
                    "left(channel_id, length(channel_id) - :suffix_length) "
                    "WHERE protocol_config_id IS NULL "
                    "AND channel_id IS NOT NULL "
                    'AND right(channel_id, :suffix_length) = :suffix'
                ).bindparams(suffix_length=suffix_length, suffix=suffix)
            )
        else:
            raise RuntimeError(f"Unsupported database dialect: {dialect}")


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column("protocol_config_id", sa.String(length=80), nullable=True)
        )

    _backfill_protocol_config_id()

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.create_index(
            "ix_request_logs_protocol_config_created",
            ["protocol_config_id", "created_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_index("ix_request_logs_protocol_config_created")
        batch_op.drop_column("protocol_config_id")
