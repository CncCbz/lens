"""precompute request log primary attempt columns

Revision ID: 3a5b7c9d1e2f
Revises: d7e8f9a0b1c2
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "3a5b7c9d1e2f"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column("primary_credential_id", sa.String(160), nullable=True)
        )
        batch_op.add_column(
            sa.Column("primary_credential_name", sa.String(200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("primary_attempt_channel_id", sa.String(160), nullable=True)
        )
        batch_op.add_column(
            sa.Column("primary_attempt_channel_name", sa.String(200), nullable=True)
        )
    op.execute("""
        WITH pa AS (
            SELECT
                id,
                CASE WHEN attempts_json ~ '^\\s*\\[' AND attempts_json <> '[]'
                    THEN COALESCE(
                        (SELECT a->>'credential_id'
                         FROM jsonb_array_elements(attempts_json::jsonb)
                              WITH ORDINALITY AS t(a, ord)
                         WHERE (a->>'success') = 'true'
                         ORDER BY ord DESC LIMIT 1),
                        attempts_json::jsonb -> -1 ->> 'credential_id'
                    )
                    ELSE NULL
                END AS credential_id,
                CASE WHEN attempts_json ~ '^\\s*\\[' AND attempts_json <> '[]'
                    THEN COALESCE(
                        (SELECT a->>'credential_name'
                         FROM jsonb_array_elements(attempts_json::jsonb)
                              WITH ORDINALITY AS t(a, ord)
                         WHERE (a->>'success') = 'true'
                         ORDER BY ord DESC LIMIT 1),
                        attempts_json::jsonb -> -1 ->> 'credential_name'
                    )
                    ELSE NULL
                END AS credential_name,
                CASE WHEN attempts_json ~ '^\\s*\\[' AND attempts_json <> '[]'
                    THEN COALESCE(
                        (SELECT a->>'channel_id'
                         FROM jsonb_array_elements(attempts_json::jsonb)
                              WITH ORDINALITY AS t(a, ord)
                         WHERE (a->>'success') = 'true'
                         ORDER BY ord DESC LIMIT 1),
                        attempts_json::jsonb -> -1 ->> 'channel_id'
                    )
                    ELSE NULL
                END AS attempt_channel_id,
                CASE WHEN attempts_json ~ '^\\s*\\[' AND attempts_json <> '[]'
                    THEN COALESCE(
                        (SELECT a->>'channel_name'
                         FROM jsonb_array_elements(attempts_json::jsonb)
                              WITH ORDINALITY AS t(a, ord)
                         WHERE (a->>'success') = 'true'
                         ORDER BY ord DESC LIMIT 1),
                        attempts_json::jsonb -> -1 ->> 'channel_name'
                    )
                    ELSE NULL
                END AS attempt_channel_name
            FROM request_logs
        )
        UPDATE request_logs AS rl
        SET
            primary_credential_id = pa.credential_id,
            primary_credential_name = pa.credential_name,
            primary_attempt_channel_id = pa.attempt_channel_id,
            primary_attempt_channel_name = pa.attempt_channel_name
        FROM pa
        WHERE rl.id = pa.id
        """)


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("primary_attempt_channel_name")
        batch_op.drop_column("primary_attempt_channel_id")
        batch_op.drop_column("primary_credential_name")
        batch_op.drop_column("primary_credential_id")
