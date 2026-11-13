from __future__ import annotations

from ..models import ProtocolKind

CHAT_PROTOCOLS: frozenset[ProtocolKind] = frozenset(
    {
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.ANTHROPIC,
        ProtocolKind.GEMINI,
        ProtocolKind.MISTRAL_CONVERSATIONS,
        ProtocolKind.AZURE_OPENAI_RESPONSES,
        ProtocolKind.OPENAI_CODEX_RESPONSES,
        ProtocolKind.GOOGLE_VERTEX,
        ProtocolKind.BEDROCK_CONVERSE_STREAM,
        ProtocolKind.PI_MESSAGES,
    }
)
IMAGE_PROTOCOLS: frozenset[ProtocolKind] = frozenset(
    {ProtocolKind.OPENAI_IMAGE, ProtocolKind.OPENROUTER_IMAGES}
)
CLASSIFIER_PROTOCOLS: frozenset[ProtocolKind] = frozenset(
    {
        ProtocolKind.TYPESAFE_SYSTEM_ONE,
        ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
        ProtocolKind.LLAMA_CPP_CLASSIFY,
        ProtocolKind.OPENAI_DECISIONS,
    }
)


def wire_protocol(protocol: ProtocolKind) -> ProtocolKind:
    return {
        ProtocolKind.AZURE_OPENAI_RESPONSES: ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.OPENAI_CODEX_RESPONSES: ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.GOOGLE_VERTEX: ProtocolKind.GEMINI,
        ProtocolKind.MISTRAL_CONVERSATIONS: ProtocolKind.OPENAI_CHAT,
    }.get(protocol, protocol)


SUPPORTED_CONVERSIONS: frozenset[tuple[str, str]] = frozenset(
    (channel_protocol.value, client_protocol.value)
    for family in (CHAT_PROTOCOLS, IMAGE_PROTOCOLS, CLASSIFIER_PROTOCOLS)
    for channel_protocol in family
    for client_protocol in family
    if channel_protocol != client_protocol
    and client_protocol != ProtocolKind.LLAMA_CPP_CLASSIFY
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
    for channel_value, reachable_value in sorted(SUPPORTED_CONVERSIONS):
        targets = matrix.setdefault(channel_value, [channel_value])
        if reachable_value not in targets:
            targets.append(reachable_value)
    return matrix
