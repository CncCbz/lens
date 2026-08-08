from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lens_api.core.config import Settings
from lens_api.gateway.service.proxy_upstream import (
    _build_json_result,
    _build_sse_to_json_result,
    _build_stream_result,
    _call_channel,
)
from lens_api.gateway.service.runtime_context import _RequestDeadline
from lens_api.gateway.upstreams import build_upstream_request
from lens_api.models import ChannelConfig, ChannelKeyItem, ProtocolKind


def _channel(protocol: ProtocolKind) -> ChannelConfig:
    return ChannelConfig(
        id="ch",
        name="Channel",
        protocol=protocol,
        base_url="https://provider.example",
        api_key="upstream-key",
        keys=[ChannelKeyItem(id="key", key="upstream-key")],
    )


def _deadline() -> _RequestDeadline:
    return _RequestDeadline(started_at=time.time(), timeout_seconds=30)


def _chat_sse_body(text: str = "hi", *, complete: bool = True) -> bytes:
    chunks = [
        f"data: {json.dumps({'id': 'chatcmpl_1', 'model': 'm', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': text}, 'finish_reason': None}]})}\n\n".encode(),
    ]
    if complete:
        chunks.append(
            f"data: {json.dumps({'id': 'chatcmpl_1', 'model': 'm', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}})}\n\n".encode()
        )
    chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)


def _chat_json_body() -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl_1",
            "object": "chat.completion",
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
        }
    ).encode()


async def _collect(response) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# _build_sse_to_json_result (non-stream request + upstream SSE)
# ---------------------------------------------------------------------------


def test_sse_to_json_complete_chat_stream_returns_json() -> None:
    channel = _channel(ProtocolKind.OPENAI_CHAT)
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream", "x-request-id": "r1"},
        content=_chat_sse_body(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    result = asyncio.run(
        _build_sse_to_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            "m",
            None,
            None,
            False,
        )
    )
    assert result.is_stream is False
    assert result.response.media_type == "application/json"
    assert result.response.headers.get("content-type") == "application/json"
    parsed = json.loads(result.response.body)
    assert parsed["choices"][0]["message"]["content"] == "hi"
    assert parsed["choices"][0]["finish_reason"] == "stop"
    assert parsed["usage"]["total_tokens"] == 5


def test_sse_to_json_incomplete_chat_stream_raises_502() -> None:
    channel = _channel(ProtocolKind.OPENAI_CHAT)
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_chat_sse_body(complete=False),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            _build_sse_to_json_result(
                response,
                channel,
                ProtocolKind.OPENAI_CHAT,
                "m",
                None,
                None,
                False,
            )
        )
    assert exc_info.value.status_code == 502


def test_sse_to_json_converts_protocol_to_client() -> None:
    # Upstream is Anthropic SSE, client speaks Chat.
    channel = _channel(ProtocolKind.ANTHROPIC)
    anthropic_sse = (
        b'data: {"type":"message_start","message":{"id":"msg_1","model":"claude","usage":{"input_tokens":3,"output_tokens":0}}}\n\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=anthropic_sse,
        request=httpx.Request("POST", "https://provider.example/v1/messages"),
    )
    result = asyncio.run(
        _build_sse_to_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            "claude",
            None,
            None,
            False,
        )
    )
    assert result.is_stream is False
    assert result.response.media_type == "application/json"
    parsed = json.loads(result.response.body)
    # Converted to Chat format for the Chat client.
    assert parsed["object"] == "chat.completion"
    assert parsed["choices"][0]["message"]["content"] == "hi"
    assert parsed["choices"][0]["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# _build_json_result (normal non-stream JSON path)
# ---------------------------------------------------------------------------


def test_build_json_result_reads_body_with_aread() -> None:
    channel = _channel(ProtocolKind.OPENAI_CHAT)
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=_chat_json_body(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    result = asyncio.run(
        _build_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            {"model": "m"},
            None,
            None,
            False,
        )
    )
    assert result.is_stream is False
    parsed = json.loads(result.response.body)
    assert parsed["choices"][0]["message"]["content"] == "hi"


def test_build_json_result_streamed_body_does_not_raise_response_not_read() -> None:
    """A streamed (not yet read) httpx response must be read via aread(), not
    via the ``.content`` property, which raises httpx.ResponseNotRead."""

    async def body_iter():
        yield _chat_json_body()

    channel = _channel(ProtocolKind.OPENAI_CHAT)
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=body_iter(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    # Sanity: accessing .content before read raises ResponseNotRead.
    with pytest.raises(httpx.ResponseNotRead):
        _ = response.content
    result = asyncio.run(
        _build_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            {"model": "m"},
            None,
            None,
            False,
        )
    )
    parsed = json.loads(result.response.body)
    assert parsed["choices"][0]["message"]["content"] == "hi"


# ---------------------------------------------------------------------------
# _build_stream_result (converted stream media type / headers)
# ---------------------------------------------------------------------------


def test_build_stream_result_converted_removes_upstream_content_type() -> None:
    channel = _channel(ProtocolKind.ANTHROPIC)
    anthropic_sse = (
        b'data: {"type":"message_start","message":{"id":"msg_1","model":"claude"}}\n\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}\n\n'
        b'data: {"type":"message_stop"}\n\n'
    )
    response = httpx.Response(
        200,
        headers={
            # Deliberately non-SSE upstream type to prove the converted
            # stream advertises the target media type instead.
            "content-type": "application/x-ndjson",
            "x-request-id": "r1",
        },
        content=anthropic_sse,
        request=httpx.Request("POST", "https://provider.example/v1/messages"),
    )
    result = asyncio.run(
        _build_stream_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            {"model": "m", "stream": True},
            None,
            time.time(),
            False,
            deadline=_deadline(),
        )
    )
    assert result.is_stream is True
    assert result.response.media_type == "text/event-stream"
    assert result.response.headers.get("content-type").startswith("text/event-stream")
    body = asyncio.run(_collect(result.response))
    # Anthropic -> Chat output is Chat SSE framing ending in [DONE].
    assert b'"object": "chat.completion.chunk"' in body
    assert b"data: [DONE]" in body


def test_build_stream_result_passthrough_keeps_upstream_content_type() -> None:
    channel = _channel(ProtocolKind.OPENAI_CHAT)
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_chat_sse_body(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    result = asyncio.run(
        _build_stream_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            {"model": "m", "stream": True},
            None,
            time.time(),
            False,
            deadline=_deadline(),
        )
    )
    assert result.is_stream is True
    assert result.response.media_type == "text/event-stream"
    assert result.response.headers.get("content-type") == "text/event-stream"


def test_call_channel_stream_request_receiving_json_is_rejected() -> None:
    channel = _channel(ProtocolKind.OPENAI_CHAT)
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    upstream = build_upstream_request(channel, body, Settings(auth_secret_key="s"))
    json_response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=_chat_json_body(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    with (
        patch(
            "lens_api.gateway.service.proxy_upstream._resolve_http_client",
            return_value=(object(), False),
        ),
        patch(
            "lens_api.gateway.service.proxy_upstream._send_upstream",
            new=AsyncMock(return_value=json_response),
        ),
    ):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                _call_channel(
                    channel,
                    body,
                    upstream,
                    b"{}",
                    None,
                    _deadline(),
                    credential_id=None,
                    probe_owner=None,
                    client_protocol=ProtocolKind.OPENAI_CHAT,
                )
            )
    assert exc_info.value.status_code == 502


def test_call_channel_non_stream_request_receiving_sse_returns_json() -> None:
    channel = _channel(ProtocolKind.OPENAI_CHAT)
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }
    upstream = build_upstream_request(channel, body, Settings(auth_secret_key="s"))
    sse_response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_chat_sse_body(),
        request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
    )
    with (
        patch(
            "lens_api.gateway.service.proxy_upstream._resolve_http_client",
            return_value=(object(), False),
        ),
        patch(
            "lens_api.gateway.service.proxy_upstream._send_upstream",
            new=AsyncMock(return_value=sse_response),
        ),
    ):
        result = asyncio.run(
            _call_channel(
                channel,
                body,
                upstream,
                b"{}",
                None,
                _deadline(),
                credential_id=None,
                probe_owner=None,
                client_protocol=ProtocolKind.OPENAI_CHAT,
            )
        )
    assert result.is_stream is False
    assert result.response.media_type == "application/json"
    parsed = json.loads(result.response.body)
    assert parsed["choices"][0]["message"]["content"] == "hi"


# ---------------------------------------------------------------------------
# Gemini alt=sse URL construction
# ---------------------------------------------------------------------------


def test_gemini_stream_request_uses_alt_sse() -> None:
    channel = _channel(ProtocolKind.GEMINI)
    body = {
        "model": "gemini-2.0-flash",
        "stream": True,
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    }
    upstream = build_upstream_request(channel, body, Settings(auth_secret_key="s"))
    assert "alt=sse" in upstream.url
    assert upstream.url.endswith(":streamGenerateContent?key=upstream-key&alt=sse")


def test_gemini_non_stream_request_keeps_plain_url() -> None:
    channel = _channel(ProtocolKind.GEMINI)
    body = {
        "model": "gemini-2.0-flash",
        "stream": False,
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    }
    upstream = build_upstream_request(channel, body, Settings(auth_secret_key="s"))
    assert "alt=sse" not in upstream.url
    assert upstream.url.endswith(":generateContent?key=upstream-key")
