"""migrate standalone free/paid variant groups into route variants

Revision ID: d8f2a4c6e9b1
Revises: c7f1a2b8d3e4
Create Date: 2026-11-01 18:00:00.000000

"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d8f2a4c6e9b1"
down_revision: Union[str, Sequence[str], None] = "c7f1a2b8d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _extract_variant_base_name(name: str) -> str:
    trimmed = name.strip()
    lower = trimmed.lower()
    for suffix in ("-free", "-paid"):
        if lower.endswith(suffix) and len(trimmed) > len(suffix):
            return trimmed[: -len(suffix)].strip()
    return ""


def _parse_str_list(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def upgrade() -> None:
    conn = op.get_bind()
    group_rows = list(
        conn.execute(
            sa.text(
                "SELECT id, name, protocols_json, route_group_id, match_overrides_json "
                "FROM model_groups"
            )
        ).mappings()
    )
    if not group_rows:
        return

    exec_by_name: dict[str, dict[str, object]] = {}
    groups_by_id: dict[str, dict[str, object]] = {}
    for row in group_rows:
        gid = str(row["id"] or "").strip()
        gname = str(row["name"] or "").strip()
        route_gid = str(row["route_group_id"] or "").strip()
        info = {
            "id": gid,
            "name": gname,
            "protocols": set(_parse_str_list(str(row["protocols_json"] or "[]"))),
            "route_group_id": route_gid,
            "match_overrides_json": str(row["match_overrides_json"] or "[]"),
        }
        if gid:
            groups_by_id[gid] = info
        if gname and not route_gid:
            exec_by_name[gname.lower()] = info

    reparented: dict[str, str] = {}
    for gid, info in groups_by_id.items():
        if info["route_group_id"]:
            continue
        base_name = _extract_variant_base_name(str(info["name"]))
        if not base_name:
            continue
        parent = exec_by_name.get(base_name.lower())
        if parent is None or parent["id"] == gid:
            continue
        parent_protocols = parent["protocols"]
        group_protocols = info["protocols"]
        if not isinstance(parent_protocols, set) or not isinstance(
            group_protocols, set
        ):
            continue
        if not group_protocols.issubset(parent_protocols):
            continue
        parent_id = str(parent["id"])
        conn.execute(
            sa.text(
                "UPDATE model_groups "
                "SET route_group_id = :parent_id, "
                "    pi_config_json = '', "
                "    pi_config_auto = 0, "
                "    sync_filter_mode = '', "
                "    sync_filter_query = '' "
                "WHERE id = :gid"
            ),
            {"parent_id": parent_id, "gid": gid},
        )
        reparented[gid] = parent_id

    if not reparented:
        return

    for gid, info in groups_by_id.items():
        old_target_id = str(info["route_group_id"] or "")
        if not old_target_id or old_target_id not in reparented:
            continue
        new_parent_id = reparented[old_target_id]
        intermediate = groups_by_id.get(old_target_id)
        existing_items = list(
            conn.execute(
                sa.text(
                    "SELECT id FROM model_group_items WHERE group_id = :gid LIMIT 1"
                ),
                {"gid": gid},
            ).mappings()
        )
        if not existing_items:
            source_items = list(
                conn.execute(
                    sa.text(
                        "SELECT channel_id, credential_id, model_name, enabled, priority, weight "
                        "FROM model_group_items "
                        "WHERE group_id = :source_gid "
                        "ORDER BY priority ASC, id ASC"
                    ),
                    {"source_gid": old_target_id},
                ).mappings()
            )
            for item in source_items:
                conn.execute(
                    sa.text(
                        "INSERT INTO model_group_items "
                        "(group_id, channel_id, credential_id, model_name, enabled, priority, weight) "
                        "VALUES (:group_id, :channel_id, :credential_id, :model_name, :enabled, :priority, :weight)"
                    ),
                    {
                        "group_id": gid,
                        "channel_id": item["channel_id"],
                        "credential_id": item["credential_id"],
                        "model_name": item["model_name"],
                        "enabled": item["enabled"],
                        "priority": item["priority"],
                        "weight": item["weight"],
                    },
                )
        child_overrides = str(info["match_overrides_json"] or "[]").strip()
        inter_overrides = (
            str(intermediate["match_overrides_json"] or "[]").strip()
            if intermediate is not None
            else "[]"
        )
        if child_overrides in ("", "[]") and inter_overrides not in ("", "[]"):
            conn.execute(
                sa.text(
                    "UPDATE model_groups "
                    "SET route_group_id = :parent_id, match_overrides_json = :overrides "
                    "WHERE id = :gid"
                ),
                {
                    "parent_id": new_parent_id,
                    "overrides": inter_overrides,
                    "gid": gid,
                },
            )
        else:
            conn.execute(
                sa.text(
                    "UPDATE model_groups SET route_group_id = :parent_id WHERE id = :gid"
                ),
                {"parent_id": new_parent_id, "gid": gid},
            )


def downgrade() -> None:
    pass
