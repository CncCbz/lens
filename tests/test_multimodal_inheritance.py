from __future__ import annotations

from types import SimpleNamespace

import pytest

from lens_api.core import multimodal_modes as mm


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
    assert mm.supports_media(group, "image") is expected


def test_manual_without_the_modality_is_unsupported() -> None:
    group = _group(multimodal="manual", multimodal_overrides={"text": True})
    assert mm.supports_media(group, "image") is False
    assert mm.effective_modalities(group) == ["text"]


def test_inherited_mode_and_overrides_win_over_auto() -> None:
    group = _group(
        multimodal="auto",
        multimodal_resolved={"image": False},
        multimodal_effective="manual",
        multimodal_effective_overrides={"image": True},
        multimodal_inherited_from="parent",
    )
    assert mm.supports_media(group, "image") is True
    assert mm.effective_modalities(group) == ["image"]


def test_inherited_off_disables_the_modality() -> None:
    group = _group(
        multimodal="auto",
        multimodal_resolved={"image": True},
        multimodal_effective="off",
        multimodal_inherited_from="parent",
    )
    assert mm.supports_media(group, "image") is False
    assert mm.effective_modalities(group) == []


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
    assert mm.is_explicit(_group(**kwargs)) is expected
