from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from lens_api.api.routes import provider_proxy
from lens_api.core.protocol_reachability import CHAT_PROTOCOLS, can_reach_protocol
from lens_api.gateway.converters import (
    convert_request,
    convert_response,
    convert_stream_iterator,
)
from lens_api.gateway.converters.chat_to_bedrock import (
    bedrock_eventstream_to_sse,
    bedrock_sse_to_eventstream,
)
from lens_api.gateway.service.auth import get_current_gateway_key
from lens_api.models import ProtocolKind

P = ProtocolKind
PROTOCOLS = sorted(CHAT_PROTOCOLS, key=lambda p: p.value)


@pytest.mark.parametrize("source", PROTOCOLS)
@pytest.mark.parametrize("target", PROTOCOLS)
def test_extended_chat_request_matrix(source: P, target: P) -> None:
    chat = {
        "model": "model-a",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    native = convert_request(P.OPENAI_CHAT, source, chat)
    original = deepcopy(native)
    converted = convert_request(source, target, native, "model-b")
    assert converted["model"] == "model-b"
    restored = convert_request(target, P.OPENAI_CHAT, converted)
    assert any(
        m.get("role") == "user" and m.get("content") for m in restored["messages"]
    )
    assert native == original
    assert can_reach_protocol(target, source)


@pytest.mark.parametrize("source", PROTOCOLS)
@pytest.mark.parametrize("target", PROTOCOLS)
def test_extended_chat_response_matrix(source: P, target: P) -> None:
    chat = {
        "id": "resp-1",
        "model": "model-a",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    native = convert_response(
        source, P.OPENAI_CHAT, json.dumps(chat).encode(), "model-a"
    )
    converted = convert_response(target, source, native, "model-a")
    restored = json.loads(convert_response(P.OPENAI_CHAT, target, converted, "model-a"))
    content = restored["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            str(p.get("text") or "") for p in content if isinstance(p, dict)
        )
    assert content == "answer"
    assert restored["choices"][0]["finish_reason"] == "stop"


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    for position in range(0, len(content), 7):
        yield content[position : position + 7]


async def _collect(iterator: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in iterator])


@pytest.mark.asyncio
@pytest.mark.parametrize("source", PROTOCOLS)
@pytest.mark.parametrize("target", PROTOCOLS)
async def test_extended_chat_stream_matrix(source: P, target: P) -> None:
    frames = [
        {
            "model": "model-a",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "answer"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "model": "model-a",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]
    raw = (
        b"".join(b"data: " + json.dumps(f).encode() + b"\n\n" for f in frames)
        + b"data: [DONE]\n\n"
    )
    native = await _collect(
        convert_stream_iterator(source, P.OPENAI_CHAT, _chunks(raw), "model-a")
    )
    converted = await _collect(
        convert_stream_iterator(target, source, _chunks(native), "model-a")
    )
    restored = await _collect(
        convert_stream_iterator(P.OPENAI_CHAT, target, _chunks(converted), "model-a")
    )
    payloads = [
        json.loads(line[6:])
        for line in restored.decode().splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert (
        "".join(
            c.get("delta", {}).get("content") or ""
            for f in payloads
            for c in f.get("choices", [])
        )
        == "answer"
    )
    assert any(
        c.get("finish_reason") == "stop" for f in payloads for c in f.get("choices", [])
    )


@pytest.mark.asyncio
async def test_bedrock_binary_frames_roundtrip_and_crc() -> None:
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "answer"}}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    sse = b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
    binary = await _collect(bedrock_sse_to_eventstream(_chunks(sse)))
    restored = await _collect(bedrock_eventstream_to_sse(_chunks(binary)))
    assert [
        json.loads(l[6:])
        for l in restored.decode().splitlines()
        if l.startswith("data: ")
    ] == events
    corrupt = bytearray(binary)
    corrupt[-1] ^= 1
    with pytest.raises(ValueError):
        await _collect(bedrock_eventstream_to_sse(_chunks(bytes(corrupt))))


def test_native_provider_routes_forward_protocol_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request
    from starlette.responses import Response

    async def capture(request: Request, gateway_key: object, **kwargs: Any) -> Response:
        from starlette.responses import JSONResponse

        payload = await request.json()
        return JSONResponse(
            {
                "protocol": kwargs["protocol"].value,
                "stream": kwargs["stream"],
                "model": request.path_params.get("model_name")
                or request.path_params.get("deployment")
                or payload.get("model"),
                "operation": kwargs["path_suffix"],
            }
        )

    app = FastAPI()
    provider_proxy.register(app, SimpleNamespace(proxy_provider_request=capture))
    app.dependency_overrides[get_current_gateway_key] = lambda: object()
    from fastapi.testclient import TestClient

    cases = [
        ("/mistral/v1/chat/completions", P.MISTRAL_CONVERSATIONS, {"model": "m"}),
        ("/openai/v1/responses", P.AZURE_OPENAI_RESPONSES, {"model": "m"}),
        ("/backend-api/codex/responses", P.OPENAI_CODEX_RESPONSES, {"model": "m"}),
        (
            "/v1/projects/project/locations/global/publishers/google/models/m:generateContent",
            P.GOOGLE_VERTEX,
            {},
        ),
        ("/model/m/converse-stream", P.BEDROCK_CONVERSE_STREAM, {}),
        ("/messages", P.PI_MESSAGES, {"model": "m"}),
        ("/openrouter/v1/chat/completions", P.OPENROUTER_IMAGES, {"model": "m"}),
        ("/systemone", P.TYPESAFE_SYSTEM_ONE, {"model": "m"}),
        ("/accounts/a/ai/run", P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE, {"model": "m"}),
        ("/llama/m/tokenize", P.LLAMA_CPP_CLASSIFY, {}),
        ("/v1/decisions", P.OPENAI_DECISIONS, {"model": "m"}),
    ]
    with TestClient(app) as client:
        for path, protocol, body in cases:
            result = client.post(path, json=body)
            assert result.status_code == 200, result.text
            assert result.json()["protocol"] == protocol.value
            assert result.json()["model"] == "m"


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("stream", [False, True])
async def test_extended_protocol_http_boundary(
    protocol: P, stream: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from lens_api.models import ChannelConfig, ChannelKeyItem
    from lens_api.gateway.upstreams import build_upstream_request
    from lens_api.gateway.service import proxy_upstream as proxy
    from lens_api.gateway.service.runtime_context import _RequestDeadline

    body = convert_request(
        P.OPENAI_CHAT,
        protocol,
        {
            "model": "model-a",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": stream,
        },
    )
    channel = ChannelConfig(
        id="test",
        name="test",
        protocol=protocol,
        base_url=(
            "https://bedrock-runtime.us-east-1.amazonaws.com"
            if protocol == P.BEDROCK_CONVERSE_STREAM
            else "https://provider.example"
        ),
        api_key="test-key",
        keys=[ChannelKeyItem(id="key", key="test-key")],
        headers={"chatgpt-account-id": "test-account"},
    )
    upstream = build_upstream_request(channel, body, proxy.settings)
    serialized = json.dumps(upstream.json_body).encode()
    if protocol == P.BEDROCK_CONVERSE_STREAM:
        assert "modelId" not in upstream.json_body
        assert "model" not in upstream.json_body
        assert "stream" not in upstream.json_body
    response_chat = {
        "id": "resp-1",
        "model": "model-a",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    stream_response = stream or protocol in {P.PI_MESSAGES, P.OPENAI_CODEX_RESPONSES}
    if stream_response:
        frames = [
            {
                "model": "model-a",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "answer"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "model": "model-a",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": response_chat["usage"],
            },
        ]
        raw = (
            b"".join(b"data: " + json.dumps(f).encode() + b"\n\n" for f in frames)
            + b"data: [DONE]\n\n"
        )
        native = await _collect(
            convert_stream_iterator(protocol, P.OPENAI_CHAT, _chunks(raw), "model-a")
        )
        media = "text/event-stream"
        if protocol == P.BEDROCK_CONVERSE_STREAM:
            native = await _collect(bedrock_sse_to_eventstream(_chunks(native)))
            media = "application/vnd.amazon.eventstream"
    else:
        native = convert_response(
            protocol, P.OPENAI_CHAT, json.dumps(response_chat).encode(), "model-a"
        )
        media = "application/json"
    observed = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, headers={"content-type": media}, content=native)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        monkeypatch.setattr(proxy, "_resolve_http_client", lambda _: (client, False))
        monkeypatch.setattr(
            proxy, "_safe_estimate_cost", AsyncMock(return_value=(0, 0, 0))
        )
        monkeypatch.setattr(
            proxy.app_state.router, "record_success", lambda *a, **kw: None
        )
        result = await proxy._call_channel(
            channel,
            body,
            upstream,
            serialized,
            None,
            _RequestDeadline(
                started_at=time.time(),
                first_token_timeout_seconds=30,
                stream_idle_timeout_seconds=30,
            ),
            credential_id=None,
            probe_owner=None,
            client_protocol=P.OPENAI_CHAT,
        )
        if stream:
            output = await _collect(result.response.body_iterator)
            assert b"answer" in output
            assert result.is_stream
        else:
            output = json.loads(result.response.body)
            assert output["choices"][0]["message"]["content"] == "answer"
            assert result.total_tokens == 5
            assert not result.is_stream
    assert len(observed) == 1
    assert observed[0].content == serialized
    assert observed[0].url == httpx.URL(upstream.url)


@pytest.mark.parametrize("protocol", [P.PI_MESSAGES, P.BEDROCK_CONVERSE_STREAM])
def test_provider_stream_requires_terminal_when_body_capture_disabled(
    protocol: P,
) -> None:
    from lens_api.gateway.service.runtime_context import StreamCapture
    from lens_api.gateway.service.usage import _describe_stream_capture_issue
    from lens_api.gateway.service.stream_logging import _record_stream_event_payload

    capture = StreamCapture(capture_body=False)
    capture.completed = True
    capture.saw_first_chunk = True
    assert "terminal" in _describe_stream_capture_issue(protocol, capture, None)
    terminal = (
        {"type": "done", "reason": "stop"}
        if protocol == P.PI_MESSAGES
        else {"messageStop": {"stopReason": "end_turn"}}
    )
    _record_stream_event_payload(protocol, capture, terminal, 0)
    assert _describe_stream_capture_issue(protocol, capture, None) is None


def test_image_conversion_uses_native_media_fields_and_errors() -> None:
    chat_image = {
        "id": "img-1",
        "model": "m",
        "choices": [
            {
                "message": {
                    "content": "",
                    "images": [
                        {"image_url": {"url": "data:image/png;base64,aGVsbG8="}}
                    ],
                }
            }
        ],
    }
    converted = json.loads(
        convert_response(
            P.OPENAI_IMAGE, P.OPENROUTER_IMAGES, json.dumps(chat_image).encode()
        )
    )
    assert converted["data"] == [{"b64_json": "aGVsbG8="}]
    restored = json.loads(
        convert_response(
            P.OPENROUTER_IMAGES, P.OPENAI_IMAGE, json.dumps(converted).encode(), "m"
        )
    )
    assert (
        restored["choices"][0]["message"]["images"][0]["image_url"]["url"]
        == "data:image/png;base64,aGVsbG8="
    )
    request = convert_request(
        P.OPENAI_IMAGE,
        P.OPENROUTER_IMAGES,
        {"model": "m", "prompt": "draw", "n": 1},
        "upstream",
    )
    assert request["model"] == "upstream" and request["stream"] is False
    assert request["messages"] == [{"role": "user", "content": "draw"}]
    with pytest.raises(ValueError, match="size"):
        convert_request(
            P.OPENAI_IMAGE,
            P.OPENROUTER_IMAGES,
            {"model": "m", "prompt": "draw", "size": "1024x1024"},
        )
    with pytest.raises(ValueError, match="Image edits"):
        convert_request(
            P.OPENAI_IMAGE,
            P.OPENROUTER_IMAGES,
            {"model": "m", "prompt": "draw", "image": "x"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,query", [({"api-key": "gateway"}, ""), ({}, "key=gateway")]
)
async def test_native_provider_authentication(
    headers: dict[str, str], query: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.requests import Request
    from lens_api.gateway.service import auth
    from lens_api.models import GatewayApiKey

    gateway = GatewayApiKey(
        id="key",
        api_key="gateway",
        enabled=True,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    lookup = AsyncMock(return_value=gateway)
    monkeypatch.setattr(
        auth.app_state.gateway_api_key_repo, "get_gateway_api_key_by_secret", lookup
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": query.encode(),
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
    )
    assert await auth.get_current_gateway_key(request) == gateway
    lookup.assert_awaited_once_with("gateway")


@pytest.mark.parametrize("protocol", [P.PI_MESSAGES, P.BEDROCK_CONVERSE_STREAM])
def test_provider_nonstream_restore_rejects_incomplete_tool_success(
    protocol: P,
) -> None:
    from lens_api.gateway.service.stream_restore import _distill_stream_response_content

    events = (
        [
            {
                "type": "toolcall_start",
                "contentIndex": 0,
                "id": "c",
                "toolName": "lookup",
            },
            {"type": "toolcall_delta", "contentIndex": 0, "delta": "{bad"},
            {"type": "done", "reason": "toolUse"},
        ]
        if protocol == P.PI_MESSAGES
        else [
            {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "c", "name": "lookup"}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": "{}"}},
                }
            },
            {"messageStop": {"stopReason": "tool_use"}},
        ]
    )
    raw = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    with pytest.raises(ValueError):
        _distill_stream_response_content(protocol, raw)


@pytest.mark.asyncio
async def test_provider_stream_corruption_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    from lens_api.gateway.service.provider_streams import normalize_provider_stream
    from lens_api.gateway.service.stream_logging import _stream_upstream_iterator
    from lens_api.gateway.service.runtime_context import StreamCapture, _RequestDeadline

    binary = await _collect(
        bedrock_sse_to_eventstream(
            _chunks(b'data: {"messageStop":{"stopReason":"end_turn"}}\n\n')
        )
    )
    corrupt = bytearray(binary)
    corrupt[-1] ^= 1
    response = normalize_provider_stream(
        httpx.Response(
            200,
            headers={"content-type": "application/vnd.amazon.eventstream"},
            content=bytes(corrupt),
            request=httpx.Request("POST", "https://provider.example"),
        ),
        P.BEDROCK_CONVERSE_STREAM,
    )
    capture = StreamCapture(
        capture_body=False,
        upstream_response=response,
        deadline=_RequestDeadline(
            started_at=time.time(),
            first_token_timeout_seconds=30,
            stream_idle_timeout_seconds=30,
        ),
    )
    with pytest.raises(ValueError):
        await _collect(
            _stream_upstream_iterator(response, P.BEDROCK_CONVERSE_STREAM, capture, 0)
        )
    assert capture.error_status_code == 502
    assert capture.errors and "Invalid upstream stream" in capture.errors[0]
    assert not capture.completed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,path_params,body,forced_stream,operation",
    [
        (P.MISTRAL_CONVERSATIONS, {}, {"model": "m", "messages": []}, None, None),
        (P.AZURE_OPENAI_RESPONSES, {"deployment": "m"}, {"input": "hello"}, None, None),
        (
            P.OPENAI_CODEX_RESPONSES,
            {},
            {"model": "m", "input": "hello", "stream": False},
            None,
            None,
        ),
        (P.GOOGLE_VERTEX, {"model_name": "m"}, {"contents": []}, True, None),
        (P.BEDROCK_CONVERSE_STREAM, {"model_name": "m"}, {"messages": []}, True, None),
        (P.PI_MESSAGES, {}, {"model": "m", "context": {"messages": []}}, True, None),
        (
            P.OPENROUTER_IMAGES,
            {},
            {"model": "m", "messages": [], "modalities": ["image"]},
            False,
            None,
        ),
        (
            P.TYPESAFE_SYSTEM_ONE,
            {},
            {"model": "m", "state": {}, "questions": {}},
            False,
            None,
        ),
        (
            P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
            {"account_id": "a"},
            {"model": "m", "input": {"state": {}, "questions": {}}},
            False,
            None,
        ),
        (
            P.LLAMA_CPP_CLASSIFY,
            {"model_name": "m"},
            {"content": "test"},
            False,
            "tokenize",
        ),
        (
            P.OPENAI_DECISIONS,
            {},
            {"model": "m", "input": {}, "questions": {}},
            False,
            None,
        ),
    ],
)
async def test_native_request_handler_preserves_wire_body(
    protocol: P,
    path_params: dict,
    body: dict,
    forced_stream: bool | None,
    operation: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lens_api.gateway.service import proxy_routes
    from starlette.requests import Request
    from starlette.responses import Response

    raw = json.dumps(body).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "path_params": path_params,
            "headers": [],
            "query_string": b"",
        },
        receive,
    )
    forward = AsyncMock(return_value=Response(status_code=200))
    monkeypatch.setattr(proxy_routes, "_proxy_protocol", forward)
    await proxy_routes.proxy_provider_request(
        request,
        object(),
        protocol=protocol,
        stream=forced_stream,
        path_suffix=operation,
    )
    actual_protocol, forwarded, *_ = forward.await_args.args
    assert actual_protocol == protocol
    assert forwarded["model"] == "m"
    expected = {**body, "model": "m"}
    if forced_stream is not None:
        expected["stream"] = forced_stream
    assert forwarded == expected
    assert forward.await_args.kwargs["path_suffix"] == operation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream_protocol", [P.OPENAI_CHAT, P.BEDROCK_CONVERSE_STREAM]
)
async def test_native_bedrock_stream_client_receives_binary_frames(
    upstream_protocol: P,
) -> None:
    import time
    from lens_api.models import ChannelConfig
    from lens_api.gateway.service.proxy_upstream import _build_stream_result
    from lens_api.gateway.service.runtime_context import _RequestDeadline

    frames = [
        {
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "answer"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]
    raw = (
        b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames)
        + b"data: [DONE]\n\n"
    )
    native = await _collect(
        convert_stream_iterator(upstream_protocol, P.OPENAI_CHAT, _chunks(raw), "m")
    )
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=native,
        request=httpx.Request("POST", "https://provider.example"),
    )
    channel = ChannelConfig(
        id="test",
        name="test",
        protocol=upstream_protocol,
        base_url="https://provider.example",
        api_key="key",
    )
    result = await _build_stream_result(
        response,
        channel,
        P.BEDROCK_CONVERSE_STREAM,
        {"model": "m", "stream": True},
        None,
        0,
        False,
        deadline=_RequestDeadline(
            started_at=time.time(),
            first_token_timeout_seconds=30,
            stream_idle_timeout_seconds=30,
        ),
    )
    assert (
        result.response.headers["content-type"] == "application/vnd.amazon.eventstream"
    )
    wire = await _collect(result.response.body_iterator)
    restored = await _collect(bedrock_eventstream_to_sse(_chunks(wire)))
    payloads = [
        json.loads(line[6:])
        for line in restored.decode().splitlines()
        if line.startswith("data: ")
    ]
    assert any(
        event.get("contentBlockDelta", {}).get("delta", {}).get("text") == "answer"
        for event in payloads
    )
    assert any(
        event.get("messageStop", {}).get("stopReason") == "end_turn"
        for event in payloads
    )


@pytest.mark.asyncio
async def test_nonstream_forced_sse_obeys_first_token_timeout() -> None:
    import asyncio
    import time
    from lens_api.models import ChannelConfig
    from lens_api.gateway.service.proxy_upstream import _build_sse_to_json_result
    from lens_api.gateway.service.runtime_context import (
        _RequestDeadline,
        UpstreamRequestError,
    )

    class DelayedStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            await asyncio.sleep(0.2)
            yield b'data: {"type":"done","reason":"stop"}\n\n'

        async def aclose(self) -> None:
            self.closed = True

    delayed = DelayedStream()
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=delayed,
        request=httpx.Request("POST", "https://provider.example"),
    )
    channel = ChannelConfig(
        id="test",
        name="test",
        protocol=P.PI_MESSAGES,
        base_url="https://provider.example",
        api_key="key",
    )
    deadline = _RequestDeadline(
        started_at=time.perf_counter(),
        first_token_timeout_seconds=0.01,
        stream_idle_timeout_seconds=0.01,
    )
    with pytest.raises(UpstreamRequestError) as caught:
        await _build_sse_to_json_result(
            response, channel, P.OPENAI_CHAT, "m", None, None, False, deadline=deadline
        )
    assert caught.value.status_code == 504
    assert delayed.closed


@pytest.mark.parametrize("target", [P.AZURE_OPENAI_RESPONSES, P.OPENAI_CODEX_RESPONSES])
def test_responses_variant_preserves_native_input_without_pivot(target: P) -> None:
    native = {
        "model": "m",
        "reasoning": {"effort": "medium", "summary": "auto"},
        "input": [
            {
                "type": "reasoning",
                "id": "rs_1",
                "encrypted_content": "opaque",
                "summary": [],
            },
            {
                "type": "configuration_update",
                "reasoning": {"effort": "xhigh"},
            },
            {
                "type": "function_call",
                "call_id": "c",
                "name": "f",
                "arguments": "{}",
                "namespace": "remote",
            },
        ],
        "tools": [
            {
                "type": "custom",
                "name": "run",
                "format": {
                    "type": "grammar",
                    "syntax": "lark",
                    "definition": "start: /./",
                },
            }
        ],
        "include": ["reasoning.encrypted_content"],
        "stream": False,
    }
    result = convert_request(P.OPENAI_RESPONSES, target, native, "deployment")
    assert result["input"] == native["input"]
    assert result["tools"] == native["tools"]
    assert result["include"] == native["include"]
    assert result["model"] == "deployment"
    assert result["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert native["reasoning"]["effort"] == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [P.PI_MESSAGES, P.BEDROCK_CONVERSE_STREAM])
async def test_new_chat_provider_can_serve_multimodal_helper(
    protocol: P, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lens_api.models import ChannelConfig, ChannelKeyItem
    from lens_api.gateway.service import multimodal_relay as relay

    key = (
        json.dumps(
            {
                "access_key_id": "AKIDEXAMPLE",
                "secret_access_key": "secret",
                "region": "us-east-1",
            }
        )
        if protocol == P.BEDROCK_CONVERSE_STREAM
        else "test-key"
    )
    channel = ChannelConfig(
        id="channel",
        name="channel",
        protocol=protocol,
        base_url="https://provider.example",
        api_key=key,
        keys=[ChannelKeyItem(id="key", key=key)],
    )
    target = SimpleNamespace(
        channel=channel,
        model_name="vision-model",
        credential_id="key",
        credential_name="key",
    )
    group = SimpleNamespace(name="helper", protocols=[protocol])
    monkeypatch.setattr(
        relay,
        "app_state",
        SimpleNamespace(
            group_repo=SimpleNamespace(get_group=AsyncMock(return_value=group))
        ),
    )
    monkeypatch.setattr(
        relay,
        "_resolve_routing_plan",
        AsyncMock(return_value=SimpleNamespace(route_targets=[target])),
    )
    seen: list[httpx.Request] = []
    if protocol == P.PI_MESSAGES:
        frames = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "description"},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        native = await _collect(
            convert_stream_iterator(
                protocol,
                P.OPENAI_CHAT,
                _chunks(
                    b"".join(
                        b"data: " + json.dumps(f).encode() + b"\n\n" for f in frames
                    )
                ),
                "vision-model",
            )
        )
        media = "text/event-stream"
    else:
        native = convert_response(
            protocol,
            P.OPENAI_CHAT,
            json.dumps(
                {
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "description"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode(),
            "vision-model",
        )
        media = "application/json"

    def receive(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": media}, content=native)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        monkeypatch.setattr(
            relay, "_resolve_http_client", lambda _proxy: (client, False)
        )
        answer = await relay._call_helper_group(
            channels=[channel],
            helper_group_id="helper",
            kind="image",
            block={
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aGVsbG8="},
            },
            protocol=P.OPENAI_CHAT,
            global_proxy_url=None,
            deadline=SimpleNamespace(remaining_seconds=lambda: 10),
            log_ctx=SimpleNamespace(request_id="r", attempts=[]),
        )
    assert answer == "description"
    assert len(seen) == 1
    if protocol == P.BEDROCK_CONVERSE_STREAM:
        assert seen[0].headers["authorization"].startswith("AWS4-HMAC-SHA256")
        assert seen[0].headers["x-amz-content-sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("client_protocol", [P.OPENAI_IMAGE, P.OPENROUTER_IMAGES])
@pytest.mark.parametrize("upstream_protocol", [P.OPENAI_IMAGE, P.OPENROUTER_IMAGES])
async def test_image_http_boundary(
    client_protocol: P, upstream_protocol: P, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time
    from lens_api.models import ChannelConfig, ChannelKeyItem
    from lens_api.gateway.upstreams import build_upstream_request
    from lens_api.gateway.service import proxy_upstream as proxy
    from lens_api.gateway.service.runtime_context import _RequestDeadline

    original = {"model": "m", "prompt": "draw", "n": 1}
    request_body = convert_request(P.OPENAI_IMAGE, client_protocol, original)
    outbound = convert_request(
        client_protocol, upstream_protocol, request_body, "upstream"
    )
    channel = ChannelConfig(
        id="channel",
        name="channel",
        protocol=upstream_protocol,
        base_url="https://provider.example",
        api_key="key",
        keys=[ChannelKeyItem(id="key", key="key")],
    )
    upstream = build_upstream_request(channel, outbound, proxy.settings)
    image = {
        "created": 1,
        "data": [{"b64_json": "aGVsbG8="}],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }
    native_response = convert_response(
        upstream_protocol, P.OPENAI_IMAGE, json.dumps(image).encode(), "upstream"
    )
    seen: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=native_response
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        monkeypatch.setattr(
            proxy, "_resolve_http_client", lambda _proxy: (client, False)
        )
        monkeypatch.setattr(
            proxy, "_safe_estimate_cost", AsyncMock(return_value=(0, 0, 0))
        )
        monkeypatch.setattr(
            proxy.app_state.router, "record_success", lambda *args, **kwargs: None
        )
        result = await proxy._call_channel(
            channel,
            outbound,
            upstream,
            json.dumps(upstream.json_body).encode(),
            None,
            _RequestDeadline(
                started_at=time.time(),
                first_token_timeout_seconds=30,
                stream_idle_timeout_seconds=30,
            ),
            credential_id=None,
            probe_owner=None,
            client_protocol=client_protocol,
        )
    final = json.loads(
        convert_response(P.OPENAI_IMAGE, client_protocol, result.response.body, "m")
    )
    assert final["data"] == [{"b64_json": "aGVsbG8="}]
    assert final["usage"]["input_tokens"] == 3
    assert final["usage"]["output_tokens"] == 2
    assert result.total_tokens == 5
    assert len(seen) == 1
    if upstream_protocol == P.OPENROUTER_IMAGES:
        assert seen[0].url.path == "/v1/chat/completions"
        assert json.loads(seen[0].content)["modalities"] == ["image", "text"]
    else:
        assert seen[0].url.path == "/v1/images/generations"


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [["llama-model"], ["one", "two"], []])
async def test_native_llama_routing_without_body_model(
    names: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from lens_api.gateway.service import proxy_routes
    from starlette.requests import Request
    from starlette.responses import Response
    from fastapi import HTTPException

    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"content":"sample"}',
            "more_body": False,
        }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/tokenize",
            "path_params": {},
            "headers": [],
            "query_string": b"",
        },
        receive,
    )
    monkeypatch.setattr(
        proxy_routes.app_state.group_repo, "list_groups", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(proxy_routes, "_filtered_group_names", lambda *args: names)
    forward = AsyncMock(return_value=Response(status_code=200))
    monkeypatch.setattr(proxy_routes, "_proxy_protocol", forward)
    if len(names) == 1:
        await proxy_routes.proxy_provider_request(
            request,
            object(),
            protocol=P.LLAMA_CPP_CLASSIFY,
            stream=False,
            path_suffix="tokenize",
        )
        assert forward.await_args.args[1]["model"] == "llama-model"
    else:
        with pytest.raises(HTTPException) as caught:
            await proxy_routes.proxy_provider_request(
                request,
                object(),
                protocol=P.LLAMA_CPP_CLASSIFY,
                stream=False,
                path_suffix="tokenize",
            )
        assert caught.value.status_code == 400
        forward.assert_not_awaited()


@pytest.mark.parametrize("target", [P.OPENAI_CHAT, P.MISTRAL_CONVERSATIONS])
def test_provider_metadata_cleanup_keeps_user_schema_keys(target: P) -> None:
    native = {
        "model": "m",
        "context": {
            "messages": [
                {
                    "role": "system",
                    "content": "system",
                    "timestamp": 0,
                    "toolsAdded": [
                        {
                            "name": "lookup",
                            "description": "lookup",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "_pi_value": {"type": "string"},
                                    "_bedrock_id": {"type": "string"},
                                },
                            },
                        }
                    ],
                },
                {"role": "user", "content": "hello", "timestamp": 0},
            ]
        },
        "options": {},
    }
    result = convert_request(P.PI_MESSAGES, target, native)
    assert result["tools"][0]["function"]["parameters"]["properties"] == {
        "_pi_value": {"type": "string"},
        "_bedrock_id": {"type": "string"},
    }
    assert not any(key.startswith(("_pi_", "_bedrock_")) for key in result)
    assert not any(
        key.startswith(("_pi_", "_bedrock_"))
        for message in result["messages"]
        for key in message
    )


@pytest.mark.asyncio
async def test_native_pi_utf8_chunks_preserve_text_in_capture() -> None:
    import time
    from lens_api.gateway.service.stream_logging import (
        _stream_upstream_iterator,
        _capture_converted_stream_iterator,
    )
    from lens_api.gateway.service.runtime_context import StreamCapture, _RequestDeadline
    from lens_api.gateway.service.stream_restore import _distill_stream_response_content

    events = [
        {"type": "text_start", "contentIndex": 0},
        {"type": "text_delta", "contentIndex": 0, "delta": "你好🌍"},
        {"type": "text_end", "contentIndex": 0, "content": "你好🌍"},
        {
            "type": "done",
            "reason": "stop",
            "usage": {"input": 1, "output": 2, "totalTokens": 3},
        },
    ]
    raw = b"".join(
        b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n"
        for event in events
    )

    class ByteStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            for byte in raw:
                yield bytes([byte])

    response = httpx.Response(
        200,
        stream=ByteStream(),
        request=httpx.Request("POST", "https://provider.example"),
    )
    capture = StreamCapture(
        capture_body=True,
        upstream_response=response,
        deadline=_RequestDeadline(
            started_at=time.perf_counter(),
            first_token_timeout_seconds=30,
            stream_idle_timeout_seconds=30,
        ),
    )
    output = await _collect(
        _capture_converted_stream_iterator(
            _stream_upstream_iterator(
                response, P.PI_MESSAGES, capture, time.perf_counter()
            ),
            capture,
        )
    )
    assert output == raw
    assert "".join(capture.response_content_chunks) == raw.decode()
    assert "".join(capture.client_response_content_chunks) == raw.decode()
    restored = json.loads(
        _distill_stream_response_content(
            P.PI_MESSAGES, "".join(capture.response_content_chunks)
        )
    )
    assert restored["content"][0]["text"] == "你好🌍"
    assert capture.saw_response_completed and capture.completed


@pytest.mark.parametrize("override,expected", [(None, "xhigh"), ("low", "low")])
def test_codex_wire_preparation_preserves_explicit_effort_override(
    override: str | None, expected: str
) -> None:
    from lens_api.models import ChannelConfig, ChannelKeyItem
    from lens_api.gateway.service.routing_plan import (
        _prepare_upstream_body,
        _apply_param_override,
    )
    from lens_api.gateway.service.proxy_upstream import _prepare_channel_request

    channel = ChannelConfig(
        id="c",
        name="c",
        protocol=P.OPENAI_CODEX_RESPONSES,
        base_url="https://provider.example",
        api_key="key",
        keys=[ChannelKeyItem(id="key", key="key")],
        headers={"chatgpt-account-id": "a"},
        param_override=(
            json.dumps({"reasoning": {"effort": override}}) if override else ""
        ),
    )
    original = {
        "model": "m",
        "input": [{"type": "configuration_update", "reasoning": {"effort": "xhigh"}}],
        "reasoning": {"effort": "medium", "summary": "auto"},
        "stream": False,
    }
    prepared = _prepare_upstream_body(P.OPENAI_CODEX_RESPONSES, original, "upstream")
    prepared = _apply_param_override(channel, prepared)
    upstream, serialized, _ = _prepare_channel_request(
        channel,
        prepared,
        credential_id="key",
        user_agent=None,
        forwarded_headers=None,
        model_group_headers=(),
        log_body_enabled=False,
    )
    assert json.loads(serialized)["reasoning"] == {
        "effort": expected,
        "summary": "auto",
    }
    assert upstream.json_body["input"] == original["input"]
    assert original["reasoning"]["effort"] == "medium"


def test_system_one_openrouter_base_retains_api_version_after_config_normalization() -> (
    None
):
    from lens_api.models import ChannelConfig, ChannelKeyItem
    from lens_api.gateway.upstreams import build_upstream_request
    from lens_api.gateway.service.runtime_context import settings

    channel = ChannelConfig(
        id="c",
        name="c",
        protocol=P.TYPESAFE_SYSTEM_ONE,
        base_url="https://openrouter.ai/api/v1",
        api_key="key",
        keys=[ChannelKeyItem(id="key", key="key")],
    )
    upstream = build_upstream_request(
        channel, {"model": "m", "state": {}, "questions": {}}, settings
    )
    assert upstream.url == "https://openrouter.ai/api/v1/systemone"


@pytest.mark.asyncio
async def test_native_mistral_thinking_and_object_tool_arguments_restore() -> None:
    import time
    from lens_api.models import ChannelConfig
    from lens_api.gateway.service.proxy_upstream import (
        _build_stream_result,
        _build_sse_to_json_result,
    )
    from lens_api.gateway.service.runtime_context import _RequestDeadline, StreamCapture
    from lens_api.gateway.service.stream_logging import _record_stream_event_payload

    events = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": [{"type": "text", "text": "private"}],
                                "signature": "signed",
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "answer",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "abc123DEF",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": {"x": 1}},
                            }
                        ],
                    },
                    "finish_reason": None,
                }
            ]
        },
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    ]
    raw = (
        b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        + b"data: [DONE]\n\n"
    )
    capture = StreamCapture(capture_body=False)
    _record_stream_event_payload(P.MISTRAL_CONVERSATIONS, capture, events[0], 0)
    assert capture.saw_first_chunk
    channel = ChannelConfig(
        id="c",
        name="c",
        protocol=P.MISTRAL_CONVERSATIONS,
        base_url="https://provider.example",
        api_key="key",
    )

    def response() -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=raw,
            request=httpx.Request("POST", "https://provider.example"),
        )

    result = await _build_stream_result(
        response(),
        channel,
        P.MISTRAL_CONVERSATIONS,
        {"model": "m", "stream": True},
        None,
        0,
        False,
        deadline=_RequestDeadline(
            started_at=time.perf_counter(),
            first_token_timeout_seconds=30,
            stream_idle_timeout_seconds=30,
        ),
    )
    assert await _collect(result.response.body_iterator) == raw
    restored = await _build_sse_to_json_result(
        response(), channel, P.OPENAI_CHAT, "m", None, None, False
    )
    message = json.loads(restored.response.body)["choices"][0]["message"]
    assert message["content"] == "answer"
    assert message["reasoning_content"] == "private"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"x":1}'


def test_mistral_request_metadata_does_not_leak_to_chat_wire() -> None:
    native = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": [{"type": "text", "text": "private"}],
                    },
                    {"type": "text", "text": "answer"},
                ],
            }
        ],
    }
    result = convert_request(P.MISTRAL_CONVERSATIONS, P.OPENAI_CHAT, native)
    assert result["messages"][0]["content"] == "answer"
    assert "_mistral_content" not in result["messages"][0]


@pytest.mark.parametrize(
    "payload",
    [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "private"}}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"text": "private"}},
            }
        },
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": "private"}},
            }
        },
        {
            "type": "toolcall_end",
            "toolCall": {
                "id": "call-a",
                "name": "lookup",
                "arguments": {"q": "private"},
            },
        },
        {"type": "text_end", "content": "private"},
    ],
)
def test_new_provider_log_output_filter_masks_native_content(
    payload: dict[str, object],
) -> None:
    from lens_api.gateway.service.payload_serialization import _dump_log_json

    masked = _dump_log_json(payload, log_output=False)
    assert masked is not None and "private" not in masked
    if "toolCall" in payload:
        assert "lookup" in masked and "call-a" in masked
    assert "private" in (_dump_log_json(payload) or "")


def test_new_provider_input_and_tool_log_filters() -> None:
    from lens_api.gateway.service.payload_serialization import _dump_log_json

    payload = {
        "context": {
            "messages": [{"role": "user", "content": "private-input"}],
            "tools": [{"name": "private-tool"}],
        },
        "state": {"text": "private-state"},
        "assets": {"image": "private-image"},
        "questions": {"private-question": {}},
    }
    filtered = _dump_log_json(payload, log_input=False, log_tools=False)
    assert filtered is not None and "private" not in filtered
    tools_only = _dump_log_json(payload, log_tools=False)
    assert (
        tools_only is not None
        and "private-tool" not in tools_only
        and "private-question" not in tools_only
    )
    assert "private-input" in tools_only


@pytest.mark.parametrize(
    "ending",
    [
        {
            "type": "toolCall",
            "id": "c",
            "name": "lookup",
            "arguments": {"x": 1},
            "argumentsError": "invalid provider arguments",
        },
        {"type": "toolCall", "id": "changed", "name": "lookup", "arguments": {"x": 1}},
        {"type": "toolCall", "id": "c", "name": "lookup", "arguments": {"x": 2}},
        {"type": "toolCall", "id": "c", "name": "lookup", "arguments": []},
    ],
)
def test_pi_nonstream_restore_rejects_invalid_tool_end(
    ending: dict[str, object],
) -> None:
    from lens_api.gateway.service.provider_stream_restore import restore_pi_events

    with pytest.raises(ValueError):
        restore_pi_events(
            [
                {
                    "type": "toolcall_start",
                    "contentIndex": 0,
                    "id": "c",
                    "toolName": "lookup",
                },
                {"type": "toolcall_delta", "contentIndex": 0, "delta": '{"x":1}'},
                {"type": "toolcall_end", "contentIndex": 0, "toolCall": ending},
                {"type": "done", "reason": "toolUse", "usage": {}},
            ]
        )


@pytest.mark.parametrize(
    "protocol,usage",
    [
        (P.PI_MESSAGES, {"input": 3, "output": 2, "cacheRead": 5, "cacheWrite": 7}),
        (
            P.BEDROCK_CONVERSE_STREAM,
            {
                "inputTokens": 3,
                "outputTokens": 2,
                "cacheReadInputTokens": 5,
                "cacheWriteInputTokens": 7,
            },
        ),
    ],
)
def test_native_provider_cache_usage_uses_total_input_for_pricing(
    protocol: P, usage: dict[str, int]
) -> None:
    from lens_api.gateway.service.usage import _extract_usage_from_payload

    parsed = _extract_usage_from_payload(protocol, {"usage": usage})
    assert parsed["input_tokens"] == 15
    assert parsed["cache_read_input_tokens"] == 5
    assert parsed["cache_write_input_tokens"] == 7
    assert parsed["output_tokens"] == 2
    assert parsed["total_tokens"] == 17
    assert (
        _extract_usage_from_payload(protocol, {"usage": {**usage, "totalTokens": 0}})[
            "total_tokens"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"tokens_evaluated": 3, "tokens_predicted": 1}, (3, 1, 4)),
        ({"usage": None, "tokens_evaluated": 3, "tokens_predicted": 1}, (3, 1, 4)),
        ({"tokens_evaluated": 0, "tokens_predicted": 0}, (0, 0, 0)),
        ({"usage": {}, "tokens_evaluated": -0.5, "tokens_predicted": 3.9}, (0, 0, 0)),
        (
            {"usage": {"input_tokens": 7, "output_tokens": 2}, "tokens_evaluated": 3},
            (7, 2, 9),
        ),
        ({"tokens": [1, 2]}, (0, 0, 0)),
    ],
)
def test_llama_native_completion_response_usage(
    payload: dict[str, object], expected: tuple[int, int, int]
) -> None:
    from lens_api.gateway.service.usage import _extract_response_usage

    parsed = _extract_response_usage(
        P.LLAMA_CPP_CLASSIFY, httpx.Response(200, json=payload)
    )
    assert (
        tuple(parsed[key] for key in ("input_tokens", "output_tokens", "total_tokens"))
        == expected
    )


@pytest.mark.parametrize("field", ["tokens_evaluated", "tokens_predicted"])
@pytest.mark.parametrize("value", [True, -1, "invalid", 3.9, -0.5, 3.0, "3", None])
def test_llama_native_completion_rejects_invalid_usage(
    field: str, value: object
) -> None:
    from lens_api.gateway.service.usage import _extract_response_usage

    with pytest.raises(ValueError):
        _extract_response_usage(
            P.LLAMA_CPP_CLASSIFY,
            httpx.Response(
                200, json={"tokens_evaluated": 3, "tokens_predicted": 1, field: value}
            ),
        )


def test_pi_transcript_tool_log_filter_uses_native_fields() -> None:
    from lens_api.gateway.service.payload_serialization import _dump_log_json

    payload = {
        "context": {
            "messages": [
                {
                    "role": "system",
                    "content": "visible",
                    "toolsAdded": [
                        {"name": "private-tool", "parameters": {"private-schema": True}}
                    ],
                    "toolsRemoved": [{"name": "private-removed"}],
                }
            ]
        },
        "options": {
            "toolChoice": {"type": "function", "function": {"name": "private-choice"}}
        },
    }
    result = _dump_log_json(payload, log_tools=False)
    assert result is not None and "private" not in result and "visible" in result


def test_bedrock_redacted_chunks_restore_concatenated_bytes() -> None:
    from lens_api.gateway.service.provider_stream_restore import restore_bedrock_events

    result = restore_bedrock_events(
        [
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"redactedContent": "YQ=="}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"reasoningContent": {"redactedContent": "Yg=="}},
                }
            },
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"messageStop": {"stopReason": "end_turn"}},
        ]
    )
    assert result is not None
    assert (
        result["output"]["message"]["content"][0]["reasoningContent"]["redactedContent"]
        == "YWI="
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"x":"a\nb"}', {"x": "a\nb"}),
        ('{"path":"C:\\q"}', {"path": "C:\\q"}),
    ],
)
def test_pi_tool_restore_matches_pinned_json_repairs(
    raw: str, expected: dict[str, str]
) -> None:
    from lens_api.gateway.service.provider_stream_restore import restore_pi_events

    result = restore_pi_events(
        [
            {
                "type": "toolcall_start",
                "contentIndex": 0,
                "id": "c",
                "toolName": "lookup",
            },
            {"type": "toolcall_delta", "contentIndex": 0, "delta": raw},
            {
                "type": "toolcall_end",
                "contentIndex": 0,
                "toolCall": {
                    "type": "toolCall",
                    "id": "c",
                    "name": "lookup",
                    "arguments": expected,
                },
            },
            {"type": "done", "reason": "toolUse", "usage": {}},
        ]
    )
    assert result is not None and result["content"][0]["arguments"] == expected


def test_pi_length_restore_preserves_truncated_tool_text() -> None:
    from lens_api.gateway.service.provider_stream_restore import restore_pi_events
    from lens_api.gateway.converters.chat_to_pi import pi_response_to_chat

    result = restore_pi_events(
        [
            {
                "type": "toolcall_start",
                "contentIndex": 0,
                "id": "c",
                "toolName": "lookup",
            },
            {"type": "toolcall_delta", "contentIndex": 0, "delta": '{"x":'},
            {"type": "done", "reason": "length", "usage": {}},
        ]
    )
    assert result is not None and result["stopReason"] == "length"
    chat = pi_response_to_chat(result)
    assert chat["choices"][0]["finish_reason"] == "length"
    assert (
        chat["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        == '{"x":'
    )


def test_pi_tool_comparison_distinguishes_json_bool_from_number() -> None:
    from lens_api.gateway.service.provider_stream_restore import restore_pi_events

    with pytest.raises(ValueError, match="disagrees"):
        restore_pi_events(
            [
                {
                    "type": "toolcall_start",
                    "contentIndex": 0,
                    "id": "c",
                    "toolName": "lookup",
                },
                {
                    "type": "toolcall_delta",
                    "contentIndex": 0,
                    "delta": '{"nested":[1]}',
                },
                {
                    "type": "toolcall_end",
                    "contentIndex": 0,
                    "toolCall": {
                        "type": "toolCall",
                        "id": "c",
                        "name": "lookup",
                        "arguments": {"nested": [True]},
                    },
                },
                {"type": "done", "reason": "toolUse", "usage": {}},
            ]
        )


@pytest.mark.asyncio
async def test_pi_stream_emits_repaired_complete_tool_arguments() -> None:
    from lens_api.gateway.converters.chat_to_pi import pi_stream_to_chat_stream

    events = [
        {"type": "start"},
        {"type": "toolcall_start", "contentIndex": 0, "id": "c", "toolName": "lookup"},
        {"type": "toolcall_delta", "contentIndex": 0, "delta": '{"x":"a\nb"}'},
        {
            "type": "toolcall_end",
            "contentIndex": 0,
            "toolCall": {
                "type": "toolCall",
                "id": "c",
                "name": "lookup",
                "arguments": {"x": "a\nb"},
            },
        },
        {"type": "done", "reason": "toolUse", "usage": {}},
    ]
    raw = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
    chunks = []
    async for chunk in pi_stream_to_chat_stream(_chunks(raw)):
        chunks.append(chunk)
    payloads = [
        json.loads(frame[6:])
        for frame in b"".join(chunks).split(b"\n\n")
        if frame.startswith(b"data: {")
    ]
    arguments = "".join(
        call.get("function", {}).get("arguments", "")
        for payload in payloads
        for choice in payload.get("choices", [])
        for call in choice.get("delta", {}).get("tool_calls", [])
    )
    assert json.loads(arguments) == {"x": "a\nb"}
