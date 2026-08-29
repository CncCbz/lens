from __future__ import annotations

import asyncio
import json
from time import perf_counter

from lens_api.gateway.converters._chat_stream import (
    ChatToolCalls,
    normalize_chat_stream,
)
from lens_api.gateway.service.runtime_context import _RequestDeadline
from lens_api.gateway.service.stream_restore import _restore_openai_chat_stream


def test_chat_tool_calls_splits_reused_index() -> None:
    state = ChatToolCalls()
    first = state.update(
        {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "f", "arguments": '{"a":1}'},
        }
    )
    second = state.update(
        {
            "index": 0,
            "id": "call_2",
            "type": "function",
            "function": {"name": "g", "arguments": '{"b":2}'},
        }
    )
    assert first.index == 0
    assert second.index == 1
    assert [call.arguments for call in state] == ['{"a":1}', '{"b":2}']


def test_normalize_chat_stream_rewrites_reused_index() -> None:
    async def raw() -> object:
        yield (
            b"data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "f",
                                            "arguments": '{"a":1}',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ).encode()
            + b"\n\n"
        )
        yield (
            b"data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_2",
                                        "function": {
                                            "name": "g",
                                            "arguments": '{"b":2}',
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ).encode()
            + b"\n\n"
        )

    async def collect() -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        async for frame in normalize_chat_stream(raw()):
            line = frame.decode().strip()
            if line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
        return payloads

    payloads = asyncio.run(collect())
    indexes = [
        tc["index"]
        for payload in payloads
        for choice in payload["choices"]
        for tc in choice["delta"]["tool_calls"]
    ]
    assert indexes == [0, 1]


def test_restore_openai_chat_stream_splits_reused_index() -> None:
    restored = _restore_openai_chat_stream(
        [
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
                                    "id": "call_1",
                                    "function": {
                                        "name": "f",
                                        "arguments": '{"a":1}',
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_2",
                                    "function": {
                                        "name": "g",
                                        "arguments": '{"b":2}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]
    )
    assert restored is not None
    calls = restored["choices"][0]["message"]["tool_calls"]
    assert [call["id"] for call in calls] == ["call_1", "call_2"]
    assert [call["function"]["arguments"] for call in calls] == [
        '{"a":1}',
        '{"b":2}',
    ]


def test_request_deadline_switches_to_idle_after_first_chunk() -> None:
    deadline = _RequestDeadline(
        started_at=perf_counter(),
        first_token_timeout_seconds=30,
        stream_idle_timeout_seconds=5,
    )
    first = deadline.stream_chunk_wait_seconds(has_seen_first_chunk=False)
    idle = deadline.stream_chunk_wait_seconds(has_seen_first_chunk=True)
    assert first is not None and first > 20
    assert idle == 5
    assert "first-token" in deadline.timeout_message(kind="first_token")
    assert "idle" in deadline.timeout_message(kind="stream_idle")
