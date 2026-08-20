"""optimize request log list queries

Revision ID: 4d6e8f0a2b1c
Revises: c9d8e7f6a5b4
Create Date: 2026-08-19 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4d6e8f0a2b1c"
down_revision: Union[str, Sequence[str], None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REQUEST_LOG_INDEXES = (
    (
        "ix_request_logs_lifecycle_created_id",
        ["lifecycle_status", "created_at", "id"],
    ),
    ("ix_request_logs_channel_created_id", ["channel_id", "created_at", "id"]),
    (
        "ix_request_logs_gateway_created_id",
        ["gateway_key_id", "created_at", "id"],
    ),
    ("ix_request_logs_protocol_created_id", ["protocol", "created_at", "id"]),
    (
        "ix_request_logs_stats_archive_created",
        ["stats_archived", "lifecycle_status", "created_at", "id"],
    ),
    ("ix_request_logs_cost_created_id", ["total_cost_usd", "created_at", "id"]),
    ("ix_request_logs_latency_created_id", ["latency_ms", "created_at", "id"]),
    ("ix_request_logs_tokens_created_id", ["total_tokens", "created_at", "id"]),
)


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("reasoning_effort", sa.String(32), nullable=True))

    op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION lens_request_log_attempt_count(payload text)
            RETURNS integer
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                parsed jsonb;
            BEGIN
                parsed := payload::jsonb;
                IF jsonb_typeof(parsed) = 'array' THEN
                    RETURN jsonb_array_length(parsed);
                END IF;
                RETURN 0;
            EXCEPTION WHEN others THEN
                RETURN 0;
            END;
            $function$;
            """))
    op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION lens_request_log_reasoning_effort(payload text)
            RETURNS text
            LANGUAGE plpgsql
            AS $function$
            DECLARE
                parsed jsonb;
                effort text;
            BEGIN
                parsed := payload::jsonb;
                IF jsonb_typeof(parsed) = 'array' THEN
                    SELECT NULLIF(item->>'reasoning_effort', '')
                    INTO effort
                    FROM jsonb_array_elements(parsed)
                        WITH ORDINALITY AS entries(item, position)
                    WHERE NULLIF(item->>'reasoning_effort', '') IS NOT NULL
                    ORDER BY position DESC
                    LIMIT 1;
                    IF effort IS NULL OR length(effort) > 32 OR effort ~ '\\s' THEN
                        RETURN NULL;
                    END IF;
                    RETURN effort;
                END IF;
                IF jsonb_typeof(parsed) <> 'object' THEN
                    RETURN NULL;
                END IF;
                effort := COALESCE(
                    NULLIF(parsed ->> 'reasoning_effort', ''),
                    NULLIF(parsed ->> 'reasoningEffort', ''),
                    NULLIF(parsed ->> 'model_reasoning_effort', ''),
                    NULLIF(parsed ->> 'modelReasoningEffort', ''),
                    NULLIF(parsed ->> 'effort', ''),
                    NULLIF(parsed ->> 'effortLevel', ''),
                    NULLIF(parsed -> 'reasoning' ->> 'effort', ''),
                    NULLIF(parsed -> 'reasoning' ->> 'budget_tokens', ''),
                    NULLIF(parsed -> 'thinking' ->> 'effort', ''),
                    NULLIF(parsed -> 'thinking' ->> 'budget_tokens', ''),
                    NULLIF(parsed -> 'output_config' ->> 'effort', '')
                );
                IF effort IS NULL OR length(effort) > 32 OR effort ~ '\\s' THEN
                    RETURN NULL;
                END IF;
                RETURN effort;
            EXCEPTION WHEN others THEN
                RETURN NULL;
            END;
            $function$;
            """))
    op.execute(sa.text("""
            UPDATE request_logs
            SET attempt_count = lens_request_log_attempt_count(attempts_json),
                reasoning_effort = COALESCE(
                    reasoning_effort,
                    lens_request_log_reasoning_effort(request_content),
                    lens_request_log_reasoning_effort(attempts_json)
                )
            WHERE attempt_count = 0 OR reasoning_effort IS NULL
            """))
    op.execute(sa.text("DROP FUNCTION lens_request_log_attempt_count(text)"))
    op.execute(sa.text("DROP FUNCTION lens_request_log_reasoning_effort(text)"))
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.alter_column("attempt_count", server_default=None)
        for index_name, columns in REQUEST_LOG_INDEXES:
            batch_op.create_index(index_name, columns, unique=False)


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        for index_name, _ in reversed(REQUEST_LOG_INDEXES):
            batch_op.drop_index(index_name)
        batch_op.drop_column("reasoning_effort")
        batch_op.drop_column("attempt_count")
