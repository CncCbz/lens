from __future__ import annotations

import json

import pytest
from starlette.responses import Response

from lens_api.core.auth import REDACTED_CREDENTIAL_VALUE, redact_sensitive_header_json
from lens_api.core.config import Settings
from lens_api.gateway.converters.chat_to_anthropic import (
    anthropic_request_to_chat,
    chat_request_to_anthropic,
)
from lens_api.gateway.converters.chat_to_responses import (
    chat_request_to_responses,
    chat_response_to_responses,
    responses_request_to_chat,
    responses_response_to_chat,
)
from lens_api.gateway.service.admin_config import (
    _iter_json_chunks,
    export_settings_bundle,
)
from lens_api.gateway.service.errors import _apply_security_headers
from lens_api.gateway.service.payload_serialization import (
    _decode_log_content_bytes,
    _dump_log_json,
)
from lens_api.gateway.service.routing_plan import _prepare_upstream_body
from lens_api.gateway.service.runtime_context import app_state
from lens_api.gateway.upstreams import build_upstream_request
from lens_api.models import (
    ChannelConfig,
    ChannelKeyItem,
    ConfigBackupDump,
    ProtocolKind,
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


def test_chat_to_anthropic_preserves_cache_control_breakpoints() -> None:
    prepared = chat_request_to_anthropic(
        {
            "model": "model",
            "cache_control": {"type": "ephemeral"},
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "static policy",
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "static context",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": "question"},
                    ],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "cache_control": {"type": "ephemeral"},
                    "function": {
                        "name": "lookup",
                        "description": "Lookup data",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
    )

    assert prepared["cache_control"] == {"type": "ephemeral"}
    assert prepared["system"] == [
        {
            "type": "text",
            "text": "static policy",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    assert prepared["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "static context",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "question"},
    ]
    assert prepared["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_chat_to_anthropic_preserves_tool_cache_control_breakpoints() -> None:
    prepared = chat_request_to_anthropic(
        {
            "model": "model",
            "messages": [
                {"role": "user", "content": "question"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "cache_control": {"type": "ephemeral"},
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": [
                        {
                            "type": "text",
                            "text": "result",
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                },
            ],
        }
    )

    assert prepared["messages"][1]["content"] == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "lookup",
            "input": {},
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert prepared["messages"][2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": "result",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def test_anthropic_to_chat_preserves_cache_control_breakpoints() -> None:
    prepared = anthropic_request_to_chat(
        {
            "model": "model",
            "cache_control": {"type": "ephemeral"},
            "system": [
                {
                    "type": "text",
                    "text": "static policy",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "static context",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": "question"},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "lookup",
                            "input": {},
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "result",
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                },
            ],
            "tools": [
                {
                    "name": "lookup",
                    "description": "Lookup data",
                    "input_schema": {"type": "object", "properties": {}},
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    )

    assert prepared["cache_control"] == {"type": "ephemeral"}
    assert prepared["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "static policy",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    assert prepared["messages"][1]["content"] == [
        {
            "type": "text",
            "text": "static context",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "question"},
    ]
    assert prepared["messages"][2]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert prepared["messages"][3]["content"] == [
        {
            "type": "text",
            "text": "result",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    assert prepared["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_responses_roundtrip_preserves_top_level_cache_control() -> None:
    responses = chat_request_to_responses(
        {
            "model": "model",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    chat = responses_request_to_chat(responses)

    assert responses["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert chat["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_chat_to_responses_preserves_prompt_cache_routing_fields() -> None:
    responses = chat_request_to_responses(
        {
            "model": "model",
            "prompt_cache_key": "session-123",
            "prompt_cache_retention": "24h",
            "include": ["reasoning.encrypted_content"],
            "client_metadata": {"x-codex-installation-id": "install-1"},
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    chat = responses_request_to_chat(responses)

    assert responses["prompt_cache_key"] == "session-123"
    assert responses["prompt_cache_retention"] == "24h"
    assert responses["include"] == ["reasoning.encrypted_content"]
    assert responses["client_metadata"] == {"x-codex-installation-id": "install-1"}
    assert chat["prompt_cache_key"] == "session-123"
    assert chat["prompt_cache_retention"] == "24h"


def test_responses_roundtrip_preserves_cache_control_breakpoints() -> None:
    responses = chat_request_to_responses(
        {
            "model": "model",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "policy",
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": [
                        {
                            "type": "text",
                            "text": "result",
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "parameters": {"type": "object"},
                        "cache_control": {"type": "ephemeral"},
                    },
                }
            ],
        }
    )
    chat = responses_request_to_chat(responses)

    assert responses["input"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert responses["input"][1]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert responses["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert chat["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert chat["messages"][1]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert chat["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_chat_responses_response_conversion_preserves_cached_tokens() -> None:
    responses = chat_response_to_responses(
        {
            "id": "chatcmpl_1",
            "model": "model",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 8},
            },
        },
        "model",
    )
    chat = responses_response_to_chat(responses, "model")

    assert responses["usage"]["input_tokens_details"] == {"cached_tokens": 8}
    assert chat["usage"]["prompt_tokens_details"] == {"cached_tokens": 8}


def test_chat_to_responses_keeps_system_developer_in_input_prefix() -> None:
    responses = chat_request_to_responses(
        {
            "model": "model",
            "messages": [
                {"role": "developer", "content": "policy"},
                {"role": "system", "content": "system context"},
                {"role": "user", "content": "question"},
            ],
        }
    )

    assert "instructions" not in responses
    assert responses["input"][:3] == [
        {"role": "developer", "content": [{"type": "input_text", "text": "policy"}]},
        {
            "role": "system",
            "content": [{"type": "input_text", "text": "system context"}],
        },
        {"role": "user", "content": [{"type": "input_text", "text": "question"}]},
    ]


def test_chat_to_responses_derives_stable_prompt_cache_key() -> None:
    first = chat_request_to_responses(
        {
            "model": "model",
            "messages": [
                {"role": "developer", "content": "policy"},
                {"role": "user", "content": "question"},
            ],
        }
    )
    second = chat_request_to_responses(
        {
            "model": "model",
            "messages": [
                {"role": "developer", "content": "policy"},
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
        }
    )

    assert isinstance(first["prompt_cache_key"], str)
    assert len(first["prompt_cache_key"]) == 16
    assert second["prompt_cache_key"] == first["prompt_cache_key"]


def test_auth_headers_are_filtered_and_log_values_are_redacted() -> None:
    channel = ChannelConfig(
        id="channel",
        name="Channel",
        protocol=ProtocolKind.OPENAI_CHAT,
        base_url="https://provider.example",
        api_key="upstream-secret",
        headers={"X-Custom-Auth": "admin-secret"},
        keys=[ChannelKeyItem(id="key", key="upstream-secret")],
    )
    upstream = build_upstream_request(
        channel,
        {"model": "model", "api_key": "body-secret"},
        Settings(auth_secret_key="test-secret"),
        forwarded_headers={
            "Authorization": "Bearer gateway-secret",
            "Cookie": "session=secret",
            "X-Access-Token": "access-secret",
            "X-Anthropic-Authentication": "anthropic-secret",
            "X-Claude-Code-OAuth-Token": "oauth-secret",
            "X-Goog-Api-Key": "gateway-secret",
            "X-Custom-Auth": "gateway-secret",
            "X-Request-Id": "request-id",
        },
    )

    assert upstream.headers["authorization"] == "Bearer upstream-secret"
    assert upstream.headers["X-Custom-Auth"] == "admin-secret"
    assert upstream.headers["X-Request-Id"] == "request-id"
    assert not any(
        key.lower()
        in {
            "cookie",
            "x-access-token",
            "x-anthropic-authentication",
            "x-claude-code-oauth-token",
            "x-goog-api-key",
        }
        for key in upstream.headers
    )
    assert (
        json.loads(_dump_log_json(upstream.headers) or "{}")["X-Custom-Auth"]
        == REDACTED_CREDENTIAL_VALUE
    )

    logged = json.loads(
        _dump_log_json(
            {
                "Authorization": "Bearer gateway-secret",
                "accessToken": "access-secret",
                "api_key": "body-secret",
                "model": "model",
            }
        )
        or "{}"
    )
    assert logged == {
        "Authorization": REDACTED_CREDENTIAL_VALUE,
        "accessToken": REDACTED_CREDENTIAL_VALUE,
        "api_key": REDACTED_CREDENTIAL_VALUE,
        "model": "model",
    }


def test_log_content_redacts_sensitive_json_and_sse_fields() -> None:
    assert json.loads(
        _decode_log_content_bytes(b'{"api_key":"secret","value":1}') or "{}"
    ) == {"api_key": REDACTED_CREDENTIAL_VALUE, "value": 1}

    sse = _decode_log_content_bytes(
        b'data: {"token":"secret","delta":"ok"}\n\ndata: [DONE]\n\n'
    )
    assert sse == ('data: {"token":"<redacted>","delta":"ok"}\n\ndata: [DONE]\n\n')

    multiline_sse = _decode_log_content_bytes(
        b'data: {"token":\ndata: "secret","delta":"ok"}\n\n'
    )
    assert multiline_sse == 'data: {"token":"<redacted>","delta":"ok"}\n\n'


def test_sensitive_header_json_redaction_fails_closed() -> None:
    redacted = redact_sensitive_header_json(
        json.dumps(
            {
                "Authorization": "Bearer gateway-secret",
                "x-api-key": "provider-secret",
                "x-request-id": "request-id",
            }
        )
    )

    assert redacted is not None
    assert json.loads(redacted) == {
        "Authorization": REDACTED_CREDENTIAL_VALUE,
        "x-api-key": REDACTED_CREDENTIAL_VALUE,
        "x-request-id": "request-id",
    }
    assert redact_sensitive_header_json("not-json") is None


def test_security_headers_block_framing_and_script_attributes() -> None:
    response = _apply_security_headers(Response())

    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src-attr 'none'" in response.headers["content-security-policy"]


def test_json_backup_chunks_round_trip() -> None:
    payload = {"items": ["中" * 20, "value"], "enabled": True}
    encoded = "".join(_iter_json_chunks(payload, chunk_size=8))

    assert json.loads(encoded) == payload


@pytest.mark.asyncio
async def test_backup_export_streams_without_request_logs(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class BackupStoreStub:
        async def export_dump(self, **kwargs):
            calls.append(kwargs)
            return ConfigBackupDump(
                version=5,
                exported_at="2026-01-01T00:00:00+00:00",
                lens_version="test",
                include_request_logs=True,
                request_logs=[],
            )

    class SettingsRepositoryStub:
        async def get_runtime_settings(self):
            return {"time_zone": "UTC"}

    monkeypatch.setattr(app_state, "backup_store", BackupStoreStub())
    monkeypatch.setattr(app_state, "settings_repo", SettingsRepositoryStub())

    response = await export_settings_bundle(_=None)
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    payload = json.loads("".join(chunks))

    assert calls and "include_request_logs" not in calls[0]
    assert "include_request_logs" not in payload
    assert "request_logs" not in payload
    assert response.headers["cache-control"] == "no-store"
