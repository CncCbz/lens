from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from lens_api.gateway.converters.chat_to_anthropic import (
    anthropic_stream_to_chat_stream,
    chat_stream_to_anthropic_stream,
)
from lens_api.gateway.converters.chat_to_gemini import gemini_stream_to_chat_stream
from lens_api.gateway.converters.chat_to_responses import (
    chat_stream_to_responses_stream,
    responses_stream_to_chat_stream,
)


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

    output = asyncio.run(_collect(chat_stream_to_anthropic_stream(raw, "model")))

    assert b"message_stop" in output
    assert raw.drained is True
