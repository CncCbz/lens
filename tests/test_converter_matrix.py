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


def test_chat_to_anthropic_reused_tool_call_index_is_two_blocks() -> None:
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
                                            "arguments": '{"q":"a"}',
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
                                        "id": "call_2",
                                        "type": "function",
                                        "function": {
                                            "name": "time",
                                            "arguments": '{"q":"b"}',
                                        },
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
    assert [b["content_block"]["id"] for b in tool_blocks] == ["call_1", "call_2"]
    deltas = [
        p
        for p in payloads
        if p["type"] == "content_block_delta"
        and p["delta"].get("type") == "input_json_delta"
    ]
    assert [d["delta"]["partial_json"] for d in deltas] == ['{"q":"a"}', '{"q":"b"}']


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


def test_chat_to_anthropic_done_without_finish_reason_raises() -> None:
    import pytest

    raw = TrackedAsyncBytes(
        [
            _sse(
                {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
            ),
            b"data: [DONE]\n\n",
        ]
    )
    with pytest.raises(ValueError, match="without a finish reason"):
        asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "m")))
    assert raw.drained is True


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


def test_chat_to_responses_reused_tool_call_index_is_two_calls() -> None:
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
                                            "name": "f",
                                            "arguments": '{"a":1}',
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
                                        "id": "call_2",
                                        "type": "function",
                                        "function": {
                                            "name": "g",
                                            "arguments": '{"b":2}',
                                        },
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
    output = asyncio.run(_collect(chat_stream_to_responses_stream(raw, "m")))
    payloads = _json_payloads(output)
    completed = [p for p in payloads if p["type"] == "response.completed"][0]
    calls = [i for i in completed["response"]["output"] if i["type"] == "function_call"]
    assert [(c["call_id"], c["name"], c["arguments"]) for c in calls] == [
        ("call_1", "f", '{"a":1}'),
        ("call_2", "g", '{"b":2}'),
    ]


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
    assert reasons == [None, None, "STOP"], reasons


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
    # pi clamps xhigh and max to the high budget (16384).
    assert anthropic["thinking"]["budget_tokens"] == 16384
    # max_tokens must be raised so the thinking budget is not truncated.
    assert anthropic["max_tokens"] >= 16384 + 1024

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


def test_responses_to_chat_reads_configuration_update_and_additional_tools() -> None:
    # pi/Codex clients keep the top-level reasoning.effort fixed for prompt
    # caching and carry the real level plus tool definitions in input items.
    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [
                        {
                            "type": "function",
                            "name": "read",
                            "description": "Read a file",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                },
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "be helpful"}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                {"type": "configuration_update", "reasoning": {"effort": "xhigh"}},
            ],
            "reasoning": {"effort": "medium", "summary": "auto"},
        }
    )
    # additional_tools/configuration_update items must not become messages.
    assert [m["role"] for m in chat["messages"]] == ["system", "user"]
    assert chat["messages"][0]["content"] == "be helpful"
    # Tool definitions merged into top-level tools.
    assert [t["function"]["name"] for t in chat["tools"]] == ["read"]
    # The configuration_update effort overrides the fixed top-level effort.
    assert chat["reasoning_effort"] == "xhigh"


def test_responses_to_chat_unknown_typed_items_do_not_become_messages() -> None:
    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                {"type": "reasoning", "content": []},
                {"type": "custom_tool_call_output", "call_id": "c_1", "output": "o"},
                {"vendor_field": 1},
                {"role": "user"},
            ],
        }
    )
    assert [m["role"] for m in chat["messages"]] == ["user"]


def test_responses_to_chat_last_configuration_update_wins() -> None:
    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                {"type": "configuration_update", "reasoning": {"effort": "xhigh"}},
                {"type": "configuration_update", "reasoning": {"effort": "low"}},
            ],
            "reasoning": {"effort": "medium"},
        }
    )
    assert chat["reasoning_effort"] == "low"


def test_responses_to_chat_strips_responses_only_fields() -> None:
    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
            ],
            "include": ["reasoning.encrypted_content"],
            "client_metadata": {"x-codex-installation-id": "install-1"},
            "prompt_cache_key": "session-123",
        }
    )
    # Chat upstreams reject the Responses-only fields; cache routing keys survive.
    assert "include" not in chat
    assert "client_metadata" not in chat
    assert chat["prompt_cache_key"] == "session-123"


def test_responses_to_chat_embedded_tools_do_not_replace_top_level() -> None:
    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [
                        {
                            "type": "function",
                            "name": "read",
                            "description": "updated",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                },
                {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "read",
                    "description": "old",
                    "parameters": {"type": "object", "properties": {}},
                },
                {
                    "type": "function",
                    "name": "bash",
                    "description": "Run bash",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        }
    )
    names = [t["function"]["name"] for t in chat["tools"]]
    assert sorted(names) == ["bash", "read"]
    by_name = {t["function"]["name"]: t for t in chat["tools"]}
    # additional_tools items cannot replace a top-level tool definition.
    assert by_name["read"]["function"]["description"] == "old"


def test_chat_to_gemini_reasoning_effort_maps_to_thinking_budget() -> None:
    gemini = chat_request_to_gemini(
        {
            "model": "gemini-2.5-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
        }
    )
    thinking_config = gemini["generationConfig"]["thinkingConfig"]
    # pi's getGoogleBudget table: 2.5-pro uses 32768 for high.
    assert thinking_config["thinkingBudget"] == 32768
    # Without includeThoughts Gemini returns no thought parts at all.
    assert thinking_config["includeThoughts"] is True


def test_chat_to_gemini_3_uses_discrete_thinking_level() -> None:
    gemini = chat_request_to_gemini(
        {
            "model": "gemini-3-pro-preview",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
        }
    )
    thinking_config = gemini["generationConfig"]["thinkingConfig"]
    # Gemini 3+ takes a discrete level instead of a token budget.
    assert thinking_config["thinkingLevel"] == "HIGH"
    assert "thinkingBudget" not in thinking_config
    assert thinking_config["includeThoughts"] is True


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


def test_responses_to_chat_merges_assistant_text_with_tool_calls() -> None:
    from lens_api.gateway.converters._shared import _responses_input_to_chat_parts

    messages, _, _ = _responses_input_to_chat_parts(
        [
            {"role": "user", "content": [{"type": "input_text", "text": "go"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Checking."}],
            },
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "read",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "c1", "output": "body"},
        ]
    )
    # Chat carries the preamble text and the tool calls on one assistant turn.
    assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
    assert messages[1]["content"] == "Checking."
    assert messages[1]["tool_calls"][0]["function"]["name"] == "read"


def test_responses_to_chat_keeps_reasoning_content() -> None:
    from lens_api.gateway.converters._shared import _responses_input_to_chat_parts

    messages, _, _ = _responses_input_to_chat_parts(
        [
            {"role": "user", "content": [{"type": "input_text", "text": "go"}]},
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "thinking"}],
            },
            {"role": "assistant", "content": [{"type": "output_text", "text": "ok"}]},
        ]
    )
    assert messages[1]["reasoning_content"] == "thinking"
    assert messages[1]["content"] == "ok"


def test_responses_stream_keeps_incomplete_and_filter_reasons() -> None:
    import asyncio

    from lens_api.gateway.converters.chat_to_responses import (
        responses_stream_to_chat_stream,
    )

    def run(events: list[dict[str, object]]) -> str:
        async def source() -> object:
            for event in events:
                yield f"data: {json.dumps(event)}\n\n".encode()

        async def collect() -> str:
            chunks = []
            async for chunk in responses_stream_to_chat_stream(source(), "m"):
                chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            finish_reasons = [
                json.loads(line[6:])["choices"][0].get("finish_reason")
                for line in "".join(chunks).splitlines()
                if line.startswith("data: ") and line[6:].strip() != "[DONE]"
            ]
            return next((r for r in reversed(finish_reasons) if r), "")

        return asyncio.run(collect())

    base = [
        {"type": "response.created", "response": {"id": "r", "model": "m"}},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "c1",
                "name": "t",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": "{",
        },
    ]
    truncated = run(
        base
        + [
            {
                "type": "response.incomplete",
                "response": {
                    "id": "r",
                    "model": "m",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            }
        ]
    )
    # A truncated tool call must not masquerade as a complete tool_calls turn.
    assert truncated == "length"

    filtered = run(
        base
        + [
            {
                "type": "response.incomplete",
                "response": {
                    "id": "r",
                    "model": "m",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "content_filter"},
                },
            }
        ]
    )
    assert filtered == "content_filter"


def test_responses_to_chat_maps_structured_output_to_response_format() -> None:
    from lens_api.gateway.converters.chat_to_responses import responses_request_to_chat

    chat = responses_request_to_chat(
        {
            "model": "m",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": "x"}]}
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "Out",
                    "schema": {"type": "object"},
                }
            },
            "truncation": "auto",
            "previous_response_id": "resp_1",
        }
    )
    assert chat["response_format"]["type"] == "json_schema"
    assert chat["response_format"]["json_schema"]["name"] == "Out"
    # Responses-only fields must not leak into a Chat Completions body.
    assert "truncation" not in chat
    assert "previous_response_id" not in chat


def test_responses_to_chat_reads_refusal_parts() -> None:
    from lens_api.gateway.converters.chat_to_responses import responses_response_to_chat

    chat = responses_response_to_chat(
        {
            "id": "r",
            "model": "m",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "I cannot help."}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        },
        "m",
    )
    assert chat["choices"][0]["message"]["content"] == "I cannot help."

    # A completed response with tool calls reports tool_calls, not stop.
    tool_chat = responses_response_to_chat(
        {
            "id": "r",
            "model": "m",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "t",
                    "arguments": "{}",
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        },
        "m",
    )
    assert tool_chat["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_to_anthropic_replays_reasoning_as_thinking_when_signed() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import chat_request_to_anthropic

    signed = chat_request_to_anthropic(
        {
            "model": "c",
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "ans",
                    "reasoning_content": "think",
                    "reasoning_signature": "SIG",
                },
            ],
        }
    )
    blocks = signed["messages"][1]["content"]
    assert blocks[0] == {"type": "thinking", "thinking": "think", "signature": "SIG"}

    # An unsigned thinking block cannot be replayed as thinking; pi downgrades it.
    unsigned = chat_request_to_anthropic(
        {
            "model": "c",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ans", "reasoning_content": "think"},
            ],
        }
    )
    assert unsigned["messages"][1]["content"][0] == {"type": "text", "text": "think"}


def test_chat_to_anthropic_omits_temperature_with_thinking() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import chat_request_to_anthropic

    body = {"model": "c", "messages": [{"role": "user", "content": "hi"}]}

    with_thinking = chat_request_to_anthropic(
        {**body, "reasoning_effort": "high", "temperature": 0.7}
    )
    assert with_thinking["thinking"]["type"] == "enabled"
    assert "temperature" not in with_thinking

    without_thinking = chat_request_to_anthropic({**body, "temperature": 0.7})
    assert without_thinking["temperature"] == 0.7


def test_gemini_reasoning_roundtrip_keeps_thought_parts() -> None:
    from lens_api.gateway.converters.chat_to_gemini import (
        chat_request_to_gemini,
        gemini_response_to_chat,
    )

    converted = chat_request_to_gemini(
        {
            "model": "gemini-2.5-flash",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ans", "reasoning_content": "think"},
            ],
            "reasoning_effort": "high",
        }
    )
    model_parts = converted["contents"][1]["parts"]
    assert model_parts[0] == {"text": "think", "thought": True}
    assert model_parts[1] == {"text": "ans"}

    chat = gemini_response_to_chat(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "think", "thought": True},
                            {"text": "ans"},
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        },
        "m",
    )
    message = chat["choices"][0]["message"]
    assert message["reasoning_content"] == "think"
    assert message["content"] == "ans"


def test_chat_to_responses_preserves_incomplete_reason_on_roundtrip() -> None:
    from lens_api.gateway.converters.chat_to_responses import (
        chat_response_to_responses,
        responses_response_to_chat,
    )

    chat = {
        "choices": [
            {
                "message": {"role": "assistant", "content": "x"},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    responses = chat_response_to_responses(chat, "m")
    assert responses["status"] == "incomplete"
    # Without this the reason is lost and the next conversion reports content_filter.
    assert responses["incomplete_details"] == {"reason": "max_output_tokens"}
    back = responses_response_to_chat(responses, "m")
    assert back["choices"][0]["finish_reason"] == "length"


def test_gemini_3_off_uses_current_minimal_fallback() -> None:
    from lens_api.gateway.converters.chat_to_gemini import chat_request_to_gemini

    gemini = chat_request_to_gemini(
        {
            "model": "gemini-3-pro-preview",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "none",
        }
    )
    thinking_config = gemini["generationConfig"]["thinkingConfig"]
    # Current fallback without a per-model thinkingLevelMap.
    assert "thinkingBudget" not in thinking_config
    assert thinking_config["thinkingLevel"] == "MINIMAL"


def test_responses_to_chat_drops_orphan_reasoning() -> None:
    from lens_api.gateway.converters._shared import _responses_input_to_chat_parts

    messages, _, _ = _responses_input_to_chat_parts(
        [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "t"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
        ]
    )
    # An assistant message with null content and no tool calls is rejected upstream.
    assert [m["role"] for m in messages] == ["user", "user"]


def test_responses_to_chat_keeps_assistant_cache_control_and_images() -> None:
    from lens_api.gateway.converters._shared import _responses_input_to_chat_parts

    messages, _, _ = _responses_input_to_chat_parts(
        [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "ans"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAA"},
                ],
                "cache_control": {"type": "ephemeral"},
            },
        ]
    )
    assistant = messages[1]
    assert assistant["cache_control"] == {"type": "ephemeral"}
    assert [part["type"] for part in assistant["content"]] == ["text", "image_url"]


def test_chat_to_anthropic_normalizes_tool_ids_and_sets_is_error() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import chat_request_to_anthropic

    raw_id = "call.abc/def" + "x" * 70
    converted = chat_request_to_anthropic(
        {
            "model": "c",
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": raw_id,
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": raw_id,
                    "content": "out",
                    "is_error": True,
                },
            ],
        }
    )
    tool_use = converted["messages"][1]["content"][0]
    tool_result = converted["messages"][2]["content"][0]
    # Anthropic requires [a-zA-Z0-9_-] and at most 64 chars; both sides must match.
    assert len(tool_use["id"]) == 64
    assert "." not in tool_use["id"] and "/" not in tool_use["id"]
    assert tool_result["tool_use_id"] == tool_use["id"]
    assert tool_result["is_error"] is True


def test_chat_to_anthropic_sends_system_as_block_array() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import chat_request_to_anthropic

    converted = chat_request_to_anthropic(
        {
            "model": "c",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
        }
    )
    assert converted["system"] == [{"type": "text", "text": "sys"}]


def test_chat_to_anthropic_sets_thinking_display() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import chat_request_to_anthropic

    converted = chat_request_to_anthropic(
        {
            "model": "c",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
        }
    )
    assert converted["thinking"]["display"] == "summarized"


def test_chat_to_anthropic_tool_result_keeps_images() -> None:
    from lens_api.gateway.converters.chat_to_anthropic import chat_request_to_anthropic

    converted = chat_request_to_anthropic(
        {
            "model": "c",
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "content": [
                        {"type": "text", "text": "see"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAA"},
                        },
                    ],
                },
            ],
        }
    )
    content = converted["messages"][2]["content"][0]["content"]
    assert [block["type"] for block in content] == ["text", "image"]
    assert content[1]["source"]["data"] == "AAA"


def test_chat_to_gemini_function_response_uses_output_and_error_keys() -> None:
    from lens_api.gateway.converters.chat_to_gemini import chat_request_to_gemini

    def last_part(is_error: bool) -> dict[str, object]:
        converted = chat_request_to_gemini(
            {
                "model": "gemini-2.5-pro",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "f", "arguments": "{}"},
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "c1",
                        "content": "res",
                        "is_error": is_error,
                    },
                ],
            }
        )
        return converted["contents"][-1]["parts"][0]

    # Match pi\'s output/error convention for tool results.
    assert last_part(False)["functionResponse"]["response"] == {"output": "res"}
    assert last_part(True)["functionResponse"]["response"] == {"error": "res"}


def test_chat_to_gemini_tool_call_id_follows_pi_model_selection() -> None:
    from lens_api.gateway.converters.chat_to_gemini import chat_request_to_gemini

    def function_call(model: str) -> dict[str, object]:
        converted = chat_request_to_gemini(
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "f", "arguments": "{}"},
                            }
                        ],
                    },
                ],
            }
        )
        return converted["contents"][-1]["parts"][0]["functionCall"]

    # pi omits call IDs for Gemini 1.x/2.x.
    assert "id" not in function_call("gemini-2.5-pro")
    assert function_call("gemini-3-pro-preview")["id"] == "c1"
    assert function_call("claude-3-5-sonnet")["id"] == "c1"


def test_gemini_parallel_results_keep_explicit_ids_and_fifo_fallback() -> None:
    for explicit in (True, False):
        ids = ["first", "second"]
        calls = [
            {
                "functionCall": {
                    "name": "weather",
                    "args": {"city": city},
                    **({"id": cid} if explicit else {}),
                }
            }
            for city, cid in zip(("Paris", "Rome"), ids)
        ]
        order = list(reversed(ids)) if explicit else ids
        results = [
            {
                "functionResponse": {
                    "name": "weather",
                    "response": {"output": cid},
                    **({"id": cid} if explicit else {}),
                }
            }
            for cid in order
        ]
        chat = gemini_request_to_chat(
            {
                "contents": [
                    {"role": "model", "parts": calls},
                    {"role": "user", "parts": results + [{"text": "Compare them"}]},
                ]
            }
        )
        tool_ids = [m["tool_call_id"] for m in chat["messages"] if m["role"] == "tool"]
        expected = (
            order if explicit else [c["id"] for c in chat["messages"][0]["tool_calls"]]
        )
        assert tool_ids == expected
        assert len(set(tool_ids)) == 2
        assert chat["messages"][-1]["content"] == "Compare them"


def test_gemini_call_and_result_ids_share_normalization() -> None:
    for model in (
        "gemini-3-pro-preview",
        "gemini-2.5-pro",
        "claude-sonnet-4",
        "gpt-oss-120b",
    ):
        cid = "call:bad/" + "x" * 80
        converted = chat_request_to_gemini(
            {
                "model": model,
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": cid,
                                "function": {"name": "weather", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": cid, "content": "sunny"},
                ],
            }
        )
        call = converted["contents"][0]["parts"][0]["functionCall"]
        result = converted["contents"][1]["parts"][0]["functionResponse"]
        assert result["name"] == "weather"
        if model == "gemini-2.5-pro":
            assert "id" not in call and "id" not in result
        else:
            assert call["id"] == result["id"] == ("call_bad_" + "x" * 80)[:64]


def test_gemini_tools_preserve_full_schema_without_mutating_input() -> None:
    import copy

    schema = {
        "type": "object",
        "$defs": {"const": {"const": "named"}},
        "properties": {
            "const": {"const": "a"},
            "n": {"anyOf": [{"const": 3}, {"type": "null"}]},
            "b": {"const": True},
        },
        "default": {"const": "literal"},
        "examples": [{"const": "literal"}],
        "additionalProperties": False,
    }
    original = copy.deepcopy(schema)
    converted = chat_request_to_gemini(
        {
            "messages": [],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "choose", "parameters": schema},
                }
            ],
        }
    )
    declaration = converted["tools"][0]["functionDeclarations"][0]
    assert "parameters" not in declaration
    mapped = declaration["parametersJsonSchema"]
    assert mapped["properties"]["const"] == {"enum": ["a"]}
    assert mapped["$defs"]["const"] == {"enum": ["named"]}
    assert mapped["properties"]["n"]["anyOf"][0] == {"enum": [3]}
    assert mapped["properties"]["b"] == {"const": True}
    assert mapped["default"] == original["default"]
    assert mapped["examples"] == original["examples"]
    assert schema == original
    reverse = gemini_request_to_chat(converted)
    assert reverse["tools"][0]["function"]["parameters"] == mapped
    assert (
        gemini_request_to_chat(
            {
                "tools": [
                    {"functionDeclarations": [{"name": "legacy", "parameters": schema}]}
                ]
            }
        )["tools"][0]["function"]["parameters"]
        == schema
    )


def test_gemini_tool_finish_preserves_length_and_filter() -> None:
    from lens_api.gateway.converters.chat_to_gemini import gemini_response_to_chat

    for finish, expected in (
        ("STOP", "tool_calls"),
        ("MAX_TOKENS", "length"),
        ("SAFETY", "content_filter"),
    ):
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "weather", "args": {}}}]
                    },
                    "finishReason": finish,
                }
            ]
        }
        assert (
            gemini_response_to_chat(body, "m")["choices"][0]["finish_reason"]
            == expected
        )
        # The finish may arrive separately from the call.
        terminal = {"candidates": [{"finishReason": finish}]}
        del body["candidates"][0]["finishReason"]
        chunks = _json_payloads(
            asyncio.run(
                _collect(
                    gemini_stream_to_chat_stream(
                        TrackedAsyncBytes([_sse(body), _sse(terminal)]), "m"
                    )
                )
            )
        )
        assert chunks[-1]["choices"][0]["finish_reason"] == expected


def test_gemini_stream_buffers_arguments_and_keeps_parallel_indices() -> None:
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
                                        "id": "a",
                                        "thoughtSignature": "sigA",
                                        "function": {
                                            "name": "weather",
                                            "arguments": '{"city":',
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "b",
                                        "function": {
                                            "name": "weather",
                                            "arguments": '{"city":"Rome"}',
                                        },
                                    },
                                ]
                            }
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
                                    {"index": 0, "function": {"arguments": '"Paris"}'}}
                                ]
                            }
                        }
                    ]
                }
            ),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        ]
    )
    chunks = _json_payloads(
        asyncio.run(_collect(chat_stream_to_gemini_stream(raw, "gemini-3-pro-preview")))
    )
    calls = [
        p
        for chunk in chunks
        for c in chunk["candidates"]
        for p in c["content"]["parts"]
        if "functionCall" in p
    ]
    assert [p["functionCall"]["args"] for p in calls] == [
        {"city": "Paris"},
        {"city": "Rome"},
    ]
    assert calls[0]["thoughtSignature"] == "sigA"
    assert len(chunks) == 2
    assert "finishReason" not in chunks[0]["candidates"][0]
    assert chunks[-1]["candidates"][0]["content"]["parts"] == []
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"
    reverse = _json_payloads(
        asyncio.run(
            _collect(
                gemini_stream_to_chat_stream(
                    TrackedAsyncBytes(
                        [
                            _sse({"candidates": [{"content": {"parts": [p]}}]})
                            for p in calls
                        ]
                        + [_sse({"candidates": [{"finishReason": "STOP"}]})]
                    ),
                    "m",
                )
            )
        )
    )
    indices = [
        tc["index"]
        for chunk in reverse
        for tc in chunk["choices"][0]["delta"].get("tool_calls", [])
    ]
    assert indices == [0, 1]


def test_gemini_stream_invalid_arguments_never_become_empty_object() -> None:
    import pytest

    for arguments in ('{"city":', "not-json", "[]"):
        for finish in ("length", "tool_calls"):
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
                                                "id": "a",
                                                "function": {
                                                    "name": "weather",
                                                    "arguments": arguments,
                                                },
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ),
                    _sse({"choices": [{"delta": {}, "finish_reason": finish}]}),
                ]
            )
            if finish == "tool_calls":
                with pytest.raises(ValueError, match="complete JSON object"):
                    asyncio.run(_collect(chat_stream_to_gemini_stream(raw, "m")))
            else:
                chunks = _json_payloads(
                    asyncio.run(_collect(chat_stream_to_gemini_stream(raw, "m")))
                )
                assert chunks[-1]["candidates"][0]["finishReason"] == "MAX_TOKENS"
                assert chunks[-1]["candidates"][0]["content"]["parts"] == []


def test_gemini_signatures_stay_on_their_parts_through_chat() -> None:
    from lens_api.gateway.converters.chat_to_gemini import (
        chat_response_to_gemini,
        gemini_response_to_chat,
    )

    parts = [
        {"text": "private", "thought": True, "thoughtSignature": "thinking-sig"},
        {"text": "Checking", "thoughtSignature": "text-sig"},
        {"text": " weather"},
        {
            "functionCall": {"id": "c1", "name": "weather", "args": {}},
            "thoughtSignature": "call-sig",
        },
    ]
    body = {
        "model": "gemini-3-pro-preview",
        "contents": [{"role": "model", "parts": parts}],
    }
    chat = gemini_request_to_chat(body)
    assert chat["messages"][0]["content"] == "Checking weather"
    assert chat["messages"][0]["reasoning_content"] == "private"
    assert chat_request_to_gemini(chat)["contents"][0]["parts"] == parts
    response = {
        "modelVersion": "gemini-3-pro-preview",
        "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
    }
    assert (
        chat_response_to_gemini(gemini_response_to_chat(response, "m"), "m")[
            "candidates"
        ][0]["content"]["parts"]
        == parts
    )
    stream = gemini_stream_to_chat_stream(
        TrackedAsyncBytes([_sse(response)]), "gemini-3-pro-preview"
    )
    output = _json_payloads(
        asyncio.run(
            _collect(chat_stream_to_gemini_stream(stream, "gemini-3-pro-preview"))
        )
    )
    assert [
        part
        for chunk in output
        for candidate in chunk["candidates"]
        for part in candidate["content"]["parts"]
    ] == parts


def test_gemini_convert_request_uses_target_model_for_thinking_and_ids() -> None:
    from lens_api.gateway.converters import convert_request

    converted = convert_request(
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.GEMINI,
        {
            "model": "alias",
            "reasoning_effort": "high",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call:one",
                            "function": {"name": "weather", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call:one", "content": "sunny"},
            ],
            "top_p": 0.8,
            "stop": ["END"],
            "response_format": {"type": "json_object"},
        },
        target_model="gemini-3-pro-preview",
    )
    assert (
        converted["generationConfig"]["thinkingConfig"]["thinkingLevel"].lower()
        == "high"
    )
    assert "thinkingBudget" not in converted["generationConfig"]["thinkingConfig"]
    assert converted["contents"][0]["parts"][0]["functionCall"]["id"] == "call_one"
    assert converted["contents"][1]["parts"][0]["functionResponse"]["id"] == "call_one"
    assert converted["generationConfig"]["topP"] == 0.8
    assert converted["generationConfig"]["stopSequences"] == ["END"]
    assert converted["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_stream_retains_source_model_until_terminal() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "model": "gemini-3-pro-preview",
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call:one",
                                        "function": {
                                            "name": "weather",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                }
            ),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        ]
    )
    chunks = _json_payloads(
        asyncio.run(_collect(chat_stream_to_gemini_stream(raw, "alias")))
    )
    assert chunks[0]["modelVersion"] == "gemini-3-pro-preview"
    assert (
        chunks[0]["candidates"][0]["content"]["parts"][0]["functionCall"]["id"]
        == "call_one"
    )


def test_gemini_invalid_nonstream_arguments_are_rejected() -> None:
    import pytest

    for arguments in ('{"city":', "invalid", "null", "[]"):
        with pytest.raises(ValueError, match="JSON object arguments"):
            chat_request_to_gemini(
                {
                    "messages": [
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "a",
                                    "function": {
                                        "name": "weather",
                                        "arguments": arguments,
                                    },
                                }
                            ],
                        }
                    ]
                }
            )


def test_gemini_tool_stream_without_terminal_is_not_fabricated() -> None:
    import pytest

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
                                        "id": "a",
                                        "function": {
                                            "name": "weather",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            )
        ]
    )
    with pytest.raises(ValueError, match="without a finish reason"):
        asyncio.run(_collect(chat_stream_to_gemini_stream(raw, "m")))


def test_parser_utf8_all_byte_boundaries() -> None:
    payload = {"text": "中文🙂", "arguments": '{"城市":"北京🌍"}'}
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    streams = [
        (
            b"data: " + encoded + ending + ending + b"data: [DONE]" + ending + ending,
            False,
        )
        for ending in (b"\n", b"\r\n", b"\r")
    ] + [(encoded + b"\n" + encoded, True), (b"[" + encoded + b"]", True)]
    for data, raw_json in streams:
        for split in range(len(data) + 1):
            raw = TrackedAsyncBytes([data[:split], data[split:]])
            result = asyncio.run(_parse_all_raw(raw) if raw_json else _parse_all(raw))
            assert result == (
                [payload, payload] if raw_json and data[:1] == b"{" else [payload]
            )
            assert raw.drained
    multiline = b'data: {"text":\r\ndata: "' + "中文🙂".encode() + b'"}\r\n\r\n'
    for split in range(len(multiline) + 1):
        assert asyncio.run(
            _parse_all(TrackedAsyncBytes([multiline[:split], multiline[split:]]))
        ) == [{"text": "中文🙂"}]


def test_parser_invalid_utf8_and_done_drain() -> None:
    import pytest

    for data in (b'data: {"text":"\xff"}\n\n', b'data: {"text":"\xe4\xb8'):
        with pytest.raises(ValueError):
            asyncio.run(_parse_all(TrackedAsyncBytes([data])))
    for ending in (b"\n", b"\r\n", b"\r"):
        data = b"data: [DONE]" + ending + ending
        for chunks in ([data + b"\xff\xe4"], [data, b"\xff\xe4"]):
            raw = TrackedAsyncBytes(chunks)
            assert asyncio.run(_parse_all(raw)) == []
            assert raw.drained


def test_named_tool_choice_requests_and_cross_protocol() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters import convert_request

    response_choice = {"type": "function", "name": "weather"}
    chat_choice = {"type": "function", "function": {"name": "weather"}}
    body = {"input": "go", "tool_choice": response_choice}
    original = deepcopy(body)
    chat = responses_request_to_chat(body)
    assert chat["tool_choice"] == chat_choice
    chat_original = deepcopy(chat)
    assert chat_request_to_responses(chat)["tool_choice"] == response_choice
    assert body == original and chat == chat_original
    for choice in ("auto", "none", "required"):
        assert (
            responses_request_to_chat({"input": "go", "tool_choice": choice})[
                "tool_choice"
            ]
            == choice
        )
        assert (
            chat_request_to_responses({"messages": [], "tool_choice": choice})[
                "tool_choice"
            ]
            == choice
        )
    for choice in ({"type": "file_search"}, {"type": "allowed_tools", "tools": []}):
        assert (
            responses_request_to_chat({"input": "go", "tool_choice": choice})[
                "tool_choice"
            ]
            == choice
        )
        assert (
            chat_request_to_responses({"messages": [], "tool_choice": choice})[
                "tool_choice"
            ]
            == choice
        )
    anthropic = convert_request(
        ProtocolKind.OPENAI_RESPONSES, ProtocolKind.ANTHROPIC, body
    )
    assert anthropic["tool_choice"] == {"type": "tool", "name": "weather"}
    assert (
        convert_request(
            ProtocolKind.ANTHROPIC, ProtocolKind.OPENAI_RESPONSES, anthropic
        )["tool_choice"]
        == response_choice
    )
    gemini = convert_request(ProtocolKind.OPENAI_RESPONSES, ProtocolKind.GEMINI, body)
    assert gemini["toolConfig"]["functionCallingConfig"] == {
        "mode": "ANY",
        "allowedFunctionNames": ["weather"],
    }
    assert (
        convert_request(ProtocolKind.OPENAI_CHAT, ProtocolKind.OPENAI_RESPONSES, chat)[
            "tool_choice"
        ]
        == response_choice
    )
    assert (
        convert_request(ProtocolKind.OPENAI_RESPONSES, ProtocolKind.OPENAI_CHAT, body)[
            "tool_choice"
        ]
        == chat_choice
    )
    assert body == original and chat == chat_original


def test_gemini_forced_function_cross_protocol_without_input_mutation() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters import convert_request

    body = {
        "model": "gemini-2.5-flash",
        "contents": [
            {"role": "user", "parts": [{"text": "What's the weather in Paris?"}]}
        ],
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "weather",
                        "description": "Get the current weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                    {"name": "forecast", "parameters": {"type": "object"}},
                ]
            }
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": ["weather"],
            }
        },
    }
    original = deepcopy(body)
    for target, choice in (
        (
            ProtocolKind.OPENAI_CHAT,
            {"type": "function", "function": {"name": "weather"}},
        ),
        (ProtocolKind.OPENAI_RESPONSES, {"type": "function", "name": "weather"}),
        (ProtocolKind.ANTHROPIC, {"type": "tool", "name": "weather"}),
    ):
        converted = convert_request(ProtocolKind.GEMINI, target, body)
        assert converted["tool_choice"] == choice
        assert len(converted["tools"]) == 2
        assert body == original
    chat = gemini_request_to_chat(body)
    chat["tools"][0]["function"]["parameters"]["properties"]["city"]["type"] = "number"
    chat["tool_choice"]["function"]["name"] = "forecast"
    assert body == original


def test_gemini_tool_choice_string_controls_cross_protocol() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters import convert_request

    for mode, choice in (
        ("NONE", "none"),
        ("AUTO", "auto"),
        ("ANY", "required"),
        ("VALIDATED", "auto"),
    ):
        for names in ({}, {"allowedFunctionNames": []}):
            body = {
                "contents": [{"role": "user", "parts": [{"text": "Hello"}]}],
                "tools": [{"functionDeclarations": [{"name": "weather"}]}],
                "toolConfig": {"functionCallingConfig": {"mode": mode, **names}},
            }
            original = deepcopy(body)
            for target in (ProtocolKind.OPENAI_CHAT, ProtocolKind.OPENAI_RESPONSES):
                assert (
                    convert_request(ProtocolKind.GEMINI, target, body)["tool_choice"]
                    == choice
                )
            assert convert_request(ProtocolKind.GEMINI, ProtocolKind.ANTHROPIC, body)[
                "tool_choice"
            ] == {"type": "any" if choice == "required" else choice}
            assert body == original
    assert "tool_choice" not in gemini_request_to_chat({"contents": []})


def test_gemini_allowed_function_subset_preserves_call_constraint() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters import convert_request

    for mode, choice in (("ANY", "required"), ("AUTO", "auto"), ("VALIDATED", "auto")):
        for allowed_names in (["weather", "forecast"], ["forecast"]):
            body = {
                "contents": [{"role": "user", "parts": [{"text": "Check Paris"}]}],
                "tools": [
                    {
                        "functionDeclarations": [
                            {"name": "weather"},
                            {"name": "forecast"},
                            {"name": "search"},
                        ]
                    }
                ],
                "toolConfig": {
                    "functionCallingConfig": {
                        "mode": mode,
                        "allowedFunctionNames": allowed_names,
                    }
                },
            }
            original = deepcopy(body)
            if mode == "ANY" and len(allowed_names) == 1:
                continue
            chat = gemini_request_to_chat(body)
            assert chat["tool_choice"] == choice
            assert [tool["function"]["name"] for tool in chat["tools"]] == allowed_names
            responses = convert_request(
                ProtocolKind.GEMINI, ProtocolKind.OPENAI_RESPONSES, body
            )
            assert responses["tool_choice"] == choice
            assert [tool["name"] for tool in responses["tools"]] == allowed_names
            assert body == original


def test_gemini_validated_function_subset_cross_protocol_without_input_mutation() -> (
    None
):
    from copy import deepcopy
    from lens_api.gateway.converters import convert_request

    body = {
        "contents": [{"role": "user", "parts": [{"text": "Check Paris"}]}],
        "tools": [
            {"functionDeclarations": [{"name": "weather"}, {"name": "forecast"}]}
        ],
        "toolConfig": {
            "functionCallingConfig": {
                "mode": "VALIDATED",
                "allowedFunctionNames": ["weather"],
            }
        },
    }
    original = deepcopy(body)
    for target in (
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.ANTHROPIC,
    ):
        converted = convert_request(ProtocolKind.GEMINI, target, body)
        assert converted["tool_choice"] == (
            {"type": "auto"} if target == ProtocolKind.ANTHROPIC else "auto"
        )
        assert len(converted["tools"]) == 1
        tool = converted["tools"][0]
        assert (
            tool["function"]["name"]
            if target == ProtocolKind.OPENAI_CHAT
            else tool["name"]
        ) == "weather"
        assert body == original


def test_gemini_invalid_allowed_function_names_are_rejected() -> None:
    import pytest

    for mode in ("ANY", "AUTO", "VALIDATED"):
        for names in (
            None,
            "weather",
            [""],
            [" "],
            [None],
            [1],
            ["weather", "missing"],
            *([["missing"]] if mode != "ANY" else []),
        ):
            body = {
                "tools": [{"functionDeclarations": [{"name": "weather"}]}],
                "toolConfig": {
                    "functionCallingConfig": {
                        "mode": mode,
                        "allowedFunctionNames": names,
                    }
                },
            }
            with pytest.raises(ValueError, match="allowedFunctionNames"):
                gemini_request_to_chat(body)


def test_gemini_tool_images_roundtrip_for_model_capabilities() -> None:
    for model in ("gemini-3-pro-preview", "gemini-2.5-flash", "custom-model"):
        for text in ("", "result"):
            for is_error in (False, True):
                calls = [
                    {"id": cid, "function": {"name": "read", "arguments": "{}"}}
                    for cid in ("first", "second")
                ]
                image = {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AAA"},
                }
                converted = chat_request_to_gemini(
                    {
                        "model": model,
                        "messages": [
                            {"role": "assistant", "tool_calls": calls},
                            *[
                                {
                                    "role": "tool",
                                    "tool_call_id": cid,
                                    "is_error": is_error,
                                    "content": (
                                        [{"type": "text", "text": text}] if text else []
                                    )
                                    + [image],
                                }
                                for cid in ("first", "second")
                            ],
                        ],
                    }
                )
                contents = converted["contents"]
                responses = [
                    part["functionResponse"]
                    for content in contents
                    for part in content["parts"]
                    if "functionResponse" in part
                ]
                for response in responses:
                    assert response["name"] == "read"
                    assert response["response"] == {
                        "error" if is_error else "output": text
                        or "(see attached image)"
                    }
                    if model != "gemini-2.5-flash":
                        assert response["parts"] == [
                            {"inlineData": {"mimeType": "image/png", "data": "AAA"}}
                        ]
                if model == "gemini-2.5-flash":
                    assert len(contents) == 5
                    for index in (2, 4):
                        assert contents[index]["parts"][0] == {
                            "text": "Tool result image:"
                        }
                        assert "inlineData" in contents[index]["parts"][1]
                        assert "functionResponse" in contents[index - 1]["parts"][0]
                reverse = gemini_request_to_chat(converted)
                tools = [m for m in reverse["messages"] if m["role"] == "tool"]
                ids = [c["id"] for c in reverse["messages"][0]["tool_calls"]]
                assert [m["tool_call_id"] for m in tools] == ids
                for tool in tools:
                    assert tool["is_error"] is is_error
                    assert tool["name"] == "read"
                    if model != "gemini-2.5-flash":
                        assert tool["content"] == [
                            {"type": "text", "text": text or "(see attached image)"},
                            image,
                        ]
                    else:
                        assert tool["content"] == (text or "(see attached image)")
                all_images = [
                    part
                    for message in reverse["messages"]
                    if isinstance(message.get("content"), list)
                    for part in message["content"]
                    if part.get("type") == "image_url"
                ]
                assert all_images == [image, image]
                assert "AAA" in json.dumps(chat_request_to_gemini(reverse))


def test_gemini_stream_final_usage_merges_same_chunk_and_tail_updates() -> None:
    chat_usage = {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
        "prompt_tokens_details": {"cached_tokens": 8},
        "completion_tokens_details": {"reasoning_tokens": 3},
    }
    gemini_usage = {
        "promptTokenCount": 20,
        "candidatesTokenCount": 7,
        "totalTokenCount": 30,
        "cachedContentTokenCount": 8,
        "thoughtsTokenCount": 3,
    }
    for reverse in (False, True):
        for tail in (False, True):
            usage_key = "usageMetadata" if reverse else "usage"
            usage = gemini_usage if reverse else chat_usage
            candidate = {
                "content": {"parts": [{"text": "answer"}]},
                "finishReason": "STOP",
            }
            choice = {"delta": {"content": "answer"}, "finish_reason": "stop"}
            finish = {"candidates": [candidate]} if reverse else {"choices": [choice]}
            chunks = []
            if not tail:
                finish[usage_key] = usage
            chunks.append(_sse(finish))
            if tail:
                chunks.append(
                    _sse({usage_key: usage, **({} if reverse else {"choices": []})})
                )
            chunks.append(
                _sse(
                    {
                        usage_key: {
                            "promptTokenCount" if reverse else "prompt_tokens": 20
                        }
                    }
                )
            )
            if not reverse:
                # Empty nested metadata must retain previously supplied reasoning/cache counts.
                chunks.append(
                    _sse(
                        {
                            "usage": {
                                "completion_tokens_details": {},
                                "prompt_tokens_details": {"cached_tokens": None},
                            }
                        }
                    )
                )
            chunks.append(_sse(finish))
            chunks.append(b"data: [DONE]\n\n")
            raw = TrackedAsyncBytes(chunks)
            converter = (
                gemini_stream_to_chat_stream
                if reverse
                else chat_stream_to_gemini_stream
            )
            output = asyncio.run(_collect(converter(raw, "gemini-3-pro-preview")))
            assert raw.drained
            payloads = _json_payloads(output)
            if reverse:
                assert payloads[-1]["usage"] == chat_usage
                assert (
                    sum(bool(p["choices"][0].get("finish_reason")) for p in payloads)
                    == 1
                )
                assert [
                    p["choices"][0]["delta"]["content"]
                    for p in payloads
                    if "content" in p["choices"][0]["delta"]
                ] == ["answer"]
                assert output.count(b"data: [DONE]") == 1
            else:
                assert payloads[-1]["usageMetadata"] == gemini_usage
                assert (
                    sum(bool(p["candidates"][0].get("finishReason")) for p in payloads)
                    == 1
                )
                assert [
                    part["text"]
                    for p in payloads
                    for part in p["candidates"][0]["content"]["parts"]
                ] == ["answer"]


def test_gemini_streams_deliver_text_before_source_is_drained() -> None:
    async def check(reverse: bool) -> None:
        first = (
            {"candidates": [{"content": {"parts": [{"text": "early"}]}}]}
            if reverse
            else {"choices": [{"delta": {"content": "early"}}]}
        )
        last = (
            {"candidates": [{"finishReason": "STOP"}]}
            if reverse
            else {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        )
        raw = TrackedAsyncBytes([_sse(first), _sse(last)])
        converter = (
            gemini_stream_to_chat_stream if reverse else chat_stream_to_gemini_stream
        )
        stream = converter(raw, "m")
        while True:
            part = await anext(stream)
            if b"early" in part:
                break
        assert not raw.drained
        assert raw._index == 1
        await _collect(stream)
        assert raw.drained

    for reverse in (False, True):
        asyncio.run(check(reverse))


def test_chat_to_gemini_emits_parts_with_finish_before_tail_usage() -> None:
    async def check() -> None:
        emitted: list[bytes] = []
        drained = False

        async def source() -> AsyncIterator[bytes]:
            nonlocal drained
            yield _sse(
                {
                    "model": "source-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": "answer",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "first",
                                        "function": {
                                            "name": "read",
                                            "arguments": '{"path":"file"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            )
            early = _json_payloads(b"".join(emitted))
            assert len(early) == 1
            candidate = early[0]["candidates"][0]
            assert "finishReason" not in candidate
            assert candidate["index"] == 0
            assert early[0]["modelVersion"] == "source-model"
            assert candidate["content"]["parts"] == [
                {"text": "answer"},
                {
                    "functionCall": {
                        "name": "read",
                        "args": {"path": "file"},
                    }
                },
            ]
            yield _sse(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            )
            assert len(emitted) == 1
            yield b"data: [DONE]\n\n"
            drained = True

        async for chunk in chat_stream_to_gemini_stream(source(), "alias"):
            emitted.append(chunk)
        assert drained
        payloads = _json_payloads(b"".join(emitted))
        assert len(payloads) == 2
        assert payloads[-1] == {
            "candidates": [
                {
                    "content": {"role": "model", "parts": []},
                    "index": 0,
                    "finishReason": "STOP",
                }
            ],
            "modelVersion": "source-model",
            "usageMetadata": {
                "promptTokenCount": 2,
                "candidatesTokenCount": 3,
                "totalTokenCount": 5,
            },
        }

    asyncio.run(check())


def test_gemini_stream_tail_duplicate_calls_are_ignored() -> None:
    for reverse in (False, True):
        call = {"functionCall": {"id": "first", "name": "read", "args": {}}}
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "first",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ]
        }
        finish = (
            {"candidates": [{"content": {"parts": [call]}, "finishReason": "STOP"}]}
            if reverse
            else {"choices": [{"delta": delta, "finish_reason": "tool_calls"}]}
        )
        converter = (
            gemini_stream_to_chat_stream if reverse else chat_stream_to_gemini_stream
        )
        raw = TrackedAsyncBytes([_sse(finish), _sse(finish)])
        payloads = _json_payloads(
            asyncio.run(_collect(converter(raw, "gemini-3-pro-preview")))
        )
        assert raw.drained
        if reverse:
            assert (
                sum(
                    len(p["choices"][0]["delta"].get("tool_calls", []))
                    for p in payloads
                )
                == 1
            )
            assert payloads[-1]["choices"][0]["finish_reason"] == "tool_calls"
        else:
            assert (
                sum(
                    "functionCall" in part
                    for p in payloads
                    for part in p["candidates"][0]["content"]["parts"]
                )
                == 1
            )


def test_gemini_stream_tail_errors_and_unknown_finishes_fail() -> None:
    import pytest

    for reverse in (False, True):
        finish = (
            {"candidates": [{"finishReason": "STOP"}]}
            if reverse
            else {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        )
        unknown = (
            {"candidates": [{"finishReason": "UNKNOWN"}]}
            if reverse
            else {"choices": [{"delta": {}, "finish_reason": "unknown"}]}
        )
        converter = (
            gemini_stream_to_chat_stream if reverse else chat_stream_to_gemini_stream
        )
        for failure in ({"error": {"message": "failure"}}, unknown):
            with pytest.raises(ValueError):
                asyncio.run(
                    _collect(
                        converter(TrackedAsyncBytes([_sse(finish), _sse(failure)]), "m")
                    )
                )


def test_gemini_tool_result_file_images_survive_reverse_conversion() -> None:
    image = {
        "fileData": {
            "fileUri": "https://example.test/tool.png",
            "mimeType": "image/png",
        }
    }
    for nested in (False, True):
        response = {"name": "read", "id": "first", "response": {"output": "result"}}
        if nested:
            response["parts"] = [image]
        contents = [{"role": "user", "parts": [{"functionResponse": response}]}]
        if not nested:
            contents.append(
                {"role": "user", "parts": [{"text": "Tool result image:"}, image]}
            )
        chat = gemini_request_to_chat({"contents": contents})
        message = chat["messages"][-1]
        assert message["content"][-1] == {
            "type": "image_url",
            "image_url": {"url": "https://example.test/tool.png"},
        }
        assert chat["messages"][0]["name"] == "read"


def test_signed_gemini_text_entrypoint_responses_and_streams() -> None:
    from lens_api.gateway.converters import convert_response

    parts = [
        {"text": "private thought", "thought": True, "thoughtSignature": "think-sig"},
        {"text": "answer", "thoughtSignature": "answer-sig"},
    ]
    body = {
        "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
    }
    for target in (
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.ANTHROPIC,
        ProtocolKind.OPENAI_RESPONSES,
    ):
        result = json.loads(
            convert_response(
                target, ProtocolKind.GEMINI, json.dumps(body).encode(), "m"
            )
        )
        events = _json_payloads(
            asyncio.run(
                _collect(
                    convert_stream_iterator(
                        target,
                        ProtocolKind.GEMINI,
                        TrackedAsyncBytes([_sse(body)]),
                        "m",
                    )
                )
            )
        )
        if target == ProtocolKind.OPENAI_CHAT:
            message = result["choices"][0]["message"]
            assert isinstance(message["content"], str)
            assert message["content"] == "answer"
            assert message["reasoning_content"] == "private thought"
            deltas = [event["choices"][0]["delta"] for event in events]
            assert all(
                isinstance(delta["content"], str)
                for delta in deltas
                if "content" in delta
            )
            assert "".join(delta.get("content", "") for delta in deltas) == "answer"
            assert (
                "".join(delta.get("reasoning_content", "") for delta in deltas)
                == "private thought"
            )
        elif target == ProtocolKind.ANTHROPIC:
            texts = [
                part["text"] for part in result["content"] if part["type"] == "text"
            ]
            assert texts == ["answer"]
            assert all(isinstance(text, str) for text in texts)
            assert [
                part["thinking"]
                for part in result["content"]
                if part["type"] == "thinking"
            ] == ["private thought"]
            deltas = [
                event["delta"]
                for event in events
                if event["type"] == "content_block_delta"
            ]
            assert (
                "".join(
                    delta["text"] for delta in deltas if delta["type"] == "text_delta"
                )
                == "answer"
            )
            assert (
                "".join(
                    delta["thinking"]
                    for delta in deltas
                    if delta["type"] == "thinking_delta"
                )
                == "private thought"
            )
            assert "signature" not in json.dumps(result)
            assert not any(delta["type"] == "signature_delta" for delta in deltas)
        else:
            texts = [
                part["text"]
                for item in result["output"]
                if item["type"] == "message"
                for part in item["content"]
            ]
            assert texts == ["answer"]
            assert all(isinstance(text, str) for text in texts)
            assert (
                "".join(
                    event["delta"]
                    for event in events
                    if event["type"] == "response.output_text.delta"
                )
                == "answer"
            )
            assert (
                "".join(
                    event["delta"]
                    for event in events
                    if event["type"] == "response.reasoning_text.delta"
                )
                == "private thought"
            )
            completed = next(
                event["response"]
                for event in events
                if event["type"] == "response.completed"
            )
            assert [
                part["text"]
                for item in completed["output"]
                if item["type"] == "message"
                for part in item["content"]
            ] == ["answer"]


def test_gemini_text_metadata_roundtrip_empty_images_and_multiple_thoughts() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters.chat_to_gemini import (
        chat_response_to_gemini,
        gemini_response_to_chat,
    )

    for parts in (
        [{"text": "", "thoughtSignature": "empty-sig"}],
        [
            {"text": "first", "thought": True, "thoughtSignature": "first-sig"},
            {"text": "second", "thought": True},
            {"text": "", "thoughtSignature": "empty-sig"},
            {"text": "answer", "thoughtSignature": "answer-sig"},
            {
                "inlineData": {"mimeType": "image/png", "data": "aW1n"},
                "thoughtSignature": "image-sig",
            },
        ],
        [
            {"text": "first", "thought": True},
            {"text": "second", "thought": True},
            {"text": "answer"},
        ],
    ):
        original = deepcopy(parts)
        response = {
            "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]
        }
        chat = gemini_response_to_chat(response, "m")
        message = chat["choices"][0]["message"]
        assert message["_gemini_text_parts"] == [
            part for part in parts if "text" in part
        ]
        restored = chat_response_to_gemini(chat, "m")
        assert restored["candidates"][0]["content"]["parts"] == parts
        restored["candidates"][0]["content"]["parts"][0]["text"] = "changed"
        assert message["_gemini_text_parts"] == [
            part for part in parts if "text" in part
        ]
        assert (
            chat_request_to_gemini({"messages": [message]})["contents"][0]["parts"]
            == parts
        )
        if isinstance(message["content"], list):
            assert message["content"][0] == {"type": "text", "text": "answer"}
            assert all(not part.get("thought") for part in message["content"])
        stream = gemini_stream_to_chat_stream(TrackedAsyncBytes([_sse(response)]), "m")
        output = _json_payloads(
            asyncio.run(_collect(chat_stream_to_gemini_stream(stream, "m")))
        )
        assert [
            part
            for event in output
            for candidate in event["candidates"]
            for part in candidate["content"]["parts"]
        ] == parts
        message["_gemini_text_parts"][0]["text"] = "changed"
        assert parts == original


def test_gemini_metadata_only_stream_frame_is_immediate() -> None:
    signed = {"text": "", "thought": True, "thoughtSignature": "empty-sig"}

    async def check() -> None:
        raw = TrackedAsyncBytes(
            [
                _sse({"candidates": [{"content": {"parts": [signed]}}]}),
                _sse(
                    {
                        "candidates": [
                            {
                                "content": {"parts": [{"text": "answer"}]},
                                "finishReason": "STOP",
                            }
                        ]
                    }
                ),
            ]
        )
        chat = gemini_stream_to_chat_stream(raw, "m")
        await anext(chat)  # Role frame.
        frame = _json_payloads(await anext(chat))[0]
        assert frame["choices"][0]["delta"] == {"_gemini_text_parts": [signed]}
        assert raw._index == 1
        await _collect(chat)

        raw = TrackedAsyncBytes(
            [
                _sse({"choices": [{"delta": {"_gemini_text_parts": [signed]}}]}),
                _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            ]
        )
        gemini = chat_stream_to_gemini_stream(raw, "m")
        frame = _json_payloads(await anext(gemini))[0]
        assert frame["candidates"][0]["content"]["parts"] == [signed]
        assert raw._index == 1
        await _collect(gemini)

    asyncio.run(check())


def test_gemini_legacy_thought_arrays_and_metadata_cleanup() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters import convert_request

    parts = [
        {"text": "private", "thought": True, "thoughtSignature": "s"},
        {"text": "answer"},
    ]
    message = {
        "role": "assistant",
        "content": [{"type": "text", **part} for part in parts],
        "reasoning_content": "private",
    }
    original = deepcopy(message)
    assert (
        chat_request_to_gemini({"messages": [message]})["contents"][0]["parts"] == parts
    )
    message["_gemini_text_parts"] = deepcopy(parts)
    assert (
        chat_request_to_gemini({"messages": [message]})["contents"][0]["parts"] == parts
    )
    assert {
        key: value for key, value in message.items() if key != "_gemini_text_parts"
    } == original
    source = {"contents": [{"role": "model", "parts": parts}]}
    source_copy = deepcopy(source)
    cleaned = convert_request(
        ProtocolKind.GEMINI, ProtocolKind.OPENAI_CHAT, source, preserve_reasoning=True
    )
    assert cleaned["messages"][0]["content"] == "answer"
    assert cleaned["messages"][0]["reasoning_content"] == "private"
    assert "_gemini_text_parts" not in cleaned["messages"][0]
    assert source == source_copy


def test_signed_chat_sse_restoration_roundtrip_all_text_frames() -> None:
    from lens_api.gateway.converters import convert_response
    from lens_api.gateway.service.stream_restore import _distill_stream_response_content

    frames = [
        [{"text": "before-thought", "thought": True}, {"text": "before"}],
        [
            {"text": "signed-thought", "thought": True, "thoughtSignature": "a"},
            {"text": "signed-answer", "thoughtSignature": "b"},
        ],
        [{"text": "", "thought": True, "thoughtSignature": "empty"}],
        [{"text": "second-answer", "thoughtSignature": "c"}],
        [{"text": "after-thought", "thought": True}, {"text": "after"}],
        [
            {
                "functionCall": {"name": "lookup", "args": {"q": "x"}},
                "thoughtSignature": "tool-sig",
            }
        ],
    ]
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "candidates": [
                        {
                            "content": {"parts": parts},
                            **(
                                {"finishReason": "STOP"} if i == len(frames) - 1 else {}
                            ),
                        }
                    ]
                }
            )
            for i, parts in enumerate(frames)
        ]
    )
    chat_sse = asyncio.run(_collect(gemini_stream_to_chat_stream(raw, "m")))
    distilled = _distill_stream_response_content(
        ProtocolKind.OPENAI_CHAT, chat_sse.decode()
    )
    assert distilled is not None
    message = json.loads(distilled)["choices"][0]["message"]
    expected = [part for parts in frames for part in parts if "text" in part]
    assert message["_gemini_text_parts"] == expected
    assert message["content"] == "beforesigned-answersecond-answerafter"
    assert message["reasoning_content"] == "before-thoughtsigned-thoughtafter-thought"
    result = json.loads(
        convert_response(
            ProtocolKind.GEMINI, ProtocolKind.OPENAI_CHAT, distilled.encode(), "m"
        )
    )
    assert result["candidates"][0]["content"]["parts"] == expected + frames[-1]
    assert raw.drained


def test_restore_signed_chat_mixed_choices_copies_text_history() -> None:
    from copy import deepcopy
    from lens_api.gateway.converters.chat_to_gemini import chat_response_to_gemini

    image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aW1n"},
        "thoughtSignature": "image-sig",
    }
    signed = [{"text": "signed", "thoughtSignature": "sig"}]
    empty = {"text": "", "thoughtSignature": "empty-sig"}
    before = [
        "plain",
        {"type": "text", "text": "hidden duplicate", "thought": True},
        {"type": "input_text", "text": "before"},
        image,
        {"type": "audio", "text": "not text"},
    ]
    after = [{"type": "output_text", "text": "after"}]
    payloads = [
        {
            "choices": [
                {"index": 0, "delta": {"reasoning": "think-before", "content": before}},
                {"index": 1, "delta": {"content": "ordinary"}},
                {"index": 2, "delta": {"_gemini_text_parts": [empty]}},
            ]
        },
        {
            "choices": [
                {"index": 1, "delta": {"reasoning_content": "plain-thought"}},
                {
                    "index": 0,
                    "delta": {
                        "content": [{"type": "text", "text": "signed"}],
                        "_gemini_text_parts": signed,
                    },
                },
                {"index": 2, "delta": {"content": "tail"}, "finish_reason": "stop"},
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"reasoning_content": "think-after", "content": after},
                    "finish_reason": "stop",
                },
                {"index": 1, "delta": {"content": "-tail"}, "finish_reason": "stop"},
            ]
        },
    ]
    original = deepcopy(payloads)
    restored = _restore_openai_chat_stream(payloads)
    assert restored is not None
    messages = [choice["message"] for choice in restored["choices"]]
    assert messages[0]["_gemini_text_parts"] == [
        {"text": "think-before", "thought": True},
        {"text": "plain"},
        {"text": "before"},
        *signed,
        {"text": "think-after", "thought": True},
        {"text": "after"},
    ]
    assert (
        messages[0]["content"] == before + [{"type": "text", "text": "signed"}] + after
    )
    assert messages[0]["reasoning"] == "think-before"
    assert messages[0]["reasoning_content"] == "think-after"
    assert messages[1] == {
        "role": "assistant",
        "content": "ordinary-tail",
        "reasoning_content": "plain-thought",
    }
    assert messages[2]["_gemini_text_parts"] == [empty, {"text": "tail"}]
    assert messages[2]["content"] == "tail"
    gemini = chat_response_to_gemini(restored, "m")
    assert gemini["candidates"][0]["content"]["parts"] == messages[0][
        "_gemini_text_parts"
    ] + [
        {
            "inlineData": {"mimeType": "image/png", "data": "aW1n"},
            "thoughtSignature": "image-sig",
        }
    ]
    assert payloads == original
    messages[0]["_gemini_text_parts"][3]["text"] = "changed"
    messages[0]["content"][3]["image_url"]["url"] = "changed"
    messages[2]["_gemini_text_parts"][0]["text"] = "changed"
    assert payloads == original


def test_restore_plain_chat_does_not_add_gemini_text_metadata() -> None:
    restored = _restore_openai_chat_stream(
        [
            {
                "choices": [
                    {"delta": {"content": "answer", "reasoning_content": "thought"}}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
    )
    assert restored is not None
    assert restored["choices"][0]["message"] == {
        "role": "assistant",
        "content": "answer",
        "reasoning_content": "thought",
    }
