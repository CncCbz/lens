"""drop model group param override

Revision ID: b5a7c9d1e3f2
Revises: a4f8c2e6b0d1
Create Date: 2026-04-09 00:00:00.000000

"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from lens_api.core.match_overrides import param_override_to_match_rule

revision: str = "b5a7c9d1e3f2"
down_revision: Union[str, Sequence[str], None] = "a4f8c2e6b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, param_override_json, match_overrides_json FROM model_groups"
        )
    ).mappings()
    for row in rows:
        try:
            override = json.loads(row["param_override_json"] or "{}")
        except json.JSONDecodeError:
            override = {}
        try:
            rules = json.loads(row["match_overrides_json"] or "[]")
        except json.JSONDecodeError:
            rules = []
        if not isinstance(rules, list):
            rules = []
        rule = param_override_to_match_rule(
            override if isinstance(override, dict) else None
        )
        if rule is not None:
            conn.execute(
                sa.text(
                    "UPDATE model_groups SET match_overrides_json = :rules WHERE id = :id"
                ),
                {
                    "rules": json.dumps([rule, *rules], ensure_ascii=True),
                    "id": row["id"],
                },
            )
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.drop_column("param_override_json")


def downgrade() -> None:
    with op.batch_alter_table("model_groups") as batch_op:
        batch_op.add_column(
            sa.Column(
                "param_override_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )
