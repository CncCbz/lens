from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
from starlette.responses import Response

from lens_api.api.app import create_app
from lens_api.gateway import service
from lens_api.gateway.service.payload_serialization import _dump_log_json
from lens_api.gateway.service.proxy_upstream import (
    _build_json_result,
    _build_sse_to_json_result,
    _prepare_channel_request,
)
from lens_api.gateway.service.runtime_context import (
    GatewayApiKey,
    StreamCapture,
    UpstreamResult,
    app_state,
    relay_log_capture_flags,
)
from lens_api.gateway.service.stream_logging import _record_stream_request_log
from lens_api.gateway.service.stream_restore import _distill_stream_response_content
from lens_api.models import ChannelConfig, ChannelKeyItem, ProtocolKind
from lens_api.persistence.repositories.settings_repository import SettingsRepository
from lens_api.persistence.shared import (
    SETTING_RELAY_LOG_DEBUG_MODE,
    SETTING_RELAY_LOG_INPUT_ENABLED,
    SETTING_RELAY_LOG_OUTPUT_ENABLED,
    SETTING_RELAY_LOG_REQUEST_BODY_ENABLED,
    SETTING_RELAY_LOG_RESPONSE_BODY_ENABLED,
    SettingItem,
)


def test_relay_log_capture_flags_defaults() -> None:
    assert relay_log_capture_flags({}) == (True, True, False, False, False, False)
    assert relay_log_capture_flags({"relay_log_body_enabled": True}) == (
        True,
        True,
        True,
        True,
        False,
        False,
    )
    assert relay_log_capture_flags(
        {
            "relay_log_request_headers_enabled": False,
            "relay_log_response_headers_enabled": True,
            "relay_log_request_body_enabled": True,
            "relay_log_response_body_enabled": False,
            "relay_log_body_enabled": True,
            "relay_log_input_enabled": True,
            "relay_log_output_enabled": True,
        }
    ) == (False, True, True, False, True, False)
    assert relay_log_capture_flags(
        {
            "relay_log_request_body_enabled": True,
            "relay_log_response_body_enabled": True,
            "relay_log_input_enabled": True,
            "relay_log_output_enabled": True,
        }
    ) == (True, True, True, True, True, True)


def test_legacy_body_setting_fills_missing_body_flags() -> None:
    parse = SettingsRepository._parse_bool
    old_body = parse("true", default=False)
    assert parse(None, default=old_body) is True
    assert parse("false", default=old_body) is False


def test_dump_log_json_input_filtering() -> None:
    payload = {
        "model": "gpt-4o",
        "temperature": 0.7,
        "messages": [{"role": "user", "content": "hello world"}],
        "prompt": "completion prompt",
        "system": "system prompt",
    }
    dumped_without_input = _dump_log_json(payload, log_input=False)
    assert dumped_without_input is not None
    assert '"messages":"<omitted>"' in dumped_without_input
    assert '"prompt":"<omitted>"' in dumped_without_input
    assert '"system":"<omitted>"' in dumped_without_input
    assert '"model":"gpt-4o"' in dumped_without_input
    assert '"temperature":0.7' in dumped_without_input

    dumped_with_input = _dump_log_json(payload, log_input=True)
    assert dumped_with_input is not None
    assert "hello world" in dumped_with_input


def test_dump_log_json_output_filtering() -> None:
    chat_response = {
        "id": "chatcmpl-123",
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "super long output answer",
                    "reasoning_content": "thinking process",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 42},
    }
    dumped_without_output = _dump_log_json(chat_response, log_output=False)
    assert dumped_without_output is not None
    assert '"content":"<omitted>"' in dumped_without_output
    assert '"reasoning_content":"<omitted>"' in dumped_without_output
    assert "super long output answer" not in dumped_without_output
    assert '"finish_reason":"stop"' in dumped_without_output
    assert '"total_tokens":42' in dumped_without_output

    anthropic_response = {
        "id": "msg_123",
        "model": "claude-3-5-sonnet",
        "content": [{"type": "text", "text": "claude output text"}],
        "stop_reason": "end_turn",
    }
    dumped_anthropic = _dump_log_json(anthropic_response, log_output=False)
    assert dumped_anthropic is not None
    assert '"content":"<omitted>"' in dumped_anthropic
    assert "claude output text" not in dumped_anthropic


def test_dump_log_json_gemini_and_responses() -> None:
    gemini_req = {
        "contents": [{"role": "user", "parts": [{"text": "gemini question"}]}],
        "systemInstruction": {"parts": [{"text": "be concise"}]},
        "generationConfig": {"temperature": 0.5},
    }
    dumped_gemini_req = _dump_log_json(gemini_req, log_input=False)
    assert dumped_gemini_req is not None
    assert '"contents":"<omitted>"' in dumped_gemini_req
    assert '"systemInstruction":"<omitted>"' in dumped_gemini_req
    assert '"temperature":0.5' in dumped_gemini_req

    gemini_resp = {
        "candidates": [
            {
                "content": {"parts": [{"text": "gemini answer"}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"totalTokenCount": 10},
    }
    dumped_gemini_resp = _dump_log_json(gemini_resp, log_output=False)
    assert dumped_gemini_resp is not None
    assert '"content":"<omitted>"' in dumped_gemini_resp
    assert "gemini answer" not in dumped_gemini_resp
    assert '"totalTokenCount":10' in dumped_gemini_resp

    responses_resp = {
        "id": "resp_123",
        "output": [{"type": "message", "content": "resp answer"}],
        "status": "completed",
    }
    dumped_responses_resp = _dump_log_json(responses_resp, log_output=False)
    assert dumped_responses_resp is not None
    assert '"output":"<omitted>"' in dumped_responses_resp
    assert '"status":"completed"' in dumped_responses_resp


def test_dump_log_json_preserves_error_responses() -> None:
    error_payload = {
        "error": {
            "message": "Invalid API key provided",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }
    }
    dumped_error = _dump_log_json(error_payload, log_output=False)
    assert dumped_error is not None
    assert "Invalid API key provided" in dumped_error
    assert "invalid_api_key" in dumped_error


def test_distill_stream_response_content_output_filtering() -> None:
    raw_sse = (
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"hello"}}]}\n\n'
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":" world"}}]}\n\n'
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    )
    distilled_without_output = _distill_stream_response_content(
        ProtocolKind.OPENAI_CHAT, raw_sse, log_output=False
    )
    assert distilled_without_output is not None
    assert '"content":"<omitted>"' in distilled_without_output
    assert "hello world" not in distilled_without_output
    assert '"finish_reason":"stop"' in distilled_without_output
    assert '"total_tokens":5' in distilled_without_output

    distilled_with_output = _distill_stream_response_content(
        ProtocolKind.OPENAI_CHAT, raw_sse, log_output=True
    )
    assert distilled_with_output is not None
    assert "hello world" in distilled_with_output


def _mock_channel(protocol: ProtocolKind = ProtocolKind.OPENAI_CHAT) -> ChannelConfig:
    return ChannelConfig(
        id="test-ch",
        name="TestChannel",
        protocol=protocol,
        base_url="https://upstream.example.com",
        api_key="sk-test",
        keys=[ChannelKeyItem(id="k1", key="sk-test")],
    )


def test_upstream_request_preparation_filtering() -> None:
    channel = _mock_channel()
    upstream_body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "secret question"}],
        "temperature": 0.7,
    }

    # 1. log_body_enabled=True, log_input=False
    _, body_bytes, upstream_request_content = _prepare_channel_request(
        channel,
        upstream_body,
        credential_id=None,
        user_agent="lens-test",
        forwarded_headers=None,
        model_group_headers=(),
        log_body_enabled=True,
        log_input=False,
    )
    assert upstream_request_content is not None
    assert '"messages":"<omitted>"' in upstream_request_content
    assert "secret question" not in upstream_request_content
    # Network bytes sent to upstream must contain the actual user input
    assert b"secret question" in body_bytes

    # 2. log_body_enabled=True, log_input=True
    _, body_bytes_full, upstream_request_content_full = _prepare_channel_request(
        channel,
        upstream_body,
        credential_id=None,
        user_agent="lens-test",
        forwarded_headers=None,
        model_group_headers=(),
        log_body_enabled=True,
        log_input=True,
    )
    assert upstream_request_content_full is not None
    assert "secret question" in upstream_request_content_full
    assert b"secret question" in body_bytes_full


def test_upstream_json_response_filtering() -> None:
    channel = _mock_channel()
    body = {"model": "gpt-4o"}
    raw_payload = {
        "id": "chatcmpl-test",
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "secret answer from upstream",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        },
    }
    content_bytes = json.dumps(raw_payload).encode()
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=content_bytes,
        request=httpx.Request("POST", "https://upstream.example.com"),
    )

    # 1. log_body_enabled=True, log_output=False
    result_no_output = asyncio.run(
        _build_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            body,
            pricing_group_name=None,
            request_content=None,
            log_body_enabled=True,
            log_debug_enabled=True,
            log_output=False,
        )
    )
    # Client response must be the original untouched answer
    assert b"secret answer from upstream" in result_no_output.response.body
    # Logged response content must be filtered
    assert result_no_output.response_content is not None
    assert '"content":"<omitted>"' in result_no_output.response_content
    assert "secret answer from upstream" not in result_no_output.response_content
    # Upstream raw debug content is omitted when log_output is False
    assert result_no_output.upstream_response_content is None

    # 2. log_body_enabled=True, log_output=True
    result_with_output = asyncio.run(
        _build_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            body,
            pricing_group_name=None,
            request_content=None,
            log_body_enabled=True,
            log_debug_enabled=True,
            log_output=True,
        )
    )
    assert result_with_output.response_content is not None
    assert "secret answer from upstream" in result_with_output.response_content
    assert result_with_output.upstream_response_content is not None


def test_upstream_sse_to_json_result_filtering() -> None:
    channel = _mock_channel()
    raw_sse = (
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"secret stream text"}}]}\n\n'
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    ).encode()
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=raw_sse,
        request=httpx.Request("POST", "https://upstream.example.com"),
    )

    result_no_output = asyncio.run(
        _build_sse_to_json_result(
            response,
            channel,
            ProtocolKind.OPENAI_CHAT,
            original_model="gpt-4o",
            pricing_group_name=None,
            request_content=None,
            log_body_enabled=True,
            log_debug_enabled=True,
            log_output=False,
        )
    )
    # Client receives full JSON restored
    assert b"secret stream text" in result_no_output.response.body
    # Logged content omits output
    assert result_no_output.response_content is not None
    assert '"content":"<omitted>"' in result_no_output.response_content
    assert "secret stream text" not in result_no_output.response_content
    assert result_no_output.upstream_response_content is None


def test_upstream_stream_logging_filtering() -> None:
    channel = _mock_channel()
    gateway_key = GatewayApiKey(
        id="gk-1",
        remark="test",
        api_key="sk-test",
        enabled=True,
        allowed_models=[],
        excluded_models=[],
        max_cost_usd=0.0,
        spent_cost_usd=0.0,
        expires_at=None,
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    capture = StreamCapture(
        capture_body=True,
        stream_started_at=0.0,
        first_token_latency_ms=10,
    )
    capture.response_content_chunks.append(
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"secret chunk output"}}]}\n\n'
    )
    capture.response_content_chunks.append(
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":8}}\n\n'
    )
    result = UpstreamResult(
        response=Response(),
        status_code=200,
        is_stream=True,
        upstream_model_name="gpt-4o",
        stream_capture=capture,
    )

    with (
        patch(
            "lens_api.gateway.service.stream_logging._update_request_log",
            new=AsyncMock(),
        ) as mock_update,
        patch(
            "lens_api.gateway.service.stream_logging._record_stream_route_health",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "lens_api.gateway.service.stream_logging._safe_estimate_cost",
            new=AsyncMock(return_value=(0.0, 0.0, 0.0)),
        ),
    ):
        asyncio.run(
            _record_stream_request_log(
                request_log_id=999,
                protocol=ProtocolKind.OPENAI_CHAT,
                requested_group_name="gpt-4o",
                resolved_group_name="gpt-4o",
                client_request_content=None,
                channel=channel,
                gateway_key=gateway_key,
                user_agent="lens-test",
                started_at=0.0,
                result=result,
                attempts=[],
                log_debug_enabled=True,
                log_output=False,
            )
        )

        mock_update.assert_awaited_once()
        kwargs = mock_update.call_args.kwargs
        # Upstream response distilled must have content omitted
        assert kwargs["upstream_response_distilled"] is not None
        assert '"content":"<omitted>"' in kwargs["upstream_response_distilled"]
        assert "secret chunk output" not in kwargs["upstream_response_distilled"]
        # Client response content must have content omitted
        assert kwargs["response_content"] is not None
        assert '"content":"<omitted>"' in kwargs["response_content"]
        assert "secret chunk output" not in kwargs["response_content"]
        # Raw upstream SSE is omitted
        assert kwargs["upstream_response_content"] is None
