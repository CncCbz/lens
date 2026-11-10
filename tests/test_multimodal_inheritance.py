from __future__ import annotations

from types import SimpleNamespace

import pytest

from lens_api.core import multimodal_modes
from lens_api.models import ModelGroup


def _group(**kwargs: object) -> SimpleNamespace:
    base = {
        "multimodal": "auto",
        "multimodal_overrides": {},
        "multimodal_resolved": {},
        "multimodal_effective": None,
        "multimodal_effective_overrides": None,
        "multimodal_inherited_from": "",
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("on", True), ("off", False), ("manual", True), ("auto", True)],
)
def test_supports_media_follows_the_declared_mode(mode: str, expected: bool) -> None:
    group = _group(
        multimodal=mode,
        multimodal_overrides={"image": True},
        multimodal_resolved={"image": True},
    )
    assert multimodal_modes.supports_media(group, "image") is expected


def test_manual_without_the_modality_is_unsupported() -> None:
    group = _group(multimodal="manual", multimodal_overrides={"text": True})
    assert multimodal_modes.supports_media(group, "image") is False
    assert multimodal_modes.effective_modalities(group) == ["text"]


def test_inherited_mode_and_overrides_win_over_auto() -> None:
    group = _group(
        multimodal="auto",
        multimodal_resolved={"image": False},
        multimodal_effective="manual",
        multimodal_effective_overrides={"image": True},
        multimodal_inherited_from="parent",
    )
    assert multimodal_modes.supports_media(group, "image") is True
    assert multimodal_modes.effective_modalities(group) == ["image"]


def test_inherited_off_disables_the_modality() -> None:
    group = _group(
        multimodal="auto",
        multimodal_resolved={"image": True},
        multimodal_effective="off",
        multimodal_inherited_from="parent",
    )
    assert multimodal_modes.supports_media(group, "image") is False
    assert multimodal_modes.effective_modalities(group) == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"multimodal": "auto", "multimodal_overrides": {}}, False),
        ({"multimodal": "auto", "multimodal_overrides": {"image": False}}, True),
        ({"multimodal": "manual", "multimodal_overrides": {}}, True),
        ({"multimodal": "on", "multimodal_overrides": {}}, True),
        ({"multimodal": "off", "multimodal_overrides": {}}, True),
    ],
)
def test_is_explicit_detects_own_configuration(
    kwargs: dict[str, object], expected: bool
) -> None:
    assert multimodal_modes.is_explicit(_group(**kwargs)) is expected


def _chain(*groups: dict[str, object]) -> list[ModelGroup]:
    return [ModelGroup(**group) for group in groups]


def _root(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "root",
        "name": "root",
        "protocols": ["openai_chat"],
        "strategy": "round_robin",
        "multimodal": "manual",
        "multimodal_overrides": {"image": True, "text": True},
    }
    base.update(overrides)
    return base


def _variant(name: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": name,
        "name": name,
        "protocols": ["openai_chat"],
        "strategy": "round_robin",
        "route_group_id": "root",
        "multimodal": "auto",
    }
    base.update(overrides)
    return base


def test_grandchild_variant_inherits_through_an_intermediate() -> None:
    """A variant of a variant still reaches the ancestor that declares a config."""
    chain = _chain(
        _root(),
        _variant("mid", **{"route_group_id": "root"}),
        _variant("leaf", **{"route_group_id": "mid"}),
    )
    groups = {group.name: group for group in chain}
    for group in chain:
        group.multimodal_inherited_from = ""
    _apply(chain)
    assert groups["leaf"].multimodal_inherited_from == "root"
    assert multimodal_modes.supports_media(groups["leaf"], "image") is True


def _apply(groups: list[ModelGroup]) -> None:
    """Run the repository inheritance walk on already-parsed groups."""
    by_id = {group.id: group for group in groups}
    for group in groups:
        if not group.route_group_id.strip():
            continue
        if not multimodal_modes.is_explicit(group):
            current, hops = group, 0
            while hops < 32:
                target = by_id.get(current.route_group_id.strip())
                if target is None:
                    break
                if multimodal_modes.is_explicit(target):
                    group.multimodal_inherited_from = target.name
                    group.multimodal_effective = multimodal_modes.inherited_mode(target)
                    group.multimodal_effective_overrides = (
                        multimodal_modes.inherited_overrides(target)
                    )
                    break
                current = target
                hops += 1
