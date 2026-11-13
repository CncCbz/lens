import json
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

import pytest

from lens_api.gateway.converters import (
    convert_request,
    convert_response,
    convert_stream_iterator,
)
from lens_api.gateway.converters.chat_to_bedrock import (
    chat_request_to_bedrock,
    chat_response_to_bedrock,
    chat_stream_to_bedrock_stream,
)
from lens_api.gateway.converters.chat_to_pi import (
    chat_request_to_pi,
    chat_response_to_pi,
    chat_stream_to_pi_stream,
)
from lens_api.gateway.converters.provider_variants import (
    chat_request_to_mistral,
    chat_response_to_mistral,
    chat_stream_to_mistral_stream,
)
from lens_api.models import ProtocolKind as P

SOURCES = [P.MISTRAL_CONVERSATIONS, P.BEDROCK_CONVERSE_STREAM, P.PI_MESSAGES]
TARGETS = [P.OPENAI_CHAT, P.ANTHROPIC, P.GEMINI, P.OPENAI_RESPONSES, *SOURCES]
REQUEST = {
    P.MISTRAL_CONVERSATIONS: chat_request_to_mistral,
    P.BEDROCK_CONVERSE_STREAM: chat_request_to_bedrock,
    P.PI_MESSAGES: chat_request_to_pi,
}
RESPONSE = {
    P.MISTRAL_CONVERSATIONS: chat_response_to_mistral,
    P.BEDROCK_CONVERSE_STREAM: chat_response_to_bedrock,
    P.PI_MESSAGES: chat_response_to_pi,
}
STREAM = {
    P.MISTRAL_CONVERSATIONS: chat_stream_to_mistral_stream,
    P.BEDROCK_CONVERSE_STREAM: chat_stream_to_bedrock_stream,
    P.PI_MESSAGES: chat_stream_to_pi_stream,
}


def chat_message() -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "answer",
        "reasoning_content": "private",
        "reasoning_signature": "FOREIGN_SIG",
        "reasoning_blocks": [
            {"type": "thinking", "thinking": "private", "signature": "FOREIGN_SIG"}
        ],
        "tool_calls": [
            {
                "id": "call1",
                "type": "function",
                "thought_signature": "FOREIGN_TOOL",
                "function": {"name": "weather", "arguments": '{"city":"北京"}'},
            }
        ],
    }


def assert_clean(value: Any) -> None:
    wire = json.dumps(value, ensure_ascii=False)
    assert "FOREIGN_SIG" not in wire
    assert "FOREIGN_TOOL" not in wire
    assert "b3BhcXVl" not in wire
    assert "answer" in wire
    assert "weather" in wire


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("preserve", [False, True])
def test_foreign_request_signatures_are_removed(
    source: P, target: P, preserve: bool
) -> None:
    message = chat_message()
    body = REQUEST[source](
        {
            "model": "claude-source",
            "messages": [message],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"signature": {"type": "string"}},
                        },
                    },
                }
            ],
        }
    )
    snapshot = deepcopy(body)
    output = convert_request(
        source, target, body, "claude-target", preserve_reasoning=preserve
    )
    assert body == snapshot
    if target == source:
        assert "FOREIGN_SIG" in json.dumps(output)
    else:
        assert_clean(output)
        if target != P.OPENAI_CHAT or preserve:
            assert "private" in json.dumps(output)
        assert '"signature": {"type": "string"}' in json.dumps(output)
        if target == P.ANTHROPIC:
            assert all(
                block.get("type") not in {"thinking", "redacted_thinking"}
                for item in output["messages"]
                for block in item.get("content", [])
            )


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("target", TARGETS)
def test_foreign_response_signatures_cannot_be_replayed(source: P, target: P) -> None:
    body = RESPONSE[source](
        {
            "model": "claude-source",
            "choices": [{"message": chat_message(), "finish_reason": "tool_calls"}],
        },
        "claude-source",
    )
    output = json.loads(
        convert_response(target, source, json.dumps(body).encode(), "claude-target")
    )
    if source == target:
        assert "FOREIGN_SIG" in json.dumps(output)
    else:
        assert_clean(output)
        assert "private" in json.dumps(output)
        if target == P.ANTHROPIC:
            assert all(
                block["type"] not in {"thinking", "redacted_thinking"}
                for block in output["content"]
            )
            replay = convert_request(
                P.ANTHROPIC,
                P.ANTHROPIC,
                {
                    "model": "claude-target",
                    "messages": [{"role": "assistant", "content": output["content"]}],
                },
            )
            assert "FOREIGN_SIG" not in json.dumps(replay)


@pytest.mark.parametrize("source", [P.BEDROCK_CONVERSE_STREAM, P.PI_MESSAGES])
@pytest.mark.parametrize(
    "target", [P.ANTHROPIC, P.OPENAI_CHAT, P.PI_MESSAGES, P.BEDROCK_CONVERSE_STREAM]
)
def test_foreign_redacted_reasoning_is_not_relabelled(source: P, target: P) -> None:
    message = chat_message()
    message["reasoning_blocks"].append(
        {"type": "redacted_thinking", "data": "b3BhcXVl"}
    )
    body = RESPONSE[source](
        {
            "model": "claude-source",
            "choices": [{"message": message, "finish_reason": "tool_calls"}],
        },
        "claude-source",
    )
    output = json.loads(
        convert_response(target, source, json.dumps(body).encode(), "claude-target")
    )
    if source == target:
        assert "b3BhcXVl" in json.dumps(output)
    else:
        assert_clean(output)


async def raw(data: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(data), 3):
        yield data[i : i + 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    "target",
    [P.ANTHROPIC, P.BEDROCK_CONVERSE_STREAM, P.OPENAI_CHAT, P.MISTRAL_CONVERSATIONS],
)
async def test_foreign_sse_signatures_are_removed_incrementally(
    source: P, target: P
) -> None:
    events = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "reasoning_content": "private"},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"reasoning_signature": "FOREIGN_SIG"},
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {"index": 0, "delta": {"content": "answer"}, "finish_reason": None}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    chat = (
        b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        + b"data: [DONE]\n\n"
    )
    native = b"".join(
        [chunk async for chunk in STREAM[source](raw(chat), "claude-source")]
    )
    output = b"".join(
        [
            chunk
            async for chunk in convert_stream_iterator(
                target, source, raw(native), "claude-target"
            )
        ]
    )
    assert b"answer" in output and b"private" in output
    if target == source:
        assert b"FOREIGN_SIG" in output
    else:
        assert b"FOREIGN_SIG" not in output
        if target in {P.ANTHROPIC, P.BEDROCK_CONVERSE_STREAM, P.MISTRAL_CONVERSATIONS}:
            assert output.count(b"private") == 1
        if target == P.ANTHROPIC:
            assert b'"type": "thinking"' not in output
            assert b"signature_delta" not in output


@pytest.mark.parametrize("target", SOURCES)
def test_foreign_request_normalization_does_not_add_ids_to_source(target: P) -> None:
    message = chat_message()
    message["tool_calls"][0].pop("id")
    body = {"model": "claude-source", "messages": [message]}
    original = deepcopy(body)
    output = convert_request(
        P.OPENAI_CHAT, target, body, "claude-target", preserve_reasoning=True
    )
    assert body == original
    assert "weather" in json.dumps(output)


def test_reasoning_alias_survives_bedrock_request_and_response_boundary() -> None:
    message = chat_message()
    message["reasoning"] = message.pop("reasoning_content")
    message.pop("reasoning_blocks")
    for preserve in (False, True):
        output = convert_request(
            P.OPENAI_CHAT,
            P.BEDROCK_CONVERSE_STREAM,
            {"model": "claude-source", "messages": [message]},
            "claude-target",
            preserve_reasoning=preserve,
        )
        assert json.dumps(output).count("private") == 1
    output = convert_response(
        P.BEDROCK_CONVERSE_STREAM,
        P.OPENAI_CHAT,
        json.dumps(
            {"choices": [{"message": message, "finish_reason": "tool_calls"}]}
        ).encode(),
        "claude-target",
    )
    assert output.count(b"private") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "first-"])
async def test_mistral_boundary_preserves_same_event_reasoning_and_complete_block(
    prefix: str,
) -> None:
    events = []
    if prefix:
        events.append(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": prefix},
                        "finish_reason": None,
                    }
                ]
            }
        )
    events.extend(
        [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": "private",
                            "reasoning_blocks": [
                                {
                                    "type": "thinking",
                                    "thinking": prefix + "private",
                                    "signature": "FOREIGN_SIG",
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {"index": 0, "delta": {"content": "answer"}, "finish_reason": None}
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
    )
    output = b"".join(
        [
            chunk
            async for chunk in convert_stream_iterator(
                P.MISTRAL_CONVERSATIONS,
                P.OPENAI_CHAT,
                raw(
                    b"".join(
                        b"data: " + json.dumps(event).encode() + b"\n\n"
                        for event in events
                    )
                ),
                "model",
            )
        ]
    )
    assert output.count(b"private") == 1
    assert b"FOREIGN_SIG" not in output
    if prefix:
        assert output.count(prefix.encode()) == 1


@pytest.mark.asyncio
async def test_reasoning_alias_survives_bedrock_stream_boundary() -> None:
    events = [
        {
            "choices": [
                {"index": 0, "delta": {"reasoning": "private"}, "finish_reason": None}
            ]
        },
        {
            "choices": [
                {"index": 0, "delta": {"content": "answer"}, "finish_reason": None}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    output = b"".join(
        [
            chunk
            async for chunk in convert_stream_iterator(
                P.BEDROCK_CONVERSE_STREAM,
                P.OPENAI_CHAT,
                raw(
                    b"".join(
                        b"data: " + json.dumps(event).encode() + b"\n\n"
                        for event in events
                    )
                ),
                "claude-target",
            )
        ]
    )
    assert output.count(b"private") == 1
    assert output.index(b"private") < output.index(b"answer")
