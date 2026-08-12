from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from lens_api.gateway.converters import convert_stream_iterator
from lens_api.gateway.converters._shared import (
    _parse_sse_stream,
    responses_input_to_chat_messages,
)
from lens_api.gateway.converters.chat_to_anthropic import (
    anthropic_request_to_chat,
    anthropic_stream_to_chat_stream,
    chat_request_to_anthropic,
    chat_stream_to_anthropic_stream,
)
from lens_api.gateway.converters.chat_to_gemini import (
    chat_request_to_gemini,
    chat_stream_to_gemini_stream,
    gemini_request_to_chat,
    gemini_stream_to_chat_stream,
)
from lens_api.gateway.converters.chat_to_responses import (
    chat_request_to_responses,
    chat_stream_to_responses_stream,
    responses_request_to_chat,
    responses_stream_to_chat_stream,
)
from lens_api.gateway.service.stream_restore import _restore_openai_chat_stream
from lens_api.models import ProtocolKind


class TrackedAsyncBytes:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self._index = 0
        self.drained = False

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._chunks):
            self.drained = True
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


async def _collect(iterator: AsyncIterator[bytes]) -> bytes:
    chunks: list[bytes] = []
    async for chunk in iterator:
        chunks.append(chunk)
    return b"".join(chunks)


def _sse(payload: dict[str, object], newline: str = "\n") -> bytes:
    return f"data: {json.dumps(payload)}{newline}{newline}".encode()


def _json_payloads(output: bytes) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in output.decode().splitlines():
        if not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ")
        if data == "[DONE]":
            continue
        payloads.append(json.loads(data))
    return payloads


# ---------------------------------------------------------------------------
# Shared SSE parser
# ---------------------------------------------------------------------------


async def _parse_all(raw: TrackedAsyncBytes) -> list[dict[str, object]]:
    return [p async for p in _parse_sse_stream(raw)]


def test_parser_crlf_events_split_across_chunks() -> None:
    body = b'data: {"a":1}\r\n\r\ndata: {"a":2}\r\n\r\ndata: [DONE]\r\n\r\n'
    raw = TrackedAsyncBytes([body[:5], body[5:12], body[12:]])
    assert asyncio.run(_parse_all(raw)) == [{"a": 1}, {"a": 2}]
    assert raw.drained is True


def test_parser_crlf_split_across_chunk_boundary() -> None:
    body = b'data: {"c":3}\r\n\r\ndata: {"d":4}\r\n\r\n'
    raw = TrackedAsyncBytes([body[:18], body[18:]])
    assert asyncio.run(_parse_all(raw)) == [{"c": 3}, {"d": 4}]


def test_parser_multiline_data_and_eof_without_blank_line() -> None:
    body = b'data: {"x":1,\ndata: "y":2}\n\ndata: {"b":3}'
    raw = TrackedAsyncBytes([body])
    assert asyncio.run(_parse_all(raw)) == [{"x": 1, "y": 2}, {"b": 3}]


def test_parser_ignores_event_and_comment_lines() -> None:
    body = b'event: x\n: keepalive\ndata: {"h":9}\n\n'
    raw = TrackedAsyncBytes([body])
    assert asyncio.run(_parse_all(raw)) == [{"h": 9}]


def test_parser_done_then_extra_bytes_drains_without_yield() -> None:
    body = b'data: {"k":12}\n\ndata: [DONE]\n\ndata: {"l":13}\n\n'
    raw = TrackedAsyncBytes([body])
    assert asyncio.run(_parse_all(raw)) == [{"k": 12}]
    assert raw.drained is True


def test_parser_malformed_json_raises() -> None:
    raw = TrackedAsyncBytes([b"data: {bad}\n\n"])
    try:
        asyncio.run(_parse_all(raw))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def _parse_all_raw(
    raw: TrackedAsyncBytes,
) -> list[dict[str, object]]:
    return [p async for p in _parse_sse_stream(raw, allow_raw_json=True)]


def test_parser_raw_ndjson_and_array() -> None:
    raw = TrackedAsyncBytes([b'{"e":5}\n{"f":6}\n'])
    assert asyncio.run(_parse_all(raw)) == []
    raw2 = TrackedAsyncBytes([b'{"e":5}\n{"f":6}\n'])
    assert asyncio.run(_parse_all_raw(raw2)) == [{"e": 5}, {"f": 6}]

    array = json.dumps([{"g": 7}, {"g": 8}]).encode()
    raw3 = TrackedAsyncBytes([array])
    assert asyncio.run(_parse_all_raw(raw3)) == [{"g": 7}, {"g": 8}]


def test_parser_malformed_raw_json_raises_not_empty() -> None:
    raw = TrackedAsyncBytes([b'[{"x":1},bad]'])
    try:
        asyncio.run(_parse_all_raw(raw))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Chat -> Anthropic
# ---------------------------------------------------------------------------


def test_chat_to_anthropic_fragmented_tool_call_is_one_block() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "weather",
                                            "arguments": '{"q":',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"beijing"}'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            b"data: [DONE]\n\n",
        ]
    )
    output = asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "m")))
    payloads = _json_payloads(output)
    starts = [p for p in payloads if p["type"] == "content_block_start"]
    tool_blocks = [p for p in starts if p["content_block"]["type"] == "tool_use"]
    assert len(tool_blocks) == 1, payloads
    assert tool_blocks[0]["content_block"]["id"] == "call_1"
    assert tool_blocks[0]["content_block"]["name"] == "weather"
    deltas = [
        p
        for p in payloads
        if p["type"] == "content_block_delta"
        and p["delta"].get("type") == "input_json_delta"
    ]
    assert "".join(d["delta"]["partial_json"] for d in deltas) == '{"q":"beijing"}'


def test_chat_to_anthropic_text_and_tool_block_indexes_stay_distinct() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": "think", "content": "hello"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "f", "arguments": "{}"},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]}
            ),
            b"data: [DONE]\n\n",
        ]
    )
    output = asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "m")))
    payloads = _json_payloads(output)
    text_deltas = [
        p
        for p in payloads
        if p["type"] == "content_block_delta" and p["delta"].get("type") == "text_delta"
    ]
    thinking_deltas = [
        p
        for p in payloads
        if p["type"] == "content_block_delta"
        and p["delta"].get("type") == "thinking_delta"
    ]
    # Both text deltas must target the same text block.
    assert len(text_deltas) == 2
    assert text_deltas[0]["index"] == text_deltas[1]["index"]
    assert thinking_deltas[0]["index"] != text_deltas[0]["index"]


def test_chat_to_anthropic_clean_done_synthesizes_terminal() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
            ),
            b"data: [DONE]\n\n",
        ]
    )
    output = asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "m")))
    assert b'"type": "message_stop"' in output


def test_chat_to_anthropic_invalid_tool_json_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "f", "arguments": "{bad"},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            b"data: [DONE]\n\n",
        ]
    )
    try:
        asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Anthropic -> Chat
# ---------------------------------------------------------------------------


def test_anthropic_to_chat_usage_is_last_value_not_summed() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "model": "claude",
                        "usage": {"input_tokens": 3, "output_tokens": 1},
                    },
                }
            ),
            _sse(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 5},
                }
            ),
            _sse({"type": "message_stop"}),
        ]
    )
    output = asyncio.run(_collect(anthropic_stream_to_chat_stream(raw, "m")))
    payloads = _json_payloads(output)
    final = payloads[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"]["completion_tokens"] == 5
    assert final["usage"]["prompt_tokens"] == 3


def test_anthropic_to_chat_missing_message_stop_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"type": "message_start", "message": {"id": "m", "model": "c"}}),
            _sse(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                }
            ),
        ]
    )
    try:
        asyncio.run(_collect(anthropic_stream_to_chat_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_anthropic_to_chat_missing_stop_reason_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"type": "message_start", "message": {"id": "m", "model": "c"}}),
            _sse({"type": "message_stop"}),
        ]
    )
    try:
        asyncio.run(_collect(anthropic_stream_to_chat_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Responses -> Chat (request)
# ---------------------------------------------------------------------------


def test_responses_input_to_chat_merges_parallel_function_calls() -> None:
    input_items = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "bash",
            "arguments": '{"cmd":"a"}',
        },
        {
            "type": "function_call",
            "call_id": "call_2",
            "name": "bash",
            "arguments": '{"cmd":"b"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "out1",
        },
        {
            "type": "function_call_output",
            "call_id": "call_2",
            "output": "out2",
        },
    ]
    messages = responses_input_to_chat_messages(input_items)
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool"]
    assistant = messages[0]
    assert assistant["content"] is None
    assert [tc["id"] for tc in assistant["tool_calls"]] == ["call_1", "call_2"]
    assert messages[1]["tool_call_id"] == "call_1"
    assert messages[2]["tool_call_id"] == "call_2"


def test_responses_input_to_chat_keeps_single_tool_call_ordering() -> None:
    input_items = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "bash",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "out1",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "next"}],
        },
    ]
    messages = responses_input_to_chat_messages(input_items)
    assert [m["role"] for m in messages] == ["assistant", "tool", "user"]


# ---------------------------------------------------------------------------
# Chat -> Responses
# ---------------------------------------------------------------------------


def test_chat_to_responses_complete_lifecycle_and_output() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "model": "gpt",
                    "choices": [
                        {
                            "delta": {"reasoning_content": "r1", "content": "hi"},
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            _sse(
                {"choices": [{"delta": {"content": " there"}, "finish_reason": "stop"}]}
            ),
            _sse(
                {
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    }
                }
            ),
            b"data: [DONE]\n\n",
        ]
    )
    output = asyncio.run(_collect(chat_stream_to_responses_stream(raw, "m")))
    payloads = _json_payloads(output)
    types = [p["type"] for p in payloads]
    assert types.count("response.output_text.delta") == 2
    assert "response.output_text.done" in types
    assert "response.content_part.done" in types
    assert "response.output_item.done" in types
    assert types.count("response.completed") == 1
    # The Responses target must not carry a Chat [DONE] marker.
    assert b"data: [DONE]" not in output
    completed = [p for p in payloads if p["type"] == "response.completed"][0]
    response = completed["response"]
    assert response["status"] == "completed"
    message_items = [i for i in response["output"] if i["type"] == "message"]
    assert len(message_items) == 1
    assert message_items[0]["content"][0]["text"] == "hi there"
    reasoning_items = [i for i in response["output"] if i["type"] == "reasoning"]
    assert len(reasoning_items) == 1
    assert reasoning_items[0]["summary"][0]["text"] == "r1"
    assert response["usage"]["input_tokens"] == 3


def test_chat_to_responses_fragmented_tool_arguments() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "f", "arguments": '{"a":'},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": "1}"}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            b"data: [DONE]\n\n",
        ]
    )
    output = asyncio.run(_collect(chat_stream_to_responses_stream(raw, "m")))
    payloads = _json_payloads(output)
    types = [p["type"] for p in payloads]
    assert "response.function_call_arguments.done" in types
    completed = [p for p in payloads if p["type"] == "response.completed"][0]
    calls = [i for i in completed["response"]["output"] if i["type"] == "function_call"]
    assert len(calls) == 1
    assert calls[0]["arguments"] == '{"a":1}'
    assert calls[0]["name"] == "f"


def test_chat_to_responses_invalid_tool_json_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "f", "arguments": "{bad"},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            b"data: [DONE]\n\n",
        ]
    )
    try:
        asyncio.run(_collect(chat_stream_to_responses_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Responses -> Chat
# ---------------------------------------------------------------------------


def _responses_text_stream(text: str) -> bytes:
    return b"".join(
        [
            _sse({"type": "response.created", "response": {"id": "r", "model": "m"}}),
            _sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "msg",
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }
            ),
            _sse(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": text,
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": "msg",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "model": "m",
                        "status": "completed",
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 2,
                            "total_tokens": 5,
                        },
                    },
                }
            ),
        ]
    )


def test_responses_to_chat_tool_finish_reason() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"type": "response.created", "response": {"id": "r", "model": "m"}}),
            _sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": "fc_1",
                        "type": "function_call",
                        "status": "in_progress",
                        "name": "f",
                        "arguments": "",
                        "call_id": "call_1",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": '{"a":',
                }
            ),
            _sse(
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 0,
                    "arguments": '{"a":1}',
                }
            ),
            _sse(
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "id": "fc_1",
                        "type": "function_call",
                        "status": "completed",
                        "name": "f",
                        "arguments": '{"a":1}',
                        "call_id": "call_1",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "model": "m",
                        "status": "completed",
                        "usage": {},
                    },
                }
            ),
        ]
    )
    output = asyncio.run(_collect(responses_stream_to_chat_stream(raw, "m")))
    payloads = _json_payloads(output)
    final = payloads[-1]
    assert final["choices"][0]["finish_reason"] == "tool_calls"
    assert b"data: [DONE]" in output
    tool_deltas = [p for p in payloads if p["choices"][0]["delta"].get("tool_calls")]
    args = "".join(
        tc["function"].get("arguments", "")
        for p in tool_deltas
        for tc in p["choices"][0]["delta"]["tool_calls"]
    )
    assert args == '{"a":1}'


def test_responses_to_chat_refusal_delta() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"type": "response.created", "response": {"id": "r", "model": "m"}}),
            _sse(
                {
                    "type": "response.refusal.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "I cannot",
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {"id": "r", "model": "m", "status": "completed"},
                }
            ),
        ]
    )
    output = asyncio.run(_collect(responses_stream_to_chat_stream(raw, "m")))
    payloads = _json_payloads(output)
    refusal_deltas = [p for p in payloads if p["choices"][0]["delta"].get("refusal")]
    assert len(refusal_deltas) == 1
    assert refusal_deltas[0]["choices"][0]["delta"]["refusal"] == "I cannot"
    assert refusal_deltas[0]["choices"][0]["delta"]["content"] == "I cannot"


def test_responses_to_chat_missing_terminal_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"type": "response.created", "response": {"id": "r", "model": "m"}}),
            _sse(
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "delta": "hi",
                }
            ),
        ]
    )
    try:
        asyncio.run(_collect(responses_stream_to_chat_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_responses_to_chat_failed_terminal_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"type": "response.created", "response": {"id": "r", "model": "m"}}),
            _sse(
                {
                    "type": "response.failed",
                    "response": {
                        "id": "r",
                        "status": "failed",
                        "error": {"code": "x", "message": "boom"},
                    },
                }
            ),
        ]
    )
    try:
        asyncio.run(_collect(responses_stream_to_chat_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Chat -> Gemini
# ---------------------------------------------------------------------------


def test_chat_to_gemini_only_terminal_chunk_has_finish_reason() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"choices": [{"delta": {"content": "{"}, "finish_reason": None}]}),
            _sse(
                {"choices": [{"delta": {"content": '"x":1}'}, "finish_reason": "stop"}]}
            ),
            b"data: [DONE]\n\n",
        ]
    )
    output = asyncio.run(_collect(chat_stream_to_gemini_stream(raw, "m")))
    payloads = _json_payloads(output)
    reasons = [p["candidates"][0].get("finishReason") for p in payloads]
    assert reasons == [None, "STOP"], reasons


def test_chat_request_to_gemini_maps_tool_result_name() -> None:
    gemini = chat_request_to_gemini(
        {
            "model": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "weather", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
            ],
        }
    )
    parts = gemini["contents"][1]["parts"]
    assert parts[0]["functionResponse"]["name"] == "weather"


# ---------------------------------------------------------------------------
# Gemini -> Chat
# ---------------------------------------------------------------------------


def test_gemini_to_chat_usage_only_chunk_does_not_terminate() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": "a"}]}}
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 1,
                        "candidatesTokenCount": 1,
                        "totalTokenCount": 2,
                    },
                }
            ),
            _sse(
                {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "b"}]},
                            "finishReason": "STOP",
                        }
                    ]
                }
            ),
        ]
    )
    output = asyncio.run(_collect(gemini_stream_to_chat_stream(raw, "m")))
    payloads = _json_payloads(output)
    text = "".join(
        p["choices"][0]["delta"].get("content", "")
        for p in payloads
        if p["choices"][0]["delta"].get("content")
    )
    assert text == "ab"
    finishes = [
        p["choices"][0]["finish_reason"]
        for p in payloads
        if p["choices"][0]["finish_reason"]
    ]
    assert finishes == ["stop"]


def test_gemini_to_chat_missing_finish_reason_raises() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": "x"}]}}
                    ]
                }
            )
        ]
    )
    try:
        asyncio.run(_collect(gemini_stream_to_chat_stream(raw, "m")))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_gemini_to_chat_stable_tool_call_ids() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "weather",
                                            "args": {"a": 1},
                                        }
                                    }
                                ],
                            }
                        }
                    ]
                }
            ),
            _sse(
                {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "weather",
                                            "args": {"b": 2},
                                        }
                                    }
                                ],
                            },
                            "finishReason": "STOP",
                        }
                    ]
                }
            ),
        ]
    )
    output = asyncio.run(_collect(gemini_stream_to_chat_stream(raw, "m")))
    payloads = _json_payloads(output)
    ids = [
        tc["id"]
        for p in payloads
        for tc in p["choices"][0]["delta"].get("tool_calls", [])
    ]
    assert ids == ["call_weather_1", "call_weather_2"]
    assert all(not id.startswith("call_") or "_" in id for id in ids)


def test_gemini_to_chat_raw_json_array() -> None:
    array = json.dumps(
        [
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "ok"}]},
                        "finishReason": "STOP",
                    }
                ]
            }
        ]
    ).encode()
    raw = TrackedAsyncBytes([array])
    output = asyncio.run(_collect(gemini_stream_to_chat_stream(raw, "m")))
    payloads = _json_payloads(output)
    text = "".join(
        p["choices"][0]["delta"].get("content", "")
        for p in payloads
        if p["choices"][0]["delta"].get("content")
    )
    assert text == "ok"
    assert b"data: [DONE]" in output


def test_gemini_request_to_chat_function_response_matches_call_id() -> None:
    chat = gemini_request_to_chat(
        {
            "contents": [
                {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "weather", "args": {"a": 1}}}],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "weather",
                                "response": {"content": "sunny"},
                            }
                        }
                    ],
                },
            ]
        }
    )
    calls = [m for m in chat["messages"] if m.get("tool_calls")]
    tools = [m for m in chat["messages"] if m.get("role") == "tool"]
    assert calls and tools
    assert tools[0]["tool_call_id"] == calls[0]["tool_calls"][0]["id"]


# ---------------------------------------------------------------------------
# Two-hop conversion through the Chat pivot
# ---------------------------------------------------------------------------


def test_anthropic_to_responses_two_hop() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "type": "message_start",
                    "message": {"id": "m", "model": "claude"},
                }
            ),
            _sse(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
            _sse(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "hi"},
                }
            ),
            _sse(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                }
            ),
            _sse({"type": "message_stop"}),
        ]
    )
    stream = convert_stream_iterator(
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.ANTHROPIC,
        raw,
        "claude",
    )
    output = asyncio.run(_collect(stream))
    payloads = _json_payloads(output)
    types = [p["type"] for p in payloads]
    completed = [p for p in payloads if p["type"] == "response.completed"][0]
    text = "".join(
        part.get("text", "")
        for item in completed["response"]["output"]
        if item["type"] == "message"
        for part in item["content"]
    )
    assert text == "hi"
    assert "response.completed" in types
    assert b"data: [DONE]" not in output


def test_responses_to_anthropic_two_hop() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(p)
            for p in [
                {"type": "response.created", "response": {"id": "r", "model": "m"}},
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "delta": "hi",
                },
                {
                    "type": "response.completed",
                    "response": {"id": "r", "model": "m", "status": "completed"},
                },
            ]
        ]
    )
    stream = convert_stream_iterator(
        ProtocolKind.ANTHROPIC,
        ProtocolKind.OPENAI_RESPONSES,
        raw,
        "m",
    )
    output = asyncio.run(_collect(stream))
    payloads = _json_payloads(output)
    assert any(p["type"] == "message_start" for p in payloads)
    assert any(p["type"] == "message_stop" for p in payloads)
    text = "".join(
        p["delta"].get("text", "")
        for p in payloads
        if p["type"] == "content_block_delta" and p["delta"].get("type") == "text_delta"
    )
    assert text == "hi"


# ---------------------------------------------------------------------------
# Chat stream restoration for non-stream callers
# ---------------------------------------------------------------------------


def test_restore_openai_chat_stream_aggregates() -> None:
    payloads = [
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "he"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "f", "arguments": '{"a":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]
    restored = _restore_openai_chat_stream(payloads)
    assert restored is not None
    choice = restored["choices"][0]
    assert choice["message"]["content"] == "he"
    assert choice["message"]["tool_calls"][0]["function"]["arguments"] == '{"a":1}'
    assert choice["finish_reason"] == "tool_calls"
    assert restored["usage"]["total_tokens"] == 5


def test_restore_openai_chat_stream_requires_finish_reason() -> None:
    payloads = [
        {
            "id": "x",
            "model": "m",
            "choices": [
                {"index": 0, "delta": {"content": "partial"}, "finish_reason": None}
            ],
        }
    ]
    assert _restore_openai_chat_stream(payloads) is None


def test_restore_openai_chat_stream_invalid_tool_json_raises() -> None:
    payloads = [
        {
            "id": "x",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": "{bad"}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    ]
    try:
        _restore_openai_chat_stream(payloads)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Reasoning/thinking level mapping across protocols
# ---------------------------------------------------------------------------


def test_anthropic_to_chat_thinking_budget_maps_to_effort() -> None:
    chat = anthropic_request_to_chat(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "enabled", "budget_tokens": 16384},
        }
    )
    assert chat["reasoning_effort"] == "high"

    chat_medium = anthropic_request_to_chat(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "enabled", "budget_tokens": 4096},
        }
    )
    assert chat_medium["reasoning_effort"] == "low"


def test_anthropic_to_chat_adaptive_thinking_keeps_effort() -> None:
    chat = anthropic_request_to_chat(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "max"},
        }
    )
    assert chat["reasoning_effort"] == "max"


def test_anthropic_to_chat_disabled_thinking_maps_to_none() -> None:
    chat = anthropic_request_to_chat(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "disabled"},
        }
    )
    assert chat["reasoning_effort"] == "none"


def test_anthropic_to_responses_keeps_reasoning_effort() -> None:
    # The reported regression: pi-agent (Anthropic) max thinking must reach an
    # OpenAI Responses upstream as reasoning.effort=max.
    chat = anthropic_request_to_chat(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "max"},
        }
    )
    responses = chat_request_to_responses(chat)
    assert responses["reasoning"] == {"effort": "max"}


def test_chat_to_anthropic_reasoning_effort_maps_to_thinking_budget() -> None:
    anthropic = chat_request_to_anthropic(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "max",
            "max_tokens": 1024,
        }
    )
    assert anthropic["thinking"]["type"] == "enabled"
    assert anthropic["thinking"]["budget_tokens"] == 32768
    # max_tokens must be raised so the thinking budget is not truncated.
    assert anthropic["max_tokens"] >= 32768 + 1024

    anthropic_high = chat_request_to_anthropic(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
        }
    )
    assert anthropic_high["thinking"]["budget_tokens"] == 16384


def test_chat_to_anthropic_none_disables_thinking() -> None:
    anthropic = chat_request_to_anthropic(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "none",
        }
    )
    assert anthropic["thinking"] == {"type": "disabled"}


def test_responses_to_chat_keeps_reasoning_effort() -> None:
    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
            ],
            "reasoning": {"effort": "high"},
        }
    )
    assert chat["reasoning_effort"] == "high"


def test_chat_to_gemini_reasoning_effort_maps_to_thinking_budget() -> None:
    gemini = chat_request_to_gemini(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
        }
    )
    assert gemini["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 16384


def test_gemini_to_chat_thinking_budget_maps_to_effort() -> None:
    chat = gemini_request_to_chat(
        {
            "model": "m",
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "generationConfig": {
                "thinkingConfig": {"thinkingBudget": 8192},
            },
        }
    )
    assert chat["reasoning_effort"] == "medium"


def test_chat_response_to_anthropic_preserves_cache_usage() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import (
        chat_response_to_anthropic,
    )

    result = chat_response_to_anthropic(
        {
            "id": "chatcmpl_1",
            "model": "m",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 2608,
                "completion_tokens": 128,
                "total_tokens": 2736,
                "prompt_tokens_details": {"cached_tokens": 2560},
            },
        },
        "m",
    )
    usage = result["usage"]
    assert usage["input_tokens"] == 48  # 2608 - 2560 cached
    assert usage["output_tokens"] == 128
    assert usage["cache_read_input_tokens"] == 2560
    assert usage["cache_creation_input_tokens"] == 0
