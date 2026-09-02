"""drop model group headers

Revision ID: c6b8d0e2f4a1
Revises: b5a7c9d1e3f2
Create Date: 2026-04-09 00:00:00.000000

"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from lens_api.core.match_overrides import headers_to_match_actions

revision: str = "c6b8d0e2f4a1"
down_revision: Union[str, Sequence[str], None] = "b5a7c9d1e3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, headers_json, match_overrides_json FROM model_groups")
    ).mappings()
    for row in rows:
        try:
            headers = json.loads(row["headers_json"] or "{}")
        except json.JSONDecodeError:
            headers = {}
        try:
            rules = json.loads(row["match_overrides_json"] or "[]")
        except json.JSONDecodeError:
            rules = []
        if not isinstance(rules, list):
            rules = []
        then = headers_to_match_actions(headers) if isinstance(headers, dict) else []
        if then:
            conn.execute(
                sa.text(
                    "UPDATE model_groups SET match_overrides_json = :rules WHERE id = :id"
                ),
                {
                    "rules": json.dumps(
                        [{"if": {"all": []}, "then": then}, *rules],
                        ensure_ascii=True,
                    ),
                    "id": row["id"],
                },
            )
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("headers_json")


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "headers_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )
