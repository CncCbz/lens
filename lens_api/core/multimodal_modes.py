"""Effective multimodal capability of a model group.

One implementation for the gateway relay, the admin screen and the pi config
export, so a route variant cannot be judged differently on each path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AUTO = "auto"
MANUAL = "manual"
# Legacy values kept for backward compatibility with stored groups.
ON = "on"
OFF = "off"

ALL_MODALITIES = ("text", "image", "video", "pdf", "audio")


def _effective_mode(group: Any) -> Any:
    """Mode that decides capability: the inherited one wins when present."""
    inherited = getattr(group, "multimodal_effective", None)
    if inherited is not None:
        return inherited
    return getattr(group, "multimodal", AUTO)


def _effective_overrides(group: Any) -> Mapping[str, bool] | None:
    inherited = getattr(group, "multimodal_effective_overrides", None)
    if isinstance(inherited, Mapping):
        return inherited
    overrides = getattr(group, "multimodal_overrides", None)
    return overrides if isinstance(overrides, Mapping) else None


def supports_media(group: Any, kind: str) -> bool:
    """Whether one group can handle a media kind natively."""
    if group is None:
        return False
    mode = _effective_mode(group)
    if mode == ON:
        return True
    if mode == OFF:
        return False
    if mode == MANUAL:
        overrides = _effective_overrides(group)
        if overrides is None:
            return False
        return bool(overrides.get(kind))
    resolved = getattr(group, "multimodal_resolved", None)
    if isinstance(resolved, Mapping):
        return bool(resolved.get(kind))
    return False


def effective_modalities(group: Any) -> list[str]:
    """Modalities one group can handle natively."""
    mode = _effective_mode(group)
    if mode == ON:
        return list(ALL_MODALITIES)
    if mode == OFF:
        return []
    if mode == MANUAL:
        overrides = _effective_overrides(group)
        if overrides is None:
            return []
        return [modality for modality, supported in overrides.items() if supported]
    resolved = getattr(group, "multimodal_resolved", None)
    if isinstance(resolved, Mapping):
        return [modality for modality, supported in resolved.items() if supported]
    return []


def is_explicit(group: Any) -> bool:
    """Whether the group carries its own multimodal config instead of inheriting."""
    mode = getattr(group, "multimodal", AUTO)
    if mode != AUTO:
        return True
    overrides = getattr(group, "multimodal_overrides", None)
    return isinstance(overrides, Mapping) and bool(overrides)
