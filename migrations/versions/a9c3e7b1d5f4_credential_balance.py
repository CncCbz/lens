"""credential base url ownership and balance scripts

Revision ID: a9c3e7b1d5f4
Revises: b7e2a9c4d1f6
Create Date: 2026-10-09 00:00:00.000000

"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from lens_api.persistence.credential_ownership import assign_credential_base_urls

revision: str = "a9c3e7b1d5f4"
down_revision: Union[str, Sequence[str], None] = "b7e2a9c4d1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "balance_scripts",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
    )
    op.create_table(
        "credential_balances",
        sa.Column("credential_id", sa.String(length=80), primary_key=True),
        sa.Column("total", sa.Text(), nullable=False, server_default=""),
        sa.Column("balance", sa.Text(), nullable=False, server_default=""),
        sa.Column("currency", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "site_credentials",
        sa.Column("base_url_id", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "site_credentials",
        sa.Column("balance_script_id", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_site_credentials_base_url_id",
        "site_credentials",
        ["base_url_id"],
    )
    op.create_index(
        "ix_site_credentials_balance_script_id",
        "site_credentials",
        ["balance_script_id"],
    )
    _backfill_credential_base_urls()
    op.alter_column(
        "site_credentials",
        "base_url_id",
        existing_type=sa.String(length=80),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_site_credentials_balance_script_id", table_name="site_credentials"
    )
    op.drop_index("ix_site_credentials_base_url_id", table_name="site_credentials")
    op.drop_column("site_credentials", "balance_script_id")
    op.drop_column("site_credentials", "base_url_id")
    op.drop_table("credential_balances")
    op.drop_table("balance_scripts")


def _backfill_credential_base_urls() -> None:
    bind = op.get_bind()
    credentials = [
        dict(row)
        for row in bind.execute(
            sa.text(
                "SELECT id, site_id, name, api_key, enabled, cost_multiplier, "
                "sort_order FROM site_credentials"
            )
        ).mappings()
    ]
    base_urls = [
        dict(row)
        for row in bind.execute(
            sa.text("SELECT id, site_id, sort_order FROM site_base_urls")
        ).mappings()
    ]
    protocols = [
        dict(row)
        for row in bind.execute(
            sa.text(
                "SELECT id, site_id, credential_id, base_url_id "
                "FROM site_protocol_configs"
            )
        ).mappings()
    ]
    by_site_credentials: dict[str, list[dict]] = defaultdict(list)
    by_site_urls: dict[str, list[dict]] = defaultdict(list)
    by_site_protocols: dict[str, list[dict]] = defaultdict(list)
    for row in credentials:
        by_site_credentials[str(row["site_id"])].append(row)
    for row in base_urls:
        by_site_urls[str(row["site_id"])].append(row)
    for row in protocols:
        by_site_protocols[str(row["site_id"])].append(row)

    original_by_protocol = {
        str(row["id"]): str(row["credential_id"]) for row in protocols
    }
    for site_id in set(by_site_credentials) | set(by_site_urls):
        assigned, protocol_credentials = assign_credential_base_urls(
            by_site_credentials.get(site_id, []),
            by_site_urls.get(site_id, []),
            by_site_protocols.get(site_id, []),
        )
        existing_ids = {str(row["id"]) for row in by_site_credentials.get(site_id, [])}
        for credential in assigned:
            if str(credential["id"]) in existing_ids:
                bind.execute(
                    sa.text(
                        "UPDATE site_credentials SET base_url_id = :base_url_id "
                        "WHERE id = :id"
                    ),
                    {
                        "id": credential["id"],
                        "base_url_id": credential["base_url_id"],
                    },
                )
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO site_credentials ("
                    "id, site_id, name, api_key, enabled, cost_multiplier, "
                    "sort_order, base_url_id, balance_script_id"
                    ") VALUES ("
                    ":id, :site_id, :name, :api_key, :enabled, :cost_multiplier, "
                    ":sort_order, :base_url_id, NULL"
                    ")"
                ),
                {
                    "id": credential["id"],
                    "site_id": credential["site_id"],
                    "name": credential["name"],
                    "api_key": credential["api_key"],
                    "enabled": credential["enabled"],
                    "cost_multiplier": credential["cost_multiplier"],
                    "sort_order": credential["sort_order"],
                    "base_url_id": credential["base_url_id"],
                },
            )
        for protocol_id, credential_id in protocol_credentials.items():
            previous = original_by_protocol.get(protocol_id)
            if previous is None or previous == credential_id:
                continue
            bind.execute(
                sa.text(
                    "UPDATE site_protocol_configs SET credential_id = :credential_id "
                    "WHERE id = :id"
                ),
                {"id": protocol_id, "credential_id": credential_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE site_discovered_models SET credential_id = :credential_id "
                    "WHERE protocol_config_id = :protocol_config_id "
                    "AND credential_id = :previous"
                ),
                {
                    "protocol_config_id": protocol_id,
                    "credential_id": credential_id,
                    "previous": previous,
                },
            )
