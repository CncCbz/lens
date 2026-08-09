from __future__ import annotations

from lens_api.persistence.backup_store.export_import import _normalize_dump_group_items


def test_old_backup_maps_sort_order_to_priority() -> None:
    data: object = {
        "version": 7,
        "groups": [
            {
                "name": "g",
                "items": [
                    {"channel_id": "c", "sort_order": 2},
                    {"channel_id": "d", "sort_order": 0},
                ],
            },
        ],
    }
    out = _normalize_dump_group_items(data)
    assert isinstance(out, dict)
    groups = out["groups"]
    assert isinstance(groups, list)
    items = groups[0]["items"]
    assert items[0]["priority"] == 2
    assert items[0]["weight"] == 1
    assert "sort_order" not in items[0]
    assert items[1]["priority"] == 0
    assert items[1]["weight"] == 1


def test_new_backup_preserved() -> None:
    data: object = {
        "groups": [
            {
                "name": "g",
                "items": [
                    {"channel_id": "c", "priority": 1, "weight": 3},
                ],
            },
        ],
    }
    out = _normalize_dump_group_items(data)
    assert isinstance(out, dict)
    items = out["groups"][0]["items"]
    assert items[0]["priority"] == 1
    assert items[0]["weight"] == 3


def test_missing_weight_defaults_to_one() -> None:
    data: object = {
        "groups": [
            {
                "name": "g",
                "items": [{"channel_id": "c", "priority": 0}],
            },
        ],
    }
    out = _normalize_dump_group_items(data)
    assert isinstance(out, dict)
    assert out["groups"][0]["items"][0]["weight"] == 1


def test_non_group_dump_passthrough() -> None:
    data: object = {"version": 8, "sites": [], "settings": []}
    assert _normalize_dump_group_items(data) is data
