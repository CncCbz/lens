"""replace model group item sort_order with priority and add weight

Revision ID: 88001bf35e79
Revises: e4b7a2c9f6d3
Create Date: 2026-08-09 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "88001bf35e79"
down_revision: Union[str, Sequence[str], None] = "e4b7a2c9f6d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model_group_items") as batch_op:
        batch_op.alter_column("sort_order", new_column_name="priority")
        batch_op.add_column(
            sa.Column("weight", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_check_constraint(
            "ck_model_group_items_priority_non_negative", "priority >= 0"
        )
        batch_op.create_check_constraint(
            "ck_model_group_items_weight_positive", "weight >= 1"
        )

    with op.batch_alter_table("model_group_items") as batch_op:
        batch_op.alter_column("weight", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("model_group_items") as batch_op:
        batch_op.drop_constraint("ck_model_group_items_weight_positive", type_="check")
        batch_op.drop_constraint(
            "ck_model_group_items_priority_non_negative", type_="check"
        )
        batch_op.drop_column("weight")
        batch_op.alter_column("priority", new_column_name="sort_order")
