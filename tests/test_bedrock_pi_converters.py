from __future__ import annotations

import asyncio
import base64
import json
import struct
import zlib
from collections.abc import AsyncIterator, Callable
from copy import deepcopy
from typing import Any

import pytest

from lens_api.gateway.converters._shared import format_sse_event
from lens_api.gateway.converters.chat_to_bedrock import (
    bedrock_eventstream_to_sse,
    bedrock_request_to_chat,
    bedrock_response_to_chat,
    bedrock_sse_to_eventstream,
    bedrock_stream_to_chat_stream,
    chat_request_to_bedrock,
    chat_response_to_bedrock,
    chat_stream_to_bedrock_stream,
)
from lens_api.gateway.converters.chat_to_pi import (
    chat_request_to_pi,
    chat_response_to_pi,
    chat_stream_to_pi_stream,
    pi_request_to_chat,
    pi_response_to_chat,
    pi_stream_to_chat_stream,
)

StreamConverter = Callable[..., AsyncIterator[bytes]]


async def _raw(data: bytes, size: int = 1) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


async def _collect(iterator: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in iterator])


def _run(converter: StreamConverter, data: bytes, model: str = "m") -> bytes:
    return asyncio.run(_collect(converter(_raw(data), model)))


def _events(data: bytes) -> list[dict[str, Any]]:
    return [
        json.loads(line[5:].strip())
        for line in data.decode().splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    ]


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(format_sse_event(None, event) for event in events)


def _delta(value: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "r1",
        "model": "m",
        "choices": [{"index": 0, "delta": value, "finish_reason": finish}],
    }


def _pi_usage() -> dict[str, Any]:
    return {
        "input": 10,
        "output": 4,
        "cacheRead": 3,
        "cacheWrite": 2,
        "cacheWrite1h": 1,
        "reasoning": 2,
        "totalTokens": 19,
        "cost": {
            "input": 0.1,
            "output": 0.2,
            "cacheRead": 0.01,
            "cacheWrite": 0.02,
            "total": 0.33,
        },
    }


def _chat_response() -> dict[str, Any]:
    return {
        "id": "r1",
        "model": "claude-sonnet-4-6",
        "created": 100,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "你好",
                    "reasoning_content": "think",
                    "reasoning_signature": "signed",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "weather",
                                "arguments": '{"city":"北京"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 4,
            "total_tokens": 19,
            "prompt_tokens_details": {
                "cached_tokens": 3,
                "cache_creation_tokens": 2,
                "cache_creation_1h_tokens": 1,
            },
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }


@pytest.mark.parametrize("stream", [True, False])
def test_bedrock_request_roundtrip_media_tools_cache_and_options(stream: bool) -> None:
    body = {
        "modelId": "anthropic.claude-sonnet-4-6",
        "stream": stream,
        "system": [
            {"text": "system"},
            {"cachePoint": {"type": "default", "ttl": "1h"}},
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": "look"},
                    {"image": {"format": "png", "source": {"bytes": "aW1n"}}},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "reasoningContent": {
                            "reasoningText": {"text": "think", "signature": "signed"}
                        }
                    },
                    {"text": "visible"},
                    {
                        "toolUse": {
                            "toolUseId": "c1",
                            "name": "weather",
                            "input": {"city": "北京"},
                        }
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "c1",
                            "content": [
                                {"text": "result"},
                                {
                                    "image": {
                                        "format": "png",
                                        "source": {"bytes": "aW1n"},
                                    }
                                },
                            ],
                            "status": "success",
                        }
                    },
                    {"cachePoint": {"type": "default"}},
                ],
            },
        ],
        "inferenceConfig": {
            "maxTokens": 100,
            "temperature": 0.3,
            "topP": 0.9,
            "stopSequences": ["END"],
        },
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": "weather",
                        "description": "weather",
                        "inputSchema": {"json": {"type": "object"}},
                        "strict": True,
                    }
                },
                {"cachePoint": {"type": "default", "ttl": "1h"}},
            ],
            "toolChoice": {"tool": {"name": "weather"}},
        },
        "additionalModelRequestFields": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        },
        "requestMetadata": {"task": "test"},
    }
    original = deepcopy(body)
    chat = bedrock_request_to_chat(body)
    assert chat["stream"] is stream
    assert chat["reasoning_effort"] == "high"
    assert chat["messages"][2]["reasoning_signature"] == "signed"
    assert chat["messages"][3]["role"] == "tool"
    assert chat["messages"][3]["_bedrock_result_cache_point"] == {"type": "default"}
    rebuilt = chat_request_to_bedrock(chat)
    assert rebuilt["stream"] is stream
    assert rebuilt["messages"] == body["messages"]
    assert rebuilt["system"] == body["system"]
    assert rebuilt["toolConfig"] == body["toolConfig"]
    assert rebuilt["inferenceConfig"] == body["inferenceConfig"]
    results = [
        block["toolResult"]
        for msg in rebuilt["messages"]
        for block in msg["content"]
        if "toolResult" in block
    ]
    assert results == [body["messages"][2]["content"][0]["toolResult"]]
    assert body == original


def test_bedrock_tool_results_group_errors_and_images() -> None:
    body = {
        "model": "anthropic.claude-sonnet-4-6",
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "a", "function": {"name": "f", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "g", "arguments": "{}"}},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "a",
                "is_error": True,
                "content": [
                    {"type": "text", "text": "failed"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aW1n"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "b", "content": "ok"},
        ],
    }
    original = deepcopy(body)
    native = chat_request_to_bedrock(body)
    blocks = native["messages"][-1]["content"]
    assert [block["toolResult"]["toolUseId"] for block in blocks[:2]] == ["a", "b"]
    assert blocks[0]["toolResult"]["status"] == "error"
    assert all("image" not in block for block in blocks[0]["toolResult"]["content"])
    assert "image" in blocks[-1]
    assert body == original


@pytest.mark.parametrize(
    "model,signed,expected",
    [
        ("anthropic.claude-sonnet-4-6", True, "reasoningContent"),
        ("anthropic.claude-sonnet-4-6", False, "text"),
        ("qwen", True, "reasoningContent"),
    ],
)
def test_bedrock_replay_signed_and_unsigned_thinking(
    model: str, signed: bool, expected: str
) -> None:
    message = {"role": "assistant", "content": "visible", "reasoning_content": "think"}
    if signed:
        message["reasoning_signature"] = "signature"
    native = chat_request_to_bedrock({"model": model, "messages": [message]})
    first = native["messages"][0]["content"][0]
    assert expected in first
    if model == "qwen":
        assert "signature" not in first["reasoningContent"]["reasoningText"]


@pytest.mark.parametrize(
    "native,chat",
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("tool_use", "tool_calls"),
    ],
)
def test_bedrock_response_stop_usage_thinking_tools(native: str, chat: str) -> None:
    response = chat_response_to_bedrock(_chat_response())
    response["stopReason"] = native
    response["usage"]["totalTokens"] = 14
    original = deepcopy(response)
    converted = bedrock_response_to_chat(response, "client-model")
    assert converted["model"] == "client-model"
    assert converted["choices"][0]["finish_reason"] == chat
    assert converted["choices"][0]["message"]["content"] == "你好"
    assert converted["choices"][0]["message"]["reasoning_signature"] == "signed"
    assert converted["usage"]["prompt_tokens"] == 15
    assert converted["usage"]["completion_tokens_details"]["reasoning_tokens"] == 2
    assert chat_response_to_bedrock(converted) == response
    assert response == original


@pytest.mark.parametrize(
    "reason",
    [None, "guardrail_intervened", "content_filtered", "malformed_model_output"],
)
def test_bedrock_response_does_not_fabricate_success(reason: str | None) -> None:
    with pytest.raises(ValueError):
        bedrock_response_to_chat(
            {
                "output": {"message": {"role": "assistant", "content": []}},
                "stopReason": reason,
            }
        )


def test_bedrock_redacted_native_media_json_roundtrip() -> None:
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"reasoningContent": {"redactedContent": "AQID"}},
                    {"text": "visible"},
                    {"image": {"format": "png", "source": {"bytes": "aW1n"}}},
                ],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5},
    }
    converted = bedrock_response_to_chat(response)
    assert chat_response_to_bedrock(converted) == response
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "document": {
                            "name": "doc",
                            "format": "pdf",
                            "source": {"bytes": "eA=="},
                        }
                    }
                ],
            }
        ]
    }
    assert (
        chat_request_to_bedrock(bedrock_request_to_chat(request))["messages"]
        == request["messages"]
    )


@pytest.mark.parametrize("stream", [True, False])
def test_pi_context_transcript_tools_media_and_options(stream: bool) -> None:
    body = {
        "model": "m",
        "stream": stream,
        "context": {
            "messages": [
                {
                    "role": "system",
                    "content": "sys",
                    "timestamp": 0,
                    "toolsAdded": [
                        {
                            "name": "weather",
                            "description": "weather",
                            "parameters": {"type": "object"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image", "data": "aW1n", "mimeType": "image/png"},
                    ],
                    "timestamp": 1,
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "think",
                            "thinkingSignature": "sig",
                        },
                        {"type": "text", "text": "visible", "textSignature": "text-id"},
                        {
                            "type": "toolCall",
                            "id": "c1",
                            "name": "weather",
                            "arguments": {"city": "北京"},
                            "thoughtSignature": "google-sig",
                        },
                    ],
                    "api": "pi-messages",
                    "provider": "radius",
                    "model": "m",
                    "usage": _pi_usage(),
                    "stopReason": "toolUse",
                    "timestamp": 2,
                },
                {
                    "role": "toolResult",
                    "toolCallId": "c1",
                    "toolName": "weather",
                    "content": [
                        {"type": "text", "text": "failed"},
                        {"type": "image", "mimeType": "image/png", "data": "aW1n"},
                    ],
                    "isError": True,
                    "timestamp": 3,
                },
            ]
        },
        "options": {
            "temperature": 0.5,
            "maxTokens": 100,
            "reasoning": "high",
            "cacheRetention": "long",
            "sessionId": "session",
            "toolChoice": "required",
        },
    }
    original = deepcopy(body)
    chat = pi_request_to_chat(body)
    assert chat["messages"][2]["reasoning_signature"] == "sig"
    assert chat["messages"][3]["is_error"] is True
    assert chat_request_to_pi(chat) == body
    assert body == original


def test_pi_chat_tool_result_name_pairing_and_missing_pair() -> None:
    body = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "function": {"name": "weather", "arguments": "{}"}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "result",
                "is_error": True,
            },
        ],
    }
    native = chat_request_to_pi(body)
    assert native["context"]["messages"][1]["toolName"] == "weather"
    assert native["context"]["messages"][1]["isError"] is True
    with pytest.raises(ValueError, match="Missing tool name"):
        chat_request_to_pi(
            {"messages": [{"role": "tool", "tool_call_id": "orphan", "content": "x"}]}
        )


def test_pi_response_canonical_assistant_message() -> None:
    chat = _chat_response()
    original = deepcopy(chat)
    pi = chat_response_to_pi(chat, "client-model")
    assert pi["role"] == "assistant"
    assert pi["api"] == "pi-messages"
    assert pi["model"] == "client-model"
    assert pi["stopReason"] == "toolUse"
    assert pi["usage"]["input"] == 10
    assert pi["usage"]["reasoning"] == 2
    assert pi["usage"]["cacheWrite1h"] == 1
    restored = pi_response_to_chat(pi, "client-model")
    message = restored["choices"][0]["message"]
    assert message["content"] == "你好"
    assert message["reasoning_signature"] == "signed"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {
        "city": "北京"
    }
    assert chat == original


@pytest.mark.parametrize("reason", ["error", "aborted", "pending", None])
def test_pi_response_rejects_unsuccessful_or_pending(reason: str | None) -> None:
    with pytest.raises(ValueError):
        pi_response_to_chat(
            {
                "role": "assistant",
                "content": [],
                "stopReason": reason,
                "errorMessage": "failed",
            }
        )


def _chat_stream() -> bytes:
    return _sse(
        _delta({"role": "assistant", "content": ""}),
        _delta({"reasoning_content": "think"}),
        _delta({"reasoning_signature": "sig"}),
        _delta({"content": "你"}),
        _delta({"content": "好"}),
        _delta(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "c1",
                        "function": {"name": "weather", "arguments": '{"city":'},
                    }
                ]
            }
        ),
        _delta({"tool_calls": [{"index": 0, "function": {"arguments": '"北京"}'}}]}),
        _delta({}, "tool_calls"),
        {"choices": [], "usage": _chat_response()["usage"]},
    ) + format_sse_event(None, "[DONE]")


def test_chat_pi_stream_compact_events_and_roundtrip() -> None:
    pi = _run(chat_stream_to_pi_stream, _chat_stream())
    events = _events(pi)
    assert all("partial" not in event and "message" not in event for event in events)
    assert events[0] == {"type": "start"}
    assert events[-1]["type"] == "done"
    assert events[-1]["reason"] == "toolUse"
    assert events[-1]["usage"]["cacheRead"] == 3
    thinking = next(event for event in events if event["type"] == "thinking_end")
    assert thinking["contentSignature"] == "sig"
    assert (
        next(event for event in events if event["type"] == "toolcall_start")["toolName"]
        == "weather"
    )
    restored = _events(_run(pi_stream_to_chat_stream, pi))
    assert (
        "".join(
            choice["delta"].get("content", "")
            for event in restored
            for choice in event.get("choices", [])
        )
        == "你好"
    )
    assert restored[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert restored[-1]["usage"]["prompt_tokens"] == 15


def test_pi_stream_authoritative_ends_without_deltas() -> None:
    pi = _sse(
        {"type": "start"},
        {"type": "thinking_start", "contentIndex": 0},
        {
            "type": "thinking_end",
            "contentIndex": 0,
            "content": "secret",
            "contentSignature": "opaque",
            "redacted": True,
        },
        {"type": "text_start", "contentIndex": 1},
        {
            "type": "text_end",
            "contentIndex": 1,
            "content": "visible",
            "contentSignature": "text-id",
        },
        {"type": "toolcall_start", "contentIndex": 2, "id": "c1", "toolName": "f"},
        {
            "type": "toolcall_end",
            "contentIndex": 2,
            "toolCall": {
                "type": "toolCall",
                "id": "c1",
                "name": "f",
                "arguments": {"x": 1},
            },
        },
        {
            "type": "done",
            "reason": "toolUse",
            "usage": _pi_usage(),
            "responseId": "resp",
            "responseModel": "resolved",
        },
    )
    chat = _events(_run(pi_stream_to_chat_stream, pi, "client"))
    deltas = [choice["delta"] for event in chat for choice in event.get("choices", [])]
    assert any(
        delta.get("reasoning_blocks", [{}])[0].get("data") == "opaque"
        for delta in deltas
    )
    assert any(delta.get("text_signature") == "text-id" for delta in deltas)
    assert chat[-1]["id"] == "resp"
    assert chat[-1]["model"] == "client"
    assert any(
        delta.get("tool_calls", [{}])[0].get("function", {}).get("arguments")
        == '{"x": 1}'
        for delta in deltas
    )


@pytest.mark.parametrize("index", [-1, True, 1, 1000000000, "__proto__"])
def test_pi_stream_rejects_invalid_indices(index: Any) -> None:
    data = _sse(
        {"type": "start"},
        {"type": "text_start", "contentIndex": index},
        {"type": "done", "reason": "stop", "usage": _pi_usage()},
    )
    with pytest.raises(ValueError, match="contentIndex"):
        _run(pi_stream_to_chat_stream, data)


@pytest.mark.parametrize(
    "converter,data",
    [
        (pi_stream_to_chat_stream, b""),
        (pi_stream_to_chat_stream, _sse({"type": "start"})),
        (pi_stream_to_chat_stream, b"data: [DONE]\n\n"),
        (bedrock_stream_to_chat_stream, b""),
        (bedrock_stream_to_chat_stream, _sse({"messageStart": {"role": "assistant"}})),
    ],
)
def test_inbound_streams_require_native_terminal(
    converter: StreamConverter, data: bytes
) -> None:
    with pytest.raises(ValueError):
        _run(converter, data)


@pytest.mark.parametrize(
    "converter", [chat_stream_to_pi_stream, chat_stream_to_bedrock_stream]
)
def test_outbound_streams_emit_error_on_chat_eof_without_finish(
    converter: StreamConverter,
) -> None:
    events = _events(
        _run(
            converter,
            _sse(_delta({"content": "partial"})) + format_sse_event(None, "[DONE]"),
        )
    )
    assert not any(
        event.get("type") == "done" or "messageStop" in event for event in events
    )
    assert (
        events[-1].get("type") == "error" or "modelStreamErrorException" in events[-1]
    )


@pytest.mark.parametrize(
    "converter,event",
    [
        (
            pi_stream_to_chat_stream,
            {
                "type": "error",
                "reason": "aborted",
                "usage": _pi_usage(),
                "errorMessage": "cancelled",
            },
        ),
        (
            bedrock_stream_to_chat_stream,
            {"modelStreamErrorException": {"message": "failed"}},
        ),
    ],
)
def test_inbound_stream_errors_never_become_success(
    converter: StreamConverter, event: dict[str, Any]
) -> None:
    output = _run(converter, _sse(event))
    assert "error" in _events(output)[-1]
    assert b"[DONE]" not in output


def test_chat_stream_error_after_finish_suppresses_done() -> None:
    data = _sse(
        _delta({"content": "partial"}),
        _delta({}, "stop"),
        {"error": {"message": "late failure"}},
    )
    events = _events(_run(chat_stream_to_pi_stream, data))
    assert events[-1]["type"] == "error"
    assert not any(event.get("type") == "done" for event in events)


def test_bedrock_stream_internal_sse_reasoning_tool_usage_roundtrip() -> None:
    native = _run(chat_stream_to_bedrock_stream, _chat_stream())
    events = _events(native)
    assert events[0] == {"messageStart": {"role": "assistant"}}
    assert events[-2] == {"messageStop": {"stopReason": "tool_use"}}
    assert events[-1]["metadata"]["usage"]["cacheReadInputTokens"] == 3
    assert any(
        event.get("contentBlockDelta", {})
        .get("delta", {})
        .get("reasoningContent", {})
        .get("signature")
        == "sig"
        for event in events
    )
    assert any(
        event.get("contentBlockStart", {})
        .get("start", {})
        .get("toolUse", {})
        .get("toolUseId")
        == "c1"
        for event in events
    )
    restored = _events(_run(bedrock_stream_to_chat_stream, native))
    assert restored[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert restored[-1]["usage"]["prompt_tokens"] == 15


def test_bedrock_redacted_chunks_join_as_bytes_and_unclosed_thinking_flushes() -> None:
    data = _sse(
        {"messageStart": {"role": "assistant"}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"redactedContent": "AQ=="}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"redactedContent": "AgM="}},
            }
        },
        {"messageStop": {"stopReason": "end_turn"}},
    )
    events = _events(_run(bedrock_stream_to_chat_stream, data))
    assert any(
        choice["delta"].get("reasoning_blocks")
        == [{"type": "redacted_thinking", "data": "AQID"}]
        for event in events
        for choice in event.get("choices", [])
    )


def test_reused_chat_tool_indices_are_distinct_calls() -> None:
    data = _sse(
        _delta(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "a",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ]
            }
        ),
        _delta(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "b",
                        "function": {"name": "g", "arguments": "{}"},
                    }
                ]
            }
        ),
        _delta({}, "tool_calls"),
    )
    events = _events(_run(chat_stream_to_pi_stream, data))
    starts = [event for event in events if event.get("type") == "toolcall_start"]
    assert [event["id"] for event in starts] == ["a", "b"]
    assert [event["contentIndex"] for event in starts] == [0, 1]


@pytest.mark.parametrize(
    "converter", [chat_stream_to_pi_stream, chat_stream_to_bedrock_stream]
)
def test_invalid_tool_json_is_not_success(converter: StreamConverter) -> None:
    data = _sse(
        _delta(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "a",
                        "function": {"name": "f", "arguments": '{"x":'},
                    }
                ]
            }
        ),
        _delta({}, "tool_calls"),
    )
    with pytest.raises(ValueError, match="arguments JSON"):
        _run(converter, data)


def _binary(converter: StreamConverter, data: bytes) -> bytes:
    return asyncio.run(_collect(converter(_raw(data))))


def test_aws_eventstream_frames_crc_arbitrary_chunks_and_exceptions() -> None:
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "你好"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3}
            }
        },
        {"throttlingException": {"message": "retry"}},
    ]
    encoded = _binary(bedrock_sse_to_eventstream, _sse(*events))
    offset = 0
    while offset < len(encoded):
        total, _, crc = struct.unpack(">III", encoded[offset : offset + 12])
        assert zlib.crc32(encoded[offset : offset + 8]) & 0xFFFFFFFF == crc
        assert (
            zlib.crc32(encoded[offset : offset + total - 4]) & 0xFFFFFFFF
            == struct.unpack(">I", encoded[offset + total - 4 : offset + total])[0]
        )
        offset += total
    assert _events(_binary(bedrock_eventstream_to_sse, encoded)) == events


@pytest.mark.parametrize("cut", [1, 7, 11, 12, 20, -1])
def test_aws_truncated_frames_raise(cut: int) -> None:
    data = _binary(
        bedrock_sse_to_eventstream, _sse({"messageStart": {"role": "assistant"}})
    )
    with pytest.raises(ValueError, match="Truncated"):
        _binary(bedrock_eventstream_to_sse, data[:cut])


@pytest.mark.parametrize(
    "position,match", [(8, "prelude CRC"), (-1, "message CRC"), (20, "message CRC")]
)
def test_aws_invalid_crc_raises(position: int, match: str) -> None:
    data = bytearray(
        _binary(
            bedrock_sse_to_eventstream, _sse({"messageStart": {"role": "assistant"}})
        )
    )
    data[position] ^= 1
    with pytest.raises(ValueError, match=match):
        _binary(bedrock_eventstream_to_sse, bytes(data))


def _frame(headers: bytes, payload: bytes) -> bytes:
    prelude = struct.pack(">II", 16 + len(headers) + len(payload), len(headers))
    data = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + payload
    return data + struct.pack(">I", zlib.crc32(data))


def _header(name: str, value: str) -> bytes:
    return (
        bytes([len(name)])
        + name.encode()
        + b"\x07"
        + struct.pack(">H", len(value.encode()))
        + value.encode()
    )


def test_aws_headers_all_types_and_error_headers() -> None:
    headers = _header(":message-type", "event") + _header(":event-type", "messageStart")
    for kind, value in [
        (0, b""),
        (1, b""),
        (2, b"\x01"),
        (3, b"\x00\x01"),
        (4, b"\x00" * 4),
        (5, b"\x00" * 8),
        (6, b"\x00\x02ab"),
        (8, b"\x00" * 8),
        (9, b"\x00" * 16),
    ]:
        headers += bytes([2]) + f"h{kind}".encode() + bytes([kind]) + value
    assert _events(
        _binary(bedrock_eventstream_to_sse, _frame(headers, b'{"role":"assistant"}'))
    ) == [{"messageStart": {"role": "assistant"}}]
    error = (
        _header(":message-type", "error")
        + _header(":error-code", "InternalFailure")
        + _header(":error-message", "failed")
    )
    assert _events(_binary(bedrock_eventstream_to_sse, _frame(error, b""))) == [
        {"error": {"type": "InternalFailure", "message": "failed"}}
    ]


@pytest.mark.parametrize(
    "headers",
    [
        b"\x05ab",
        b"\x01x\xff",
        _header(":message-type", "event"),
        _header(":message-type", "event") + _header(":message-type", "event"),
    ],
)
def test_aws_invalid_headers_raise(headers: bytes) -> None:
    with pytest.raises(ValueError):
        _binary(bedrock_eventstream_to_sse, _frame(headers, b"{}"))


def test_streams_drain_after_native_terminal() -> None:
    drained: list[bool] = []

    async def source() -> AsyncIterator[bytes]:
        yield _sse(
            {"type": "start"}, {"type": "done", "reason": "stop", "usage": _pi_usage()}
        )
        yield b": keepalive\n\n"
        drained.append(True)

    asyncio.run(_collect(pi_stream_to_chat_stream(source())))
    assert drained == [True]


@pytest.mark.parametrize(
    "model,effort,expected",
    [
        ("openai.gpt-oss-120b", "max", {"reasoning_effort": "high"}),
        ("openai.gpt-oss-120b", "minimal", {"reasoning_effort": "low"}),
        ("openai.gpt-5", "xhigh", {"reasoning": {"effort": "xhigh"}}),
        ("openai.gpt-5", "minimal", {"reasoning": {"effort": "low"}}),
        ("qwen", "high", None),
    ],
)
def test_bedrock_model_specific_reasoning_fields(
    model: str, effort: str, expected: dict[str, Any] | None
) -> None:
    result = chat_request_to_bedrock(
        {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": effort,
        }
    )
    assert result.get("additionalModelRequestFields") == expected
    if expected:
        chat = bedrock_request_to_chat(result)
        assert chat["reasoning_effort"] == (
            "high"
            if "oss" in model and effort == "max"
            else "low" if effort == "minimal" else effort
        )


def test_pi_redacted_and_unsigned_thinking_roundtrip() -> None:
    message = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "plain"},
            {
                "type": "thinking",
                "thinking": "[Reasoning redacted]",
                "thinkingSignature": "opaque",
                "redacted": True,
            },
            {"type": "text", "text": "visible", "textSignature": "text-id"},
        ],
        "api": "pi-messages",
        "provider": "radius",
        "model": "m",
        "usage": _pi_usage(),
        "stopReason": "stop",
        "timestamp": 100123,
    }
    original = deepcopy(message)
    chat = pi_response_to_chat(message)
    native = chat_response_to_pi(chat)
    assert native["content"] == message["content"]
    assert native["timestamp"] == 100123
    assert native["usage"] == message["usage"]
    assert message == original


def test_multiple_stream_reasoning_blocks_keep_separate_signatures() -> None:
    data = _sse(
        {"type": "start"},
        {"type": "thinking_start", "contentIndex": 0},
        {"type": "thinking_delta", "contentIndex": 0, "delta": "first"},
        {
            "type": "thinking_end",
            "contentIndex": 0,
            "content": "first",
            "contentSignature": "sig1",
        },
        {"type": "thinking_start", "contentIndex": 1},
        {
            "type": "thinking_end",
            "contentIndex": 1,
            "content": "second",
            "contentSignature": "sig2",
        },
        {"type": "thinking_start", "contentIndex": 2},
        {
            "type": "thinking_end",
            "contentIndex": 2,
            "content": "redacted",
            "contentSignature": "opaque",
            "redacted": True,
        },
        {
            "type": "done",
            "reason": "stop",
            "usage": _pi_usage(),
            "providerThinkingLevel": "high",
        },
    )
    chat = _run(pi_stream_to_chat_stream, data)
    events = _events(_run(chat_stream_to_pi_stream, chat))
    ends = [event for event in events if event["type"] == "thinking_end"]
    assert [event["content"] for event in ends] == ["first", "second", "redacted"]
    assert [event["contentSignature"] for event in ends] == ["sig1", "sig2", "opaque"]
    assert ends[-1]["redacted"] is True
    assert events[-1]["providerThinkingLevel"] == "high"


def test_pi_tool_end_must_agree_with_streamed_arguments() -> None:
    data = _sse(
        {"type": "start"},
        {"type": "toolcall_start", "contentIndex": 0, "id": "c", "toolName": "f"},
        {"type": "toolcall_delta", "contentIndex": 0, "delta": '{"x":1}'},
        {
            "type": "toolcall_end",
            "contentIndex": 0,
            "toolCall": {
                "type": "toolCall",
                "id": "c",
                "name": "f",
                "arguments": {"x": 2},
            },
        },
        {"type": "done", "reason": "toolUse", "usage": _pi_usage()},
    )
    with pytest.raises(ValueError, match="disagrees"):
        _run(pi_stream_to_chat_stream, data)


@pytest.mark.parametrize("converter", [chat_request_to_pi, chat_request_to_bedrock])
def test_requests_do_not_alias_nested_input(
    converter: Callable[..., dict[str, Any]],
) -> None:
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "parameters": {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                    },
                },
            }
        ],
    }
    original = deepcopy(body)
    output = converter(body)
    if "context" in output:
        output["context"]["messages"][0]["toolsAdded"][0]["parameters"]["properties"][
            "x"
        ]["type"] = "number"
    else:
        output["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"][
            "properties"
        ]["x"]["type"] = "number"
    assert body == original


@pytest.mark.parametrize(
    "total,headers", [(15, 0), (33 * 1024 * 1024, 0), (20, 5), (200000, 150000)]
)
def test_aws_rejects_impossible_frame_lengths(total: int, headers: int) -> None:
    prelude = struct.pack(">II", total, headers)
    data = prelude + struct.pack(">I", zlib.crc32(prelude))
    with pytest.raises(ValueError, match="frame length"):
        _binary(bedrock_eventstream_to_sse, data)


@pytest.mark.parametrize("payload", [b"{bad}", b"[]", b"\xff"])
def test_aws_rejects_invalid_json_payload(payload: bytes) -> None:
    headers = _header(":message-type", "event") + _header(":event-type", "messageStart")
    with pytest.raises(ValueError):
        _binary(bedrock_eventstream_to_sse, _frame(headers, payload))
