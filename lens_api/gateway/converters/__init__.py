import json
from typing import Any, AsyncIterator, Callable

from ...core.protocol_reachability import can_reach_protocol, needs_conversion
from ...models import ProtocolKind
from .openai_chat_compat import normalize_openai_chat_request
from .chat_to_anthropic import (
    anthropic_request_to_chat,
    anthropic_response_to_chat,
    anthropic_stream_to_chat_stream,
    chat_request_to_anthropic,
    chat_response_to_anthropic,
    chat_stream_to_anthropic_stream,
)
from .chat_to_gemini import (
    chat_request_to_gemini,
    chat_response_to_gemini,
    chat_stream_to_gemini_stream,
    gemini_request_to_chat,
    gemini_response_to_chat,
    gemini_stream_to_chat_stream,
)
from .chat_to_responses import (
    chat_request_to_responses,
    chat_response_to_responses,
    chat_stream_to_responses_stream,
    responses_request_to_chat,
    responses_response_to_chat,
    responses_stream_to_chat_stream,
)

__all__ = [
    "can_reach_protocol",
    "needs_conversion",
    "convert_request",
    "convert_response",
    "convert_stream_iterator",
]

_CHAT_PROTOCOLS = frozenset(
    {
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.ANTHROPIC,
        ProtocolKind.GEMINI,
    }
)

_ToChatRequest = Callable[[dict[str, Any]], dict[str, Any]]
_FromChatRequest = Callable[[dict[str, Any]], dict[str, Any]]
_ToChatResponse = Callable[[dict[str, Any], str], dict[str, Any]]
_FromChatResponse = Callable[[dict[str, Any], str], dict[str, Any]]
_StreamConverter = Callable[[AsyncIterator[bytes], str], AsyncIterator[bytes]]


def _identity_request(body: dict[str, Any]) -> dict[str, Any]:
    return dict(body)


def _identity_chat_request(body: dict[str, Any]) -> dict[str, Any]:
    return normalize_openai_chat_request(body)


def _identity_response(
    body: dict[str, Any], _original_model: str = ""
) -> dict[str, Any]:
    return dict(body)


async def _identity_stream(
    raw_iterator: AsyncIterator[bytes], _original_model: str = ""
) -> AsyncIterator[bytes]:
    async for chunk in raw_iterator:
        yield chunk


_TO_CHAT_REQUEST: dict[ProtocolKind, _ToChatRequest] = {
    ProtocolKind.OPENAI_CHAT: _identity_chat_request,
    ProtocolKind.OPENAI_RESPONSES: responses_request_to_chat,
    ProtocolKind.ANTHROPIC: anthropic_request_to_chat,
    ProtocolKind.GEMINI: gemini_request_to_chat,
}
_FROM_CHAT_REQUEST: dict[ProtocolKind, _FromChatRequest] = {
    ProtocolKind.OPENAI_CHAT: _identity_chat_request,
    ProtocolKind.OPENAI_RESPONSES: chat_request_to_responses,
    ProtocolKind.ANTHROPIC: chat_request_to_anthropic,
    ProtocolKind.GEMINI: chat_request_to_gemini,
}
_TO_CHAT_RESPONSE: dict[ProtocolKind, _ToChatResponse] = {
    ProtocolKind.OPENAI_CHAT: _identity_response,
    ProtocolKind.OPENAI_RESPONSES: responses_response_to_chat,
    ProtocolKind.ANTHROPIC: anthropic_response_to_chat,
    ProtocolKind.GEMINI: gemini_response_to_chat,
}
_FROM_CHAT_RESPONSE: dict[ProtocolKind, _FromChatResponse] = {
    ProtocolKind.OPENAI_CHAT: _identity_response,
    ProtocolKind.OPENAI_RESPONSES: chat_response_to_responses,
    ProtocolKind.ANTHROPIC: chat_response_to_anthropic,
    ProtocolKind.GEMINI: chat_response_to_gemini,
}
_TO_CHAT_STREAM: dict[ProtocolKind, _StreamConverter] = {
    ProtocolKind.OPENAI_CHAT: _identity_stream,
    ProtocolKind.OPENAI_RESPONSES: responses_stream_to_chat_stream,
    ProtocolKind.ANTHROPIC: anthropic_stream_to_chat_stream,
    ProtocolKind.GEMINI: gemini_stream_to_chat_stream,
}
_FROM_CHAT_STREAM: dict[ProtocolKind, _StreamConverter] = {
    ProtocolKind.OPENAI_CHAT: _identity_stream,
    ProtocolKind.OPENAI_RESPONSES: chat_stream_to_responses_stream,
    ProtocolKind.ANTHROPIC: chat_stream_to_anthropic_stream,
    ProtocolKind.GEMINI: chat_stream_to_gemini_stream,
}


def _ensure_chat_protocol_pair(
    client_protocol: ProtocolKind, channel_protocol: ProtocolKind
) -> None:
    if (
        client_protocol not in _CHAT_PROTOCOLS
        or channel_protocol not in _CHAT_PROTOCOLS
    ):
        raise ValueError(
            f"Unsupported conversion: {client_protocol.value} -> {channel_protocol.value}"
        )


def _request_to_chat(
    protocol: ProtocolKind, body: dict[str, Any], preserve_reasoning: bool
) -> dict[str, Any]:
    if protocol == ProtocolKind.ANTHROPIC:
        return anthropic_request_to_chat(body, preserve_thinking=preserve_reasoning)
    return _TO_CHAT_REQUEST[protocol](body)


def convert_request(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    body: dict[str, Any],
    target_model: str | None = None,
    preserve_reasoning: bool = False,
) -> dict[str, Any]:
    _ensure_chat_protocol_pair(client_protocol, channel_protocol)
    if client_protocol == channel_protocol:
        result = (
            normalize_openai_chat_request(body)
            if client_protocol == ProtocolKind.OPENAI_CHAT
            else dict(body)
        )
    elif channel_protocol == ProtocolKind.OPENAI_CHAT:
        result = _request_to_chat(client_protocol, body, preserve_reasoning)
    elif client_protocol == ProtocolKind.OPENAI_CHAT:
        result = _FROM_CHAT_REQUEST[channel_protocol](body)
    else:
        chat_body = _request_to_chat(client_protocol, body, preserve_reasoning)
        result = _FROM_CHAT_REQUEST[channel_protocol](chat_body)
    if target_model:
        result["model"] = target_model
    return result


def convert_response(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    response_body: bytes,
    original_model: str = "",
) -> bytes:
    _ensure_chat_protocol_pair(client_protocol, channel_protocol)
    response_data = json.loads(response_body)
    if not isinstance(response_data, dict):
        raise ValueError("Upstream response JSON must be an object")
    if client_protocol == channel_protocol:
        converted = response_data
    elif channel_protocol == ProtocolKind.OPENAI_CHAT:
        converted = _FROM_CHAT_RESPONSE[client_protocol](response_data, original_model)
    elif client_protocol == ProtocolKind.OPENAI_CHAT:
        converted = _TO_CHAT_RESPONSE[channel_protocol](response_data, original_model)
    else:
        chat_body = _TO_CHAT_RESPONSE[channel_protocol](response_data, original_model)
        converted = _FROM_CHAT_RESPONSE[client_protocol](chat_body, original_model)
    return json.dumps(converted, ensure_ascii=False).encode("utf-8")


async def convert_stream_iterator(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    raw_iterator: AsyncIterator[bytes],
    original_model: str = "",
) -> AsyncIterator[bytes]:
    _ensure_chat_protocol_pair(client_protocol, channel_protocol)
    if client_protocol == channel_protocol:
        stream = raw_iterator
    elif channel_protocol == ProtocolKind.OPENAI_CHAT:
        stream = _FROM_CHAT_STREAM[client_protocol](raw_iterator, original_model)
    elif client_protocol == ProtocolKind.OPENAI_CHAT:
        stream = _TO_CHAT_STREAM[channel_protocol](raw_iterator, original_model)
    else:
        chat_stream = _TO_CHAT_STREAM[channel_protocol](raw_iterator, original_model)
        stream = _FROM_CHAT_STREAM[client_protocol](chat_stream, original_model)
    async for chunk in stream:
        yield chunk
