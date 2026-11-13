import hashlib
import json
from copy import deepcopy
from typing import Any, AsyncIterator, Callable

from ...core.protocol_reachability import (
    CHAT_PROTOCOLS,
    CLASSIFIER_PROTOCOLS,
    IMAGE_PROTOCOLS,
    can_reach_protocol,
    needs_conversion,
    wire_protocol,
)
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
    normalize_openai_responses_request,
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

_CHAT_PROTOCOLS = CHAT_PROTOCOLS

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


from .chat_to_bedrock import (
    bedrock_request_to_chat,
    chat_request_to_bedrock,
    bedrock_response_to_chat,
    chat_response_to_bedrock,
    bedrock_stream_to_chat_stream,
    chat_stream_to_bedrock_stream,
)
from .chat_to_pi import (
    pi_request_to_chat,
    chat_request_to_pi,
    pi_response_to_chat,
    chat_response_to_pi,
    pi_stream_to_chat_stream,
    chat_stream_to_pi_stream,
)
from .provider_variants import (
    mistral_request_to_chat,
    chat_request_to_mistral,
    mistral_response_to_chat,
    chat_response_to_mistral,
    mistral_stream_to_chat_stream,
    chat_stream_to_mistral_stream,
)

for _protocol, _family in (
    (ProtocolKind.AZURE_OPENAI_RESPONSES, ProtocolKind.OPENAI_RESPONSES),
    (ProtocolKind.OPENAI_CODEX_RESPONSES, ProtocolKind.OPENAI_RESPONSES),
    (ProtocolKind.GOOGLE_VERTEX, ProtocolKind.GEMINI),
):
    for _registry in (
        _TO_CHAT_REQUEST,
        _FROM_CHAT_REQUEST,
        _TO_CHAT_RESPONSE,
        _FROM_CHAT_RESPONSE,
        _TO_CHAT_STREAM,
        _FROM_CHAT_STREAM,
    ):
        _registry[_protocol] = _registry[_family]

for _protocol, _functions in (
    (
        ProtocolKind.MISTRAL_CONVERSATIONS,
        (
            mistral_request_to_chat,
            chat_request_to_mistral,
            mistral_response_to_chat,
            chat_response_to_mistral,
            mistral_stream_to_chat_stream,
            chat_stream_to_mistral_stream,
        ),
    ),
    (
        ProtocolKind.BEDROCK_CONVERSE_STREAM,
        (
            bedrock_request_to_chat,
            chat_request_to_bedrock,
            bedrock_response_to_chat,
            chat_response_to_bedrock,
            bedrock_stream_to_chat_stream,
            chat_stream_to_bedrock_stream,
        ),
    ),
    (
        ProtocolKind.PI_MESSAGES,
        (
            pi_request_to_chat,
            chat_request_to_pi,
            pi_response_to_chat,
            chat_response_to_pi,
            pi_stream_to_chat_stream,
            chat_stream_to_pi_stream,
        ),
    ),
):
    for _registry, _function in zip(
        (
            _TO_CHAT_REQUEST,
            _FROM_CHAT_REQUEST,
            _TO_CHAT_RESPONSE,
            _FROM_CHAT_RESPONSE,
            _TO_CHAT_STREAM,
            _FROM_CHAT_STREAM,
        ),
        _functions,
    ):
        _registry[_protocol] = _function


def _same_wire_format(left: ProtocolKind, right: ProtocolKind) -> bool:
    return left == right or (
        ProtocolKind.MISTRAL_CONVERSATIONS not in {left, right}
        and wire_protocol(left) == wire_protocol(right)
    )


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


def _without_transport_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if not key.startswith(("_bedrock_", "_pi_", "_mistral_"))
    }


def _chat_request_without_provider_metadata(
    body: dict[str, Any], *, preserve_reasoning: bool
) -> dict[str, Any]:
    body = _without_transport_metadata(body)
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    cleaned: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            cleaned.append(message)
            continue
        message = _without_transport_metadata(message)
        for key in (
            "_responses_reasoning_items",
            "_gemini_text_parts",
            "reasoning_blocks",
            "reasoning_signature",
            "reasoning_redacted",
            "is_error",
        ):
            message.pop(key, None)
        if not preserve_reasoning:
            message.pop("reasoning_content", None)
            message.pop("reasoning", None)
        content = message.get("content")
        if isinstance(content, list):
            message["content"] = [
                (
                    {
                        key: value
                        for key, value in part.items()
                        if key not in {"thought", "thoughtSignature"}
                        and not key.startswith(("_bedrock_", "_pi_", "_mistral_"))
                    }
                    if isinstance(part, dict)
                    else part
                )
                for part in content
                if not isinstance(part, dict) or part.get("thought") is not True
            ]
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            message["tool_calls"] = [
                (
                    {
                        key: value
                        for key, value in call.items()
                        if key != "thoughtSignature"
                        and not key.startswith(("_bedrock_", "_pi_", "_mistral_"))
                    }
                    if isinstance(call, dict)
                    else call
                )
                for call in tool_calls
            ]
        cleaned.append(message)
    return {**body, "messages": cleaned}


_OPAQUE_PROTOCOLS = {
    ProtocolKind.MISTRAL_CONVERSATIONS,
    ProtocolKind.BEDROCK_CONVERSE_STREAM,
    ProtocolKind.PI_MESSAGES,
}


def _signature_boundary(source: ProtocolKind, target: ProtocolKind) -> bool:
    return source != target and bool({source, target} & _OPAQUE_PROTOCOLS)


def _clean_foreign_message(
    message: dict[str, Any], *, plain_reasoning: bool, delta: bool = False
) -> dict[str, Any]:
    result = _without_transport_metadata(deepcopy(message))
    for key in (
        "reasoning_signature",
        "reasoning_redacted",
        "text_signature",
        "_responses_reasoning_items",
    ):
        result.pop(key, None)
    blocks = result.get("reasoning_blocks")
    if isinstance(blocks, list):
        result["reasoning_blocks"] = [
            {
                key: value
                for key, value in block.items()
                if key not in {"signature", "data"}
            }
            for block in blocks
            if isinstance(block, dict) and block.get("type") != "redacted_thinking"
        ]
    for key in ("content", "_gemini_text_parts", "tool_calls"):
        parts = result.get(key)
        if isinstance(parts, list):
            result[key] = [
                (
                    {
                        name: value
                        for name, value in part.items()
                        if name
                        not in {
                            "thoughtSignature",
                            "thought_signature",
                            "text_signature",
                            "textSignature",
                            "thinkingSignature",
                            "signature",
                        }
                    }
                    if isinstance(part, dict)
                    else part
                )
                for part in parts
            ]
    if plain_reasoning:
        reasoning = result.pop("reasoning_content", None)
        if not isinstance(reasoning, str):
            reasoning = result.get("reasoning")
        if not isinstance(reasoning, str):
            reasoning = ""
            if not delta and isinstance(blocks, list):
                reasoning = "".join(
                    block.get("thinking", "")
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "thinking"
                    and isinstance(block.get("thinking"), str)
                )
        result.pop("reasoning_blocks", None)
        result.pop("reasoning", None)
        if reasoning:
            content = result.get("content")
            if isinstance(content, list):
                result["content"] = [{"type": "text", "text": reasoning}, *content]
            else:
                result["content"] = reasoning + (
                    content if isinstance(content, str) else ""
                )
    return result


def _clean_foreign_chat(body: dict[str, Any], target: ProtocolKind) -> dict[str, Any]:
    result = _without_transport_metadata(deepcopy(body))
    plain = (
        target in {ProtocolKind.ANTHROPIC, ProtocolKind.BEDROCK_CONVERSE_STREAM}
        or wire_protocol(target) == ProtocolKind.OPENAI_RESPONSES
    )
    messages = result.get("messages")
    if isinstance(messages, list):
        result["messages"] = [
            (
                _clean_foreign_message(m, plain_reasoning=plain)
                if isinstance(m, dict)
                else m
            )
            for m in messages
        ]
    choices = result.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if isinstance(choice, dict):
                for key in ("message", "delta"):
                    if isinstance(choice.get(key), dict):
                        choice[key] = _clean_foreign_message(
                            choice[key], plain_reasoning=plain, delta=key == "delta"
                        )
    return result


async def _clean_foreign_stream(
    iterator: AsyncIterator[bytes], target: ProtocolKind
) -> AsyncIterator[bytes]:
    from ._shared import _parse_sse_stream, format_sse_event

    reasoning_hashes: dict[int, Any] = {}
    async for event in _parse_sse_stream(iterator):
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict) or not isinstance(
                choice.get("delta"), dict
            ):
                continue
            index = choice.get("index", 0)
            if not isinstance(index, int):
                raise ValueError("Invalid streaming choice index")
            delta = choice["delta"]
            text = delta.get("reasoning_content", delta.get("reasoning"))
            if isinstance(text, str):
                reasoning_hashes.setdefault(index, hashlib.sha256()).update(
                    text.encode("utf-8")
                )
            blocks = delta.get("reasoning_blocks")
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "thinking":
                        completed = block.get("thinking")
                        digest = reasoning_hashes.pop(index, None)
                        if (
                            isinstance(completed, str)
                            and digest is not None
                            and hashlib.sha256(completed.encode("utf-8")).digest()
                            == digest.digest()
                        ):
                            # Keep only this event's new text; the completed block
                            # also repeats the deltas sent in earlier events.
                            block["thinking"] = text if isinstance(text, str) else ""
        yield format_sse_event(None, _clean_foreign_chat(event, target))
    yield format_sse_event(None, "[DONE]")


def convert_request(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    body: dict[str, Any],
    target_model: str | None = None,
    preserve_reasoning: bool = False,
) -> dict[str, Any]:
    if client_protocol in IMAGE_PROTOCOLS and channel_protocol in IMAGE_PROTOCOLS:
        from .image_protocols import image_request

        result = (
            dict(body)
            if client_protocol == channel_protocol
            else image_request(
                body, to_openrouter=channel_protocol == ProtocolKind.OPENROUTER_IMAGES
            )
        )
        if target_model:
            result["model"] = target_model
        return result
    if (
        client_protocol in CLASSIFIER_PROTOCOLS
        and channel_protocol in CLASSIFIER_PROTOCOLS
    ):
        from .classifier_protocols import convert_classifier_request

        return convert_classifier_request(
            client_protocol, channel_protocol, body, target_model
        )
    _ensure_chat_protocol_pair(client_protocol, channel_protocol)
    if target_model:
        body = {**body, "model": target_model}
    if _same_wire_format(client_protocol, channel_protocol):
        if client_protocol == ProtocolKind.OPENAI_CHAT:
            result = normalize_openai_chat_request(body)
        elif client_protocol in {
            ProtocolKind.OPENAI_RESPONSES,
            ProtocolKind.AZURE_OPENAI_RESPONSES,
            ProtocolKind.OPENAI_CODEX_RESPONSES,
        }:
            result = normalize_openai_responses_request(body)
        else:
            result = dict(body)
    elif _signature_boundary(client_protocol, channel_protocol):
        chat_body = _clean_foreign_chat(
            _request_to_chat(client_protocol, deepcopy(body), preserve_reasoning),
            channel_protocol,
        )
        if channel_protocol in {
            ProtocolKind.OPENAI_CHAT,
            ProtocolKind.MISTRAL_CONVERSATIONS,
        }:
            chat_body = _chat_request_without_provider_metadata(
                chat_body,
                preserve_reasoning=preserve_reasoning
                or channel_protocol == ProtocolKind.MISTRAL_CONVERSATIONS,
            )
        result = (
            chat_request_to_responses(chat_body, preserve_reasoning=preserve_reasoning)
            if wire_protocol(channel_protocol) == ProtocolKind.OPENAI_RESPONSES
            else _FROM_CHAT_REQUEST[channel_protocol](chat_body)
        )
    elif channel_protocol == ProtocolKind.OPENAI_CHAT:
        result = _chat_request_without_provider_metadata(
            _request_to_chat(client_protocol, body, preserve_reasoning),
            preserve_reasoning=preserve_reasoning,
        )
    elif channel_protocol == ProtocolKind.MISTRAL_CONVERSATIONS:
        chat_body = (
            _request_to_chat(client_protocol, body, preserve_reasoning)
            if client_protocol != ProtocolKind.OPENAI_CHAT
            else body
        )
        result = chat_request_to_mistral(
            _chat_request_without_provider_metadata(chat_body, preserve_reasoning=True)
        )
    elif client_protocol == ProtocolKind.OPENAI_CHAT:
        result = (
            chat_request_to_responses(body, preserve_reasoning=preserve_reasoning)
            if wire_protocol(channel_protocol) == ProtocolKind.OPENAI_RESPONSES
            else _FROM_CHAT_REQUEST[channel_protocol](body)
        )
    else:
        chat_body = _request_to_chat(client_protocol, body, preserve_reasoning)
        chat_body["model"] = body.get("model") or ""
        result = (
            chat_request_to_responses(chat_body, preserve_reasoning=preserve_reasoning)
            if wire_protocol(channel_protocol) == ProtocolKind.OPENAI_RESPONSES
            else _FROM_CHAT_REQUEST[channel_protocol](chat_body)
        )
    routing_model = target_model or body.get("model")
    if isinstance(routing_model, str) and routing_model:
        result["model"] = routing_model
    return result


def convert_response(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    response_body: bytes,
    original_model: str = "",
    *,
    request_body: dict[str, Any] | None = None,
) -> bytes:
    response_data = json.loads(response_body)
    if not isinstance(response_data, dict):
        raise ValueError("Upstream response JSON must be an object")
    if client_protocol in IMAGE_PROTOCOLS and channel_protocol in IMAGE_PROTOCOLS:
        from .image_protocols import image_response

        converted = (
            response_data
            if client_protocol == channel_protocol
            else image_response(
                response_data,
                to_openrouter=client_protocol == ProtocolKind.OPENROUTER_IMAGES,
                original_model=original_model,
            )
        )
        return json.dumps(converted, ensure_ascii=False).encode("utf-8")
    if (
        client_protocol in CLASSIFIER_PROTOCOLS
        and channel_protocol in CLASSIFIER_PROTOCOLS
    ):
        from .classifier_protocols import convert_classifier_response

        converted = convert_classifier_response(
            client_protocol,
            channel_protocol,
            response_data,
            original_model,
            request_body=request_body,
        )
        return json.dumps(converted, ensure_ascii=False).encode("utf-8")
    _ensure_chat_protocol_pair(client_protocol, channel_protocol)
    if _same_wire_format(client_protocol, channel_protocol):
        converted = response_data
    elif _signature_boundary(channel_protocol, client_protocol):
        chat_body = _clean_foreign_chat(
            _TO_CHAT_RESPONSE[channel_protocol](response_data, original_model),
            client_protocol,
        )
        converted = _FROM_CHAT_RESPONSE[client_protocol](chat_body, original_model)
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
    if _same_wire_format(client_protocol, channel_protocol):
        stream = raw_iterator
    elif _signature_boundary(channel_protocol, client_protocol):
        chat_stream = _clean_foreign_stream(
            _TO_CHAT_STREAM[channel_protocol](raw_iterator, original_model),
            client_protocol,
        )
        stream = _FROM_CHAT_STREAM[client_protocol](chat_stream, original_model)
    elif channel_protocol == ProtocolKind.OPENAI_CHAT:
        stream = _FROM_CHAT_STREAM[client_protocol](raw_iterator, original_model)
    elif client_protocol == ProtocolKind.OPENAI_CHAT:
        stream = _TO_CHAT_STREAM[channel_protocol](raw_iterator, original_model)
    else:
        chat_stream = _TO_CHAT_STREAM[channel_protocol](raw_iterator, original_model)
        stream = _FROM_CHAT_STREAM[client_protocol](chat_stream, original_model)
    async for chunk in stream:
        yield chunk
