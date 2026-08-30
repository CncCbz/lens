from __future__ import annotations

import asyncio
from email.utils import formatdate
import json
import time
from collections.abc import AsyncIterator

import httpx
from starlette.background import BackgroundTask

from lens_api.gateway.converters.chat_to_anthropic import (
    anthropic_stream_to_chat_stream,
    chat_stream_to_anthropic_stream,
)
from lens_api.gateway.converters.chat_to_gemini import gemini_stream_to_chat_stream
from lens_api.gateway.converters.chat_to_responses import (
    responses_stream_to_chat_stream,
)
from lens_api.gateway.router import (
    GatewayRouter,
    RouteTarget,
    decide_route_error,
    parse_retry_after_seconds,
)
from lens_api.gateway.service.proxy_routes import _build_gemini_models_payload
from lens_api.gateway.service.proxy_upstream import (
    _GatewayStreamingResponse,
    _stream_client_iterator,
)
from lens_api.gateway.service.routing_plan import _prepare_upstream_body
from lens_api.gateway.service.runtime_context import StreamCapture
from lens_api.gateway.service.site_model_probe import (
    _apply_site_model_probe_param_override,
    _build_site_model_probe_upstream_request,
    _site_model_probe_body,
    _site_model_probe_channel,
    _site_model_probe_stream_output_text,
)
from lens_api.models import (
    ChannelConfig,
    ChannelProxyMode,
    GatewayApiKey,
    ModelGroup,
    ModelGroupItem,
    ProtocolKind,
    RoutingStrategy,
    SiteModelTestCredential,
    SiteModelTestRequest,
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


def _site_model_test_request(
    *,
    protocol: ProtocolKind = ProtocolKind.OPENAI_CHAT,
    headers: dict[str, str] | None = None,
    model_name: str = "gpt-test",
    param_override: str = "",
) -> SiteModelTestRequest:
    return SiteModelTestRequest(
        protocol=protocol,
        base_url="https://example.com",
        headers=headers or {},
        proxy_mode=ChannelProxyMode.INHERIT,
        channel_proxy="",
        param_override=param_override,
        credential=SiteModelTestCredential(
            id="cred-1",
            name="Key 1",
            api_key="sk-test",
        ),
        model_name=model_name,
        prompt="hello",
    )


def _gateway_key() -> GatewayApiKey:
    return GatewayApiKey(
        id="key-1",
        api_key="sk-test",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


def _router_channel(channel_id: str, name: str) -> ChannelConfig:
    return ChannelConfig(
        id=channel_id,
        name=name,
        protocol=ProtocolKind.OPENAI_CHAT,
        base_url="https://example.com",
        api_key="sk-test",
    )


def _model_names(payload: dict[str, object]) -> list[str]:
    models = payload.get("models")
    assert isinstance(models, list)
    return [item["baseModelId"] for item in models if isinstance(item, dict)]


async def _collect(iterator: AsyncIterator[bytes]) -> bytes:
    chunks: list[bytes] = []
    async for chunk in iterator:
        chunks.append(chunk)
    return b"".join(chunks)


def _sse(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def test_route_error_decisions_cover_gateway_defaults() -> None:
    cases = {
        400: (False, False, True, True),
        401: (True, True, True, False),
        403: (True, True, True, False),
        404: (False, False, True, True),
        429: (True, True, False, False),
        500: (True, True, False, False),
        502: (True, True, False, False),
        503: (True, True, False, False),
        504: (True, True, False, False),
    }

    for status_code, expected in cases.items():
        decision = decide_route_error(status_code)
        assert (
            decision.retryable,
            decision.cooldown_candidate,
            decision.user_actionable,
            decision.skip_retry,
        ) == expected

    timeout_decision = decide_route_error(None, timeout=True)
    assert timeout_decision.retryable is True
    assert timeout_decision.cooldown_candidate is True

    body_too_large = decide_route_error(413, body_too_large=True)
    assert body_too_large.retryable is False
    assert body_too_large.cooldown_candidate is False
    assert body_too_large.skip_retry is True


def test_retry_after_parser_supports_seconds_http_date_and_milliseconds() -> None:
    assert parse_retry_after_seconds({"retry-after": "2"}) == 2
    assert parse_retry_after_seconds({"retry-after-ms": "1500"}) == 1.5
    assert parse_retry_after_seconds({"x-ms-retry-after-ms": "2500"}) == 2.5

    future = time.time() + 30
    parsed = parse_retry_after_seconds({"retry-after": formatdate(future, usegmt=True)})
    assert parsed is not None
    assert 0 < parsed <= 30


def test_router_select_can_preview_without_advancing_round_robin_cursor() -> None:
    router = GatewayRouter()
    channels = [_router_channel("channel-a", "A"), _router_channel("channel-b", "B")]

    first = router.select(channels, ProtocolKind.OPENAI_CHAT)
    preview = router.select(channels, ProtocolKind.OPENAI_CHAT, mutate=False)
    second = router.select(channels, ProtocolKind.OPENAI_CHAT)

    assert first.primary.channel.id == "channel-a"
    assert preview.primary.channel.id == "channel-b"
    assert second.primary.channel.id == "channel-b"


def test_router_failure_rate_updates_window_without_opening() -> None:
    router = GatewayRouter(
        circuit_minimum_requests=3,
        circuit_failure_rate_threshold=0.6,
    )
    channel = _router_channel("channel-a", "A")

    router.record_success(channel.id)
    router.record_failure(
        channel.id,
        "server error",
        status_code=500,
        threshold=99,
        cooldown_seconds=60,
        max_cooldown_seconds=120,
    )
    assert router.is_target_available(RouteTarget(channel)) is True

    router.record_failure(
        channel.id,
        "server error",
        status_code=500,
        threshold=99,
        cooldown_seconds=60,
        max_cooldown_seconds=120,
    )
    snapshot = router.snapshot([channel])
    health = snapshot.health[0]
    assert health.state == "available"
    assert health.window_request_count == 3
    assert health.failure_rate >= 0.6
    assert health.cooldown_remaining_seconds == 0
    assert router.is_target_available(RouteTarget(channel)) is True


def test_gemini_model_list_allows_enabled_convertible_route_item() -> None:
    execution_group = ModelGroup(
        id="exec",
        name="real-model",
        protocols=[ProtocolKind.OPENAI_CHAT, ProtocolKind.GEMINI],
        strategy=RoutingStrategy.ROUND_ROBIN,
        items=[
            ModelGroupItem(
                channel_id="channel-openai",
                protocol=ProtocolKind.OPENAI_CHAT,
                credential_id="cred-1",
                model_name="real-model",
                enabled=True,
            ),
            ModelGroupItem(
                channel_id="channel-gemini",
                protocol=ProtocolKind.GEMINI,
                credential_id="cred-1",
                model_name="real-model",
                enabled=False,
            ),
        ],
    )
    route_group = ModelGroup(
        id="route",
        name="alias-model",
        protocols=[ProtocolKind.GEMINI],
        strategy=RoutingStrategy.ROUND_ROBIN,
        route_group_id=execution_group.id,
    )

    payload = _build_gemini_models_payload(
        [execution_group, route_group],
        _gateway_key(),
    )

    assert _model_names(payload) == ["alias-model", "real-model"]


def test_gemini_model_list_rejects_non_chat_route_item() -> None:
    execution_group = ModelGroup(
        id="exec",
        name="real-model",
        protocols=[ProtocolKind.OPENAI_EMBEDDING, ProtocolKind.GEMINI],
        strategy=RoutingStrategy.ROUND_ROBIN,
        items=[
            ModelGroupItem(
                channel_id="channel-embedding",
                protocol=ProtocolKind.OPENAI_EMBEDDING,
                credential_id="cred-1",
                model_name="real-model",
                enabled=True,
            )
        ],
    )
    route_group = ModelGroup(
        id="route",
        name="alias-model",
        protocols=[ProtocolKind.GEMINI],
        strategy=RoutingStrategy.ROUND_ROBIN,
        route_group_id=execution_group.id,
    )

    payload = _build_gemini_models_payload(
        [execution_group, route_group],
        _gateway_key(),
    )

    assert payload["models"] == []


def test_site_model_probe_defaults_chat_test_to_stream() -> None:
    payload = _site_model_test_request()
    body = _site_model_probe_body(payload)
    prepared = _apply_site_model_probe_param_override(
        _site_model_probe_channel(payload), body, payload
    )

    assert body["stream"] is True
    assert isinstance(prepared, dict)
    assert prepared["stream"] is True


def test_site_model_probe_uses_configured_headers_only() -> None:
    payload = _site_model_test_request()
    body = _site_model_probe_body(payload)
    default_upstream = _build_site_model_probe_upstream_request(
        channel=_site_model_probe_channel(payload),
        body=body,
        credential_id=payload.credential.id,
    )
    rule_upstream = _build_site_model_probe_upstream_request(
        channel=_site_model_probe_channel(payload),
        body=body,
        credential_id=payload.credential.id,
    )
    channel_payload = _site_model_test_request(
        headers={"User-Agent": "channel-ua", "X-Channel": "1"}
    )
    channel_upstream = _build_site_model_probe_upstream_request(
        channel=_site_model_probe_channel(channel_payload),
        body=body,
        credential_id=channel_payload.credential.id,
    )

    assert "User-Agent" not in default_upstream.headers
    assert "Originator" not in default_upstream.headers
    assert "User-Agent" not in rule_upstream.headers
    assert "X-Global" not in rule_upstream.headers
    assert "X-Rule" not in rule_upstream.headers
    assert channel_upstream.headers["User-Agent"] == "channel-ua"
    assert channel_upstream.headers["X-Channel"] == "1"
    assert "X-Global" not in channel_upstream.headers


def test_site_model_probe_stream_output_text_supports_common_streams() -> None:
    assert (
        _site_model_probe_stream_output_text(
            ProtocolKind.OPENAI_RESPONSES,
            'data: {"type":"response.output_text.delta","delta":"ok"}\n\n',
        )
        == "ok"
    )
    assert (
        _site_model_probe_stream_output_text(
            ProtocolKind.ANTHROPIC,
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}\n\n',
        )
        == "ok"
    )
    assert (
        _site_model_probe_stream_output_text(
            ProtocolKind.GEMINI,
            '{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n',
        )
        == "ok"
    )


def test_openai_chat_prepare_normalizes_developer_role() -> None:
    prepared = _prepare_upstream_body(
        ProtocolKind.OPENAI_CHAT,
        {
            "model": "requested-model",
            "messages": [
                {"role": "developer", "content": "follow policy"},
                {"role": "user", "content": "hello"},
            ],
        },
        "upstream-model",
    )

    assert prepared["model"] == "upstream-model"
    assert prepared["messages"] == [
        {"role": "system", "content": "follow policy"},
        {"role": "user", "content": "hello"},
    ]


def test_openai_chat_prepare_inserts_missing_tool_result() -> None:
    prepared = _prepare_upstream_body(
        ProtocolKind.OPENAI_CHAT,
        {
            "model": "model",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "user", "content": "next"},
            ],
        },
        None,
    )

    assert prepared["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "No result provided"},
        {"role": "user", "content": "next"},
    ]


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
                        },
                    },
                }
            ),
        ]
    )

    output = asyncio.run(_collect(responses_stream_to_chat_stream(raw, "model")))

    assert b"data: [DONE]" in output
    assert raw.drained is True


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


def test_router_error_policy_resolver_merge_and_globals() -> None:
    from lens_api.models import (
        RouterErrorPolicyConfig,
        normalize_router_error_policy_config_json,
    )
    from lens_api.gateway.router import resolve_router_error_policy

    empty = normalize_router_error_policy_config_json("")
    assert empty == '{"overrides": {}}'

    p429 = resolve_router_error_policy("429")
    assert p429 is not None
    assert p429.cooldown_scope == "credential"
    assert p429.respect_retry_after is True

    p5xx = resolve_router_error_policy(
        "5xx",
        circuit_breaker_threshold=4,
        circuit_breaker_cooldown=90,
        circuit_breaker_max_cooldown=900,
    )
    assert p5xx is not None
    assert p5xx.failure_threshold == 4
    assert p5xx.cooldown_seconds == 90

    cfg = RouterErrorPolicyConfig.model_validate(
        {
            "overrides": {
                "5xx": {"failure_threshold": 9},
                "503": {"failure_threshold": 2, "respect_retry_after": True},
            }
        }
    )
    p503 = resolve_router_error_policy(
        "503",
        config=cfg,
        circuit_breaker_threshold=4,
        circuit_breaker_cooldown=60,
        circuit_breaker_max_cooldown=600,
    )
    assert p503 is not None
    assert p503.failure_threshold == 2
    assert p503.respect_retry_after is True

    p500 = resolve_router_error_policy(
        "500",
        config=cfg,
        circuit_breaker_threshold=4,
        circuit_breaker_cooldown=60,
        circuit_breaker_max_cooldown=600,
    )
    assert p500 is not None
    assert p500.failure_threshold == 9


def test_channel_error_policy_uses_status_when_policy_key_missing() -> None:
    from lens_api.gateway.router import decision_from_policy, classify_error
    from lens_api.gateway.service.runtime_context import resolve_channel_error_policy

    runtime = {
        "router_error_policy_config": {
            "overrides": {
                "5xx": {
                    "cooldown_scope": "target",
                    "failure_threshold": 1,
                    "cooldown_seconds": 60,
                    "max_cooldown_seconds": 60,
                }
            }
        },
        "circuit_breaker_threshold": 1,
        "circuit_breaker_cooldown": 60,
        "circuit_breaker_max_cooldown": 60,
    }
    raw = '{"overrides":{"502":{"cooldown_scope":"none"},"429":{"cooldown_scope":"none"},"504":{"cooldown_scope":"none"}}}'
    for status, policy_key in (
        (502, None),
        (429, None),
        (502, "transport_error"),
        (504, "timeout"),
    ):
        key, policy = resolve_channel_error_policy(
            runtime, raw, policy_key=policy_key, status_code=status
        )
        assert key == str(status)
        assert policy is not None
        assert policy.cooldown_scope == "none"
        decision = decision_from_policy(policy, category=classify_error(status))
        assert decision is not None
        assert decision.cooldown_candidate is False


def test_router_scoped_credential_and_model_isolation() -> None:
    from lens_api.models import ChannelKeyItem
    from lens_api.gateway.router import resolve_router_error_policy

    router = GatewayRouter()
    channel = ChannelConfig(
        id="channel-a",
        name="A",
        protocol=ProtocolKind.OPENAI_CHAT,
        base_url="https://example.com",
        api_key="sk",
        keys=[
            ChannelKeyItem(id="k1", key="a", remark="1", enabled=True),
            ChannelKeyItem(id="k2", key="b", remark="2", enabled=True),
        ],
    )
    policy = resolve_router_error_policy("429")
    applied = router.record_failure(
        channel.id,
        "rate limited",
        status_code=429,
        credential_id="k1",
        policy=policy,
        retry_after_seconds=2,
    )
    assert applied == 0
    assert (
        router.is_target_available(RouteTarget(channel=channel, credential_id="k1"))
        is True
    )
    assert (
        router.is_target_available(RouteTarget(channel=channel, credential_id="k2"))
        is True
    )

    router2 = GatewayRouter()
    channel2 = _router_channel("channel-b", "B")
    p500 = resolve_router_error_policy("500", circuit_breaker_threshold=1)
    router2.record_failure(
        channel2.id,
        "server",
        status_code=500,
        model_name="model-a",
        policy=p500,
    )
    assert (
        router2.is_target_available(RouteTarget(channel=channel2, model_name="model-a"))
        is True
    )
    assert (
        router2.is_target_available(RouteTarget(channel=channel2, model_name="model-b"))
        is True
    )


def test_protocol_channels_share_concurrency_limit() -> None:
    router = GatewayRouter()
    chat = _router_channel("cfg_openai_chat", "shared").model_copy(
        update={"concurrency_limit": 1}
    )
    responses = chat.model_copy(
        update={
            "id": "cfg_openai_responses",
            "protocol": ProtocolKind.OPENAI_RESPONSES,
        }
    )

    release, reason = router.acquire_target(RouteTarget(chat))
    assert release is not None
    assert reason is None
    rejected, reason = router.acquire_target(RouteTarget(responses))
    assert rejected is None
    assert reason == "concurrency"

    release()
    release()
    next_release, reason = router.acquire_target(RouteTarget(responses))
    assert next_release is not None
    assert reason is None
    next_release()


def test_protocol_channels_share_rpm_limit() -> None:
    router = GatewayRouter()
    chat = _router_channel("cfg_openai_chat", "shared").model_copy(
        update={"rpm_limit": 1}
    )
    responses = chat.model_copy(
        update={
            "id": "cfg_openai_responses",
            "protocol": ProtocolKind.OPENAI_RESPONSES,
        }
    )

    release, reason = router.acquire_target(RouteTarget(chat))
    assert release is not None
    assert reason is None
    rejected, reason = router.acquire_target(RouteTarget(responses))
    assert rejected is None
    assert reason == "rpm"
    release()
    still_rejected, reason = router.acquire_target(RouteTarget(responses))
    assert still_rejected is None
    assert reason == "rpm"


def test_protocol_usage_limit_blocks_acquire() -> None:
    router = GatewayRouter()
    token_blocked = _router_channel("cfg_openai_chat", "shared").model_copy(
        update={"token_limit": 10, "spent_tokens": 10}
    )
    rejected, reason = router.acquire_target(RouteTarget(token_blocked))
    assert rejected is None
    assert reason == "usage"

    cost_blocked = _router_channel("cfg_openai_chat", "shared").model_copy(
        update={"cost_limit_usd": 1.5, "spent_cost_usd": 1.5}
    )
    rejected, reason = router.acquire_target(RouteTarget(cost_blocked))
    assert rejected is None
    assert reason == "usage"

    allowed = _router_channel("cfg_openai_chat", "shared").model_copy(
        update={
            "token_limit": 10,
            "spent_tokens": 9,
            "cost_limit_usd": 1.5,
            "spent_cost_usd": 1.49,
        }
    )
    release, reason = router.acquire_target(RouteTarget(allowed))
    assert release is not None
    assert reason is None
    release()


def test_stream_client_iterator_releases_concurrency() -> None:
    releases = 0

    def release() -> None:
        nonlocal releases
        releases += 1

    capture = StreamCapture(capture_body=False, concurrency_release=release)
    raw = TrackedAsyncBytes([b"data: ok\n\n"])

    assert asyncio.run(_collect(_stream_client_iterator(raw, capture)))
    assert releases == 1
    assert capture.concurrency_release is None


def test_gateway_streaming_response_finalizes_on_send_failure() -> None:
    async def exercise() -> tuple[int, bool, bool, bool]:
        releases = 0
        iterator_closed = False
        background_ran = False

        def release() -> None:
            nonlocal releases
            releases += 1

        async def content() -> AsyncIterator[bytes]:
            nonlocal iterator_closed
            try:
                yield b"chunk"
            finally:
                iterator_closed = True

        async def finalize() -> None:
            nonlocal background_ran
            background_ran = True

        upstream = httpx.Response(200)
        capture = StreamCapture(
            capture_body=False,
            concurrency_release=release,
            upstream_response=upstream,
        )
        response = _GatewayStreamingResponse(
            content(),
            capture=capture,
            background=BackgroundTask(finalize),
        )

        async def receive() -> dict[str, str]:
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        try:
            await response(scope, receive, send)
        except Exception:
            pass
        else:
            raise AssertionError("send failure must propagate")

        return releases, iterator_closed, upstream.is_closed, background_ran

    assert asyncio.run(exercise()) == (1, True, True, True)


def test_router_auth_does_not_count_toward_failure_rate() -> None:
    router = GatewayRouter(
        circuit_minimum_requests=2,
        circuit_failure_rate_threshold=0.5,
    )
    channel = _router_channel("channel-a", "A")
    from lens_api.gateway.router import resolve_router_error_policy

    auth = resolve_router_error_policy("401")
    assert auth is not None
    assert auth.count_toward_failure_rate is False
    router.record_failure(
        channel.id,
        "auth",
        status_code=401,
        credential_id="k1",
        policy=auth,
    )
    # One 500 with high threshold should not open via failure rate polluted by auth.
    router.record_failure(
        channel.id,
        "server",
        status_code=500,
        threshold=99,
        cooldown_seconds=60,
        max_cooldown_seconds=120,
    )
    health = router.snapshot([channel]).health[0]
    # Auth is credential-scoped; channel failure-rate window should only have the 500.
    assert health.window_request_count <= 1
