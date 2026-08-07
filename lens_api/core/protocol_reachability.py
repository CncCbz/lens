from __future__ import annotations

from ..models import ProtocolKind

_CHAT_CONVERTIBLE_PROTOCOLS: frozenset[ProtocolKind] = frozenset(
    {
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.ANTHROPIC,
        ProtocolKind.GEMINI,
    }
)

SUPPORTED_CONVERSIONS: frozenset[tuple[str, str]] = frozenset(
    (channel_protocol.value, client_protocol.value)
    for channel_protocol in _CHAT_CONVERTIBLE_PROTOCOLS
    for client_protocol in _CHAT_CONVERTIBLE_PROTOCOLS
    if channel_protocol != client_protocol
)


def can_reach_protocol(
    channel_protocol: ProtocolKind, group_protocol: ProtocolKind
) -> bool:
    if channel_protocol == group_protocol:
        return True
    return (channel_protocol.value, group_protocol.value) in SUPPORTED_CONVERSIONS


def needs_conversion(
    client_protocol: ProtocolKind, channel_protocol: ProtocolKind
) -> bool:
    return (channel_protocol.value, client_protocol.value) in SUPPORTED_CONVERSIONS


def conversion_matrix() -> dict[str, list[str]]:
    matrix: dict[str, list[str]] = {p.value: [p.value] for p in ProtocolKind}
    for channel_value, reachable_value in SUPPORTED_CONVERSIONS:
        targets = matrix.setdefault(channel_value, [channel_value])
        if reachable_value not in targets:
            targets.append(reachable_value)
    return matrix
