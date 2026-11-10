from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import pytest

from lens_api.gateway.converters.chat_to_anthropic import (
    anthropic_stream_to_chat_stream,
    chat_stream_to_anthropic_stream,
)
from lens_api.gateway.converters.chat_to_gemini import (
    chat_stream_to_gemini_stream,
    gemini_stream_to_chat_stream,
)
from lens_api.gateway.converters.chat_to_responses import (
    chat_stream_to_responses_stream,
    responses_stream_to_chat_stream,
)

StreamConverter = Callable[[AsyncIterator[bytes], str], AsyncIterator[bytes]]


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


def _sse(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _json_payloads(output: bytes) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in output.decode().splitlines():
        if not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ")
        if data == "[DONE]":
            continue
        loaded = json.loads(data)
        if isinstance(loaded, dict):
            payloads.append(loaded)
    return payloads


def test_anthropic_stream_to_chat_drains_after_message_stop() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_1",
                        "model": "claude-opus-4-6",
                        "usage": {"input_tokens": 3, "output_tokens": 0},
                    },
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

    output = asyncio.run(_collect(anthropic_stream_to_chat_stream(raw, "model")))

    assert b"data: [DONE]" in output
    assert raw.drained is True


def test_responses_stream_to_chat_drains_after_response_completed() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "type": "response.created",
                    "response": {
                        "id": "resp_1",
                        "model": "gpt",
                        "status": "in_progress",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "model": "gpt",
                        "status": "completed",
                        "usage": {
                            "input_tokens": 3,
                            "output_tokens": 2,
                            "total_tokens": 5,
                            "input_tokens_details": {"cached_tokens": 2},
                        },
                    },
                }
            ),
        ]
    )

    output = asyncio.run(_collect(responses_stream_to_chat_stream(raw, "model")))
    payloads = _json_payloads(output)

    assert b"data: [DONE]" in output
    assert raw.drained is True
    assert payloads[-1]["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 2},
    }


def test_chat_stream_to_responses_preserves_cached_tokens() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "model": "gpt",
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "prompt_tokens_details": {"cached_tokens": 2},
                    },
                }
            ),
        ]
    )

    output = asyncio.run(_collect(chat_stream_to_responses_stream(raw, "model")))
    payloads = _json_payloads(output)

    assert payloads[-1]["response"]["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "input_tokens_details": {"cached_tokens": 2},
    }


def test_gemini_stream_to_chat_drains_after_finish_reason() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "ok"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 3,
                        "candidatesTokenCount": 2,
                        "totalTokenCount": 5,
                    },
                }
            )
        ]
    )

    output = asyncio.run(_collect(gemini_stream_to_chat_stream(raw, "model")))

    assert b"data: [DONE]" in output
    assert raw.drained is True


def test_chat_stream_parser_drains_after_done_marker() -> None:
    raw = TrackedAsyncBytes(
        [
            _sse(
                {
                    "choices": [
                        {
                            "delta": {"role": "assistant"},
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            b"data: [DONE]\n\n",
        ]
    )

    with pytest.raises(ValueError, match="without a finish reason"):
        asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "model")))

    assert raw.drained is True


@pytest.mark.parametrize(
    "converter",
    [
        chat_stream_to_anthropic_stream,
        chat_stream_to_responses_stream,
        chat_stream_to_gemini_stream,
    ],
)
@pytest.mark.parametrize("done_marker", [False, True])
def test_chat_outbound_requires_real_finish_reason(
    converter: StreamConverter, done_marker: bool
) -> None:
    chunks = [
        _sse({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})
    ]
    if done_marker:
        chunks.append(b"data: [DONE]\n\n")
    raw = TrackedAsyncBytes(chunks)
    with pytest.raises(ValueError, match="without a finish reason"):
        asyncio.run(_collect(converter(raw, "model")))
    assert raw.drained


@pytest.mark.parametrize(
    "converter",
    [
        chat_stream_to_anthropic_stream,
        chat_stream_to_responses_stream,
        chat_stream_to_gemini_stream,
    ],
)
def test_chat_outbound_drains_after_real_finish(converter: StreamConverter) -> None:
    raw = TrackedAsyncBytes(
        [
            _sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
    )
    asyncio.run(_collect(converter(raw, "model")))
    assert raw.drained


@pytest.mark.parametrize("finish", ["length", "content_filter", "stop", "tool_calls"])
@pytest.mark.parametrize(
    "converter", [chat_stream_to_anthropic_stream, chat_stream_to_responses_stream]
)
def test_chat_outbound_truncated_tool_arguments(
    converter: StreamConverter, finish: str
) -> None:
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
                                        "function": {
                                            "name": "lookup",
                                            "arguments": '{"key":',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": finish,
                        }
                    ]
                }
            )
        ]
    )
    if finish in {"stop", "tool_calls"}:
        with pytest.raises(ValueError, match="Invalid tool call arguments JSON"):
            asyncio.run(_collect(converter(raw, "model")))
        return
    payloads = _json_payloads(asyncio.run(_collect(converter(raw, "model"))))
    if converter is chat_stream_to_anthropic_stream:
        terminal = next(p for p in payloads if p["type"] == "message_delta")
        assert terminal["delta"]["stop_reason"] == (
            "max_tokens" if finish == "length" else "refusal"
        )
    else:
        assert payloads[-1]["type"] == "response.incomplete"
        response = payloads[-1]["response"]
        assert response["incomplete_details"] == {
            "reason": "max_output_tokens" if finish == "length" else "content_filter"
        }
        assert response["output"][0]["status"] == "incomplete"
    assert raw.drained


@pytest.mark.parametrize("prefix", [False, True, "items"])
@pytest.mark.parametrize("finish", ["completed", "length", "content_filter"])
def test_responses_backfills_terminal_output_once(
    prefix: bool | str, finish: str
) -> None:
    native = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "think"}],
        "encrypted_content": "opaque",
    }
    items = [
        {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"key":1}',
        },
        native,
    ]
    events = []
    if prefix == "items":
        events = [
            {"type": "response.output_item.done", "output_index": index, "item": item}
            for index, item in enumerate(items)
        ]
    elif prefix:
        events = [
            {"type": "response.output_text.delta", "output_index": 0, "delta": "ans"},
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "delta": '{"key":',
            },
            {
                "type": "response.reasoning_text.delta",
                "output_index": 2,
                "delta": "thi",
            },
        ]
    response = {
        "status": "completed" if finish == "completed" else "incomplete",
        "output": items,
    }
    if finish != "completed":
        response["incomplete_details"] = {
            "reason": "max_output_tokens" if finish == "length" else "content_filter"
        }
    events.append(
        {
            "type": (
                "response.completed" if finish == "completed" else "response.incomplete"
            ),
            "response": response,
        }
    )
    raw = TrackedAsyncBytes([_sse(e) for e in events])
    payloads = _json_payloads(
        asyncio.run(_collect(responses_stream_to_chat_stream(raw, "model")))
    )
    deltas = [p["choices"][0]["delta"] for p in payloads]
    assert "".join(d.get("content", "") for d in deltas) == "answer"
    assert "".join(d.get("reasoning_content", "") for d in deltas) == "think"
    calls = [call for d in deltas for call in d.get("tool_calls", [])]
    assert "".join(c["function"].get("arguments", "") for c in calls) == '{"key":1}'
    assert any(
        c.get("id") == "call_1" and c["function"].get("name") == "lookup" for c in calls
    )
    assert deltas[-1]["_responses_reasoning_items"] == [native]
    assert payloads[-1]["choices"][0]["finish_reason"] == (
        "tool_calls" if finish == "completed" else finish
    )
    assert raw.drained


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("summary", [False, True])
def test_chat_responses_native_reasoning_finalization(
    count: int, summary: bool
) -> None:
    items = [
        {
            "id": f"rs_{i}",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "think"}] if summary else [],
            "encrypted_content": f"opaque_{i}",
        }
        for i in range(count)
    ]
    delta = {"_responses_reasoning_items": items}
    if summary:
        delta["reasoning_content"] = "think"
    raw = TrackedAsyncBytes(
        [_sse({"choices": [{"delta": delta, "finish_reason": "stop"}]})]
    )
    payloads = _json_payloads(
        asyncio.run(_collect(chat_stream_to_responses_stream(raw, "model")))
    )
    assert payloads[-1]["response"]["output"] == items
    assert [
        p["item"] for p in payloads if p["type"] == "response.output_item.done"
    ] == items
    assert not any(
        p["type"].startswith("response.function_call_arguments") for p in payloads
    )


@pytest.mark.parametrize(
    "converter",
    [
        chat_stream_to_anthropic_stream,
        chat_stream_to_responses_stream,
        chat_stream_to_gemini_stream,
    ],
)
def test_chat_outbound_propagates_early_read_error(converter: StreamConverter) -> None:
    async def broken_stream() -> AsyncIterator[bytes]:
        yield _sse(
            {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}
        )
        raise OSError("early read failure")

    with pytest.raises(OSError, match="early read failure"):
        asyncio.run(_collect(converter(broken_stream(), "model")))


def test_responses_filtered_and_length_terminals() -> None:
    from lens_api.gateway.converters.chat_to_responses import chat_response_to_responses

    for finish, reason in [
        ("content_filter", "content_filter"),
        ("length", "max_output_tokens"),
    ]:
        body = {
            "choices": [{"message": {"content": "partial"}, "finish_reason": finish}]
        }
        response = chat_response_to_responses(body, "model")
        assert response["status"] == "incomplete"
        assert response["incomplete_details"] == {"reason": reason}
        raw = TrackedAsyncBytes(
            [
                _sse(
                    {
                        "choices": [
                            {"delta": {"content": "partial"}, "finish_reason": finish}
                        ]
                    }
                )
            ]
        )
        payloads = _json_payloads(
            asyncio.run(_collect(chat_stream_to_responses_stream(raw, "model")))
        )
        assert payloads[-1]["type"] == "response.incomplete"
        assert payloads[-1]["response"]["status"] == "incomplete"
        assert payloads[-1]["response"]["incomplete_details"] == {"reason": reason}


def test_responses_failed_and_cancelled_never_emit_success() -> None:
    import pytest
    from lens_api.gateway.converters.chat_to_responses import responses_response_to_chat

    for status in ("failed", "cancelled"):
        with pytest.raises(ValueError, match=status):
            responses_response_to_chat({"status": status, "output": []}, "model")
        for event in (
            f"response.{status}",
            "response.completed",
            "response.incomplete",
        ):
            seen: list[bytes] = []

            async def consume() -> None:
                raw = TrackedAsyncBytes(
                    [_sse({"type": event, "response": {"status": status}})]
                )
                async for chunk in responses_stream_to_chat_stream(raw, "model"):
                    seen.append(chunk)

            with pytest.raises(ValueError, match=status):
                asyncio.run(consume())
            assert b"[DONE]" not in b"".join(seen)
            assert all(
                p["choices"][0]["finish_reason"] is None
                for p in _json_payloads(b"".join(seen))
            )


def test_responses_request_replays_native_items_only() -> None:
    from lens_api.gateway.converters.chat_to_responses import (
        chat_request_to_responses,
        responses_request_to_chat,
    )

    native = {
        "type": "reasoning",
        "id": "rs_native",
        "summary": [{"type": "summary_text", "text": "visible"}],
        "encrypted_content": "opaque",
    }
    call = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "lookup",
        "arguments": "{}",
    }
    chat = responses_request_to_chat({"input": [native, call]})
    assert chat["messages"][0]["reasoning_content"] == "visible"
    replay = chat_request_to_responses(chat, preserve_reasoning=True)
    assert replay["input"] == [native, call]
    assert not any(
        item.get("type") == "reasoning"
        for item in chat_request_to_responses(chat)["input"]
    )
    for reasoning in (None, "visible"):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }
        if reasoning:
            message["reasoning_content"] = reasoning
        replay = chat_request_to_responses(
            {"messages": [message]}, preserve_reasoning=True
        )
        assert [item["type"] for item in replay["input"]] == ["function_call"]
    summary_chat = responses_request_to_chat(
        {
            "input": [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "visible"}],
                },
                call,
            ]
        }
    )
    assert summary_chat["messages"][0]["reasoning_content"] == "visible"
    assert [
        item["type"]
        for item in chat_request_to_responses(summary_chat, preserve_reasoning=True)[
            "input"
        ]
    ] == ["function_call"]


def test_responses_nonstream_native_reasoning_roundtrip() -> None:
    from lens_api.gateway.converters.chat_to_responses import (
        chat_response_to_responses,
        responses_response_to_chat,
        chat_request_to_responses,
    )

    native = {
        "type": "reasoning",
        "id": "rs_native",
        "summary": [{"type": "summary_text", "text": "visible"}],
        "encrypted_content": "opaque",
    }
    chat = responses_response_to_chat(
        {
            "status": "completed",
            "output": [
                native,
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
            ],
        },
        "model",
    )
    message = chat["choices"][0]["message"]
    assert message["reasoning_content"] == "visible"
    assert chat_response_to_responses(chat, "model")["output"][0] == native
    assert (
        chat_request_to_responses({"messages": [message]}, preserve_reasoning=True)[
            "input"
        ][0]
        == native
    )


def test_responses_stream_reasoning_suffix_and_terminal_encryption_roundtrip() -> None:
    native = {
        "type": "reasoning",
        "id": "rs_native",
        "summary": [
            {"type": "summary_text", "text": "first"},
            {"type": "summary_text", "text": "second"},
        ],
    }
    events = [
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "summary_index": 0,
            "delta": "fir",
        },
        {
            "type": "response.reasoning_summary_part.done",
            "output_index": 0,
            "summary_index": 0,
            "part": native["summary"][0],
        },
        {
            "type": "response.reasoning_summary_part.done",
            "output_index": 0,
            "summary_index": 0,
            "part": native["summary"][0],
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "summary_index": 1,
            "delta": "sec",
        },
        {"type": "response.output_item.done", "output_index": 0, "item": native},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [{**native, "encrypted_content": "opaque"}],
            },
        },
    ]
    output = asyncio.run(
        _collect(
            responses_stream_to_chat_stream(
                TrackedAsyncBytes([_sse(event) for event in events]), "model"
            )
        )
    )
    payloads = _json_payloads(output)
    assert (
        "".join(p["choices"][0]["delta"].get("reasoning_content", "") for p in payloads)
        == "first" + chr(10) * 2 + "second"
    )
    assert payloads[-1]["choices"][0]["delta"]["_responses_reasoning_items"] == [
        {**native, "encrypted_content": "opaque"}
    ]
    converted = _json_payloads(
        asyncio.run(
            _collect(
                chat_stream_to_responses_stream(TrackedAsyncBytes([output]), "model")
            )
        )
    )
    assert converted[-1]["response"]["output"] == [
        {**native, "encrypted_content": "opaque"}
    ]


def test_responses_reasoning_done_only_emits_text_once() -> None:
    for prefix in ("", "thi", "thinking"):
        native = {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "thinking"}],
        }
        events = [
            {
                "type": "response.reasoning_text.delta",
                "output_index": 0,
                "delta": prefix,
            },
            {"type": "response.output_item.done", "output_index": 0, "item": native},
            {"type": "response.output_item.done", "output_index": 0, "item": native},
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        payloads = _json_payloads(
            asyncio.run(
                _collect(
                    responses_stream_to_chat_stream(
                        TrackedAsyncBytes([_sse(event) for event in events]), "model"
                    )
                )
            )
        )
        assert (
            "".join(
                p["choices"][0]["delta"].get("reasoning_content", "") for p in payloads
            )
            == "thinking"
        )


def test_responses_assistant_image_only_and_part_order() -> None:
    from lens_api.gateway.converters._shared import _responses_input_to_chat_parts

    image = {"type": "output_image", "image_url": "data:image/png;base64,AA"}
    for parts in ([image], [image, {"type": "output_text", "text": "after"}, image]):
        messages, _, _ = _responses_input_to_chat_parts(
            [
                {"role": "assistant", "content": parts},
                {"role": "user", "content": "next"},
            ]
        )
        assert [message["role"] for message in messages] == ["assistant", "user"]
        assert [part["type"] for part in messages[0]["content"]] == [
            "image_url" if part is image else "text" for part in parts
        ]


@pytest.mark.parametrize("arguments", ["{", "{}"])
@pytest.mark.parametrize(
    "completion", ["missing", "arguments", "item", "terminal", "parallel"]
)
@pytest.mark.parametrize("reason", [None, "max_output_tokens", "content_filter"])
def test_responses_requires_all_tool_items_completed(
    arguments: str, completion: str, reason: str | None
) -> None:
    item = {
        "type": "function_call",
        "id": "fc1",
        "call_id": "c1",
        "name": "f",
        "arguments": "",
    }
    events = [
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": arguments,
        },
    ]
    final_item = {**item, "arguments": "{}"}
    if completion == "arguments":
        events.append(
            {
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "arguments": "{}",
            }
        )
    if completion in ("item", "parallel"):
        events.append(
            {"type": "response.output_item.done", "output_index": 0, "item": final_item}
        )
    if completion == "parallel":
        events.append(
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {**item, "id": "fc2", "call_id": "c2", "arguments": "{}"},
            }
        )
    response = {"status": "incomplete" if reason else "completed"}
    if reason:
        response["incomplete_details"] = {"reason": reason}
    if completion == "terminal":
        response["output"] = [final_item]
    events.append(
        {
            "type": "response.incomplete" if reason else "response.completed",
            "response": response,
        }
    )
    raw = TrackedAsyncBytes([_sse(event) for event in events])
    seen: list[bytes] = []

    async def consume() -> None:
        async for chunk in responses_stream_to_chat_stream(raw, "model"):
            seen.append(chunk)

    if reason is None and completion in ("missing", "arguments", "parallel"):
        with pytest.raises(ValueError, match="unfinished tool"):
            asyncio.run(consume())
        assert b"[DONE]" not in b"".join(seen)
        assert all(
            p["choices"][0]["finish_reason"] is None
            for p in _json_payloads(b"".join(seen))
        )
    else:
        asyncio.run(consume())
        assert _json_payloads(b"".join(seen))[-1]["choices"][0]["finish_reason"] == (
            "length"
            if reason == "max_output_tokens"
            else "content_filter" if reason else "tool_calls"
        )
        assert raw.drained
