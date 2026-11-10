from __future__ import annotations

import json

import pytest
from starlette.responses import Response

from lens_api.core.auth import REDACTED_CREDENTIAL_VALUE, redact_sensitive_header_json
from lens_api.core.config import Settings
from lens_api.gateway.converters import convert_request
from lens_api.gateway.converters.chat_to_anthropic import (
    anthropic_request_to_chat,
    anthropic_response_to_chat,
    anthropic_stream_to_chat_stream,
    chat_response_to_anthropic,
    chat_stream_to_anthropic_stream,
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
            # pi always sends is_error on the wire.
            "is_error": False,
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


def test_chat_to_responses_preserves_reasoning_before_tool_calls() -> None:
    responses = convert_request(
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "user", "content": "look up both values"},
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "first reasoning",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"id":1}'},
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"id":2}'},
                        },
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "one"},
                {"role": "tool", "tool_call_id": "call_2", "content": "two"},
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning": "second reasoning",
                    "tool_calls": [
                        {
                            "id": "call_3",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": '{"id":3}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_3", "content": "three"},
            ],
        },
        preserve_reasoning=True,
    )

    input_items = responses["input"]
    assert [item.get("type") or item.get("role") for item in input_items] == [
        "user",
        "reasoning",
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert [
        item["content"][0]["text"]
        for item in input_items
        if item.get("type") == "reasoning"
    ] == ["first reasoning", "second reasoning"]


def test_chat_to_responses_pads_missing_tool_call_reasoning() -> None:
    responses = convert_request(
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "user", "content": "question"},
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
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
        },
        preserve_reasoning=True,
    )

    assert responses["input"][1] == {
        "type": "reasoning",
        "content": [{"type": "reasoning_text", "text": " "}],
    }


def test_chat_to_responses_drops_reasoning_by_default() -> None:
    responses = chat_request_to_responses(
        {
            "model": "model",
            "messages": [
                {"role": "user", "content": "question"},
                {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": "private reasoning",
                },
            ],
        }
    )

    assert not any(item.get("type") == "reasoning" for item in responses["input"])


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


@pytest.mark.parametrize("with_tool", [False, True])
def test_anthropic_signed_blocks_roundtrip(with_tool: bool) -> None:
    blocks = [
        {"type": "thinking", "thinking": "first", "signature": "sig-one"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "thinking", "thinking": "second", "signature": "sig-two"},
        {"type": "text", "text": "answer"},
    ]
    if with_tool:
        blocks.append(
            {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {}}
        )
    response = {"content": blocks, "stop_reason": "end_turn"}
    chat = anthropic_response_to_chat(response, "model")
    message = chat["choices"][0]["message"]
    assert "reasoning_signature" not in message
    assert chat_response_to_anthropic(chat, "model")["content"] == blocks
    replay = chat_request_to_anthropic(
        {"messages": [{"role": "user", "content": "hello"}, message]}
    )
    assert replay["messages"][1]["content"] == blocks
    pivot = anthropic_request_to_chat(
        {"messages": [{"role": "assistant", "content": blocks}]}, preserve_thinking=True
    )
    assert chat_request_to_anthropic(pivot)["messages"][-1]["content"] == blocks
    stripped = anthropic_request_to_chat(
        {"messages": [{"role": "assistant", "content": blocks}]}
    )
    assert all(not key.startswith("reasoning_") for key in stripped["messages"][0])


def test_anthropic_unsigned_reasoning_and_filter() -> None:
    chat = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "answer",
                    "reasoning_content": "unsigned",
                },
                "finish_reason": "content_filter",
            }
        ]
    }
    response = chat_response_to_anthropic(chat, "model")
    assert response["stop_reason"] == "refusal"
    assert response["content"][0] == {"type": "thinking", "thinking": "unsigned"}
    assert chat_request_to_anthropic({"messages": [chat["choices"][0]["message"]]})[
        "messages"
    ][-1]["content"][0] == {"type": "text", "text": "unsigned"}


def test_anthropic_error_tool_images_follow_all_results() -> None:
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="},
    }
    pivot = anthropic_request_to_chat(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "one",
                            "is_error": True,
                            "content": [image],
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "two",
                            "is_error": False,
                            "content": "ok",
                        },
                    ],
                }
            ]
        }
    )
    assert [message["is_error"] for message in pivot["messages"]] == [True, False]
    blocks = chat_request_to_anthropic(pivot)["messages"][0]["content"]
    assert [block["type"] for block in blocks] == [
        "tool_result",
        "tool_result",
        "text",
        "image",
    ]
    assert blocks[0]["is_error"] is True
    assert all(block["type"] == "text" for block in blocks[0]["content"])
    assert any(
        "image attached after" in block["text"] for block in blocks[0]["content"]
    )
    assert blocks[-1] == image


@pytest.mark.parametrize(
    "model,adaptive",
    [
        ("claude-opus-4-6", True),
        ("claude-sonnet-4-6", True),
        ("claude-opus-4-7", True),
        ("claude-opus-4-8", True),
        ("claude-opus-5", True),
        ("claude-opus.5", True),
        ("claude-sonnet-5", True),
        ("claude-sonnet.5", True),
        ("claude-haiku-5", True),
        ("claude-haiku.5", True),
        ("claude-fable-5-2", True),
        ("claude-mythos-5", True),
        ("unknown-claude-opus-4-6", True),
        ("claude-opus-4-6-20990101", True),
        ("anthropic/claude-opus-4.7-20260101", True),
        ("~anthropic/claude-opus-latest", True),
        ("~anthropic/claude-mythos-latest", True),
        ("~anthropic/claude-unknown-latest", True),
        ("claude-opus-latest", False),
        ("anthropic/claude-opus-latest", False),
        ("~anthropic/claude-opus-latest-20260101", False),
        ("~ANTHROPIC/claude-opus-latest", False),
        ("CLAUDE-OPUS-4-7", False),
        ("claude-fable.5", False),
        ("claude-mythos.5", False),
        ("claude-opus-4-9", False),
        ("claude-sonnet-4-7", False),
        ("claude-opus-6", False),
        ("claude-sonnet-4-5", False),
        ("unknown-model", False),
    ],
)
def test_anthropic_adaptive_uses_family_rules(model: str, adaptive: bool) -> None:
    body = chat_request_to_anthropic(
        {
            "model": model,
            "messages": [],
            "reasoning_effort": "medium",
            "temperature": 0.5,
        }
    )
    assert body["thinking"]["type"] == ("adaptive" if adaptive else "enabled")
    assert "temperature" not in body
    if adaptive:
        assert body["output_config"] == {"effort": "medium"}
        assert "budget_tokens" not in body["thinking"]
        assert body["max_tokens"] == 4096
    else:
        assert body["thinking"]["budget_tokens"] == 8192


@pytest.mark.parametrize(
    "model,xhigh_effort,max_effort",
    [
        ("claude-opus-4-6", "high", "max"),
        ("claude-opus-4.6", "high", "max"),
        ("claude-sonnet-4-6", "high", "max"),
        ("claude-sonnet-4.6", "high", "max"),
        ("claude-opus-4-7", "xhigh", "max"),
        ("claude-opus-4.7", "xhigh", "max"),
        ("claude-opus-4-8", "xhigh", "max"),
        ("claude-opus-4.8", "xhigh", "max"),
        ("claude-opus-5", "xhigh", "max"),
        ("claude-opus.5", "xhigh", "max"),
        ("claude-sonnet-5", "xhigh", "max"),
        ("claude-sonnet.5", "xhigh", "max"),
        ("claude-haiku-5", "xhigh", "max"),
        ("claude-haiku.5", "xhigh", "max"),
        ("claude-fable-5", "xhigh", "max"),
        ("claude-fable-5-2", "xhigh", "max"),
        ("claude-mythos-5", "high", "high"),
        ("claude-mythos-5.1", "high", "high"),
        ("~anthropic/claude-opus-latest", "high", "high"),
        ("~anthropic/claude-sonnet-latest", "high", "high"),
        ("~anthropic/claude-fable-latest", "high", "high"),
        ("~anthropic/claude-mythos-latest", "high", "high"),
        ("unknown-model", None, None),
        ("claude-opus-4-9", None, None),
    ],
)
@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_anthropic_family_effort_mapping(
    model: str, xhigh_effort: str | None, max_effort: str | None, effort: str
) -> None:
    expected = xhigh_effort if effort == "xhigh" else max_effort
    models = [model]
    if not model.startswith("~"):
        models.extend([f"anthropic/{model}", f"{model}-20260101"])
    for model_id in models:
        body = chat_request_to_anthropic(
            {"model": model_id, "messages": [], "reasoning_effort": effort}
        )
        assert body["model"] == model_id
        if expected is None:
            assert body["thinking"] == {
                "type": "enabled",
                "budget_tokens": 16384,
                "display": "summarized",
            }
            assert "output_config" not in body
        else:
            assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
            assert body["output_config"] == {"effort": expected}


@pytest.mark.parametrize(
    "model",
    ["claude-opus-4.7", "claude-mythos-5", "~anthropic/claude-opus-latest"],
)
@pytest.mark.parametrize(
    "effort,expected",
    [("minimal", "low"), ("low", "low"), ("medium", "medium"), ("high", "high")],
)
def test_anthropic_adaptive_standard_efforts(
    model: str, effort: str, expected: str
) -> None:
    body = chat_request_to_anthropic(
        {"model": model, "messages": [], "reasoning_effort": effort}
    )
    assert body["thinking"]["type"] == "adaptive"
    assert body["output_config"] == {"effort": expected}


async def _anthropic_events(events):
    for event in events:
        yield ("data: " + json.dumps(event) + chr(10) * 2).encode()


def _chat_event_payloads(chunks):
    return [
        json.loads(line[6:])
        for chunk in chunks
        for line in chunk.decode().splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


@pytest.mark.asyncio
async def test_anthropic_stream_preserves_each_signed_and_redacted_block() -> None:
    blocks = [
        {"type": "thinking", "thinking": "first", "signature": "sig-one"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "thinking", "thinking": "second", "signature": "sig-two"},
    ]
    events = [{"type": "message_start", "message": {}}]
    for index, block in enumerate(blocks):
        start = (
            {"type": block["type"], "data": block["data"]}
            if block["type"] == "redacted_thinking"
            else {"type": "thinking", "thinking": "", "signature": ""}
        )
        events.append(
            {"type": "content_block_start", "index": index, "content_block": start}
        )
        if block["type"] == "thinking":
            events.append(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "thinking_delta", "thinking": block["thinking"]},
                }
            )
            for signature in (block["signature"][:3], block["signature"][3:]):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "signature_delta", "signature": signature},
                    }
                )
        events.append({"type": "content_block_stop", "index": index})
    events.extend(
        [
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
        ]
    )
    chunks = [
        chunk
        async for chunk in anthropic_stream_to_chat_stream(
            _anthropic_events(events), "model"
        )
    ]
    deltas = [event["choices"][0]["delta"] for event in _chat_event_payloads(chunks)]
    assert (
        "".join(delta.get("reasoning_signature", "") for delta in deltas)
        == "sig-onesig-two"
    )
    metadata = [
        block for delta in deltas for block in delta.get("reasoning_blocks", [])
    ]
    assert metadata == blocks
    replay = chat_request_to_anthropic(
        {
            "messages": [
                {
                    "role": "assistant",
                    "reasoning_content": "firstsecond",
                    "reasoning_blocks": metadata,
                }
            ]
        }
    )
    assert replay["messages"][-1]["content"] == blocks

    async def source():
        for chunk in chunks:
            yield chunk

    restored = _chat_event_payloads(
        [chunk async for chunk in chat_stream_to_anthropic_stream(source(), "model")]
    )
    assert [
        event["delta"]["signature"]
        for event in restored
        if event.get("delta", {}).get("type") == "signature_delta"
    ] == ["sig-one", "sig-two"]
    assert [
        event["content_block"]
        for event in restored
        if event.get("content_block", {}).get("type") == "redacted_thinking"
    ] == [blocks[1]]


@pytest.mark.asyncio
async def test_anthropic_stream_error_before_stop_raises() -> None:
    events = [
        {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    with pytest.raises(ValueError, match="Anthropic stream error.*overloaded_error"):
        _ = [
            chunk
            async for chunk in anthropic_stream_to_chat_stream(
                _anthropic_events(events), "model"
            )
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [ProtocolKind.OPENAI_CHAT, ProtocolKind.ANTHROPIC, ProtocolKind.OPENAI_RESPONSES],
)
@pytest.mark.parametrize("with_image", [False, True])
async def test_gemini_text_parts_concatenate_identically_for_json_and_stream(
    target: ProtocolKind, with_image: bool
) -> None:
    from lens_api.gateway.converters import convert_response, convert_stream_iterator

    parts: list[dict[str, object]] = [
        {"text": "private", "thought": True, "thoughtSignature": "thought-sig"},
        {"text": "an", "thoughtSignature": "answer-sig"},
        {"text": "swer"},
    ]
    if with_image:
        parts.append({"inlineData": {"mimeType": "image/png", "data": "YQ=="}})
    response = {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
    converted = json.loads(
        convert_response(
            target, ProtocolKind.GEMINI, json.dumps(response).encode(), "m"
        )
    )
    if target == ProtocolKind.OPENAI_CHAT:
        content = converted["choices"][0]["message"]["content"]
        texts = (
            [content]
            if isinstance(content, str)
            else [block["text"] for block in content if block["type"] == "text"]
        )
    elif target == ProtocolKind.ANTHROPIC:
        texts = [
            block["text"] for block in converted["content"] if block["type"] == "text"
        ]
    else:
        texts = [
            block["text"]
            for item in converted["output"]
            if item["type"] == "message"
            for block in item["content"]
            if block["type"] == "output_text"
        ]
    assert texts == ["answer"]
    payloads = _chat_event_payloads(
        [
            chunk
            async for chunk in convert_stream_iterator(
                target, ProtocolKind.GEMINI, _anthropic_events([response]), "m"
            )
        ]
    )
    if target == ProtocolKind.OPENAI_CHAT:
        contents = [
            choice["delta"]["content"]
            for p in payloads
            for choice in p.get("choices", [])
            if "content" in choice.get("delta", {})
        ]
        deltas = [
            (
                content
                if isinstance(content, str)
                else "".join(
                    block["text"] for block in content if block["type"] == "text"
                )
            )
            for content in contents
        ]
    elif target == ProtocolKind.ANTHROPIC:
        deltas = [
            p["delta"]["text"]
            for p in payloads
            if p.get("delta", {}).get("type") == "text_delta"
        ]
    else:
        deltas = [
            p["delta"]
            for p in payloads
            if p.get("type") == "response.output_text.delta"
        ]
    assert deltas == ["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target", [ProtocolKind.ANTHROPIC, ProtocolKind.OPENAI_RESPONSES]
)
async def test_multimodal_chat_response_text_stays_string(target: ProtocolKind) -> None:
    from copy import deepcopy

    from lens_api.gateway.converters import convert_response, convert_stream_iterator

    content = [
        {"type": "text", "text": "private", "thought": True},
        {"type": "text", "text": "answer"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
    ]
    snapshot = deepcopy(content)
    response = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }
    converted = json.loads(
        convert_response(
            target, ProtocolKind.OPENAI_CHAT, json.dumps(response).encode(), "m"
        )
    )
    if target == ProtocolKind.ANTHROPIC:
        texts = [
            block["text"] for block in converted["content"] if block["type"] == "text"
        ]
    else:
        texts = [
            block["text"]
            for item in converted["output"]
            if item["type"] == "message"
            for block in item["content"]
            if block["type"] == "output_text"
        ]
    assert texts == ["answer"]
    events = [{"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}]
    payloads = _chat_event_payloads(
        [
            chunk
            async for chunk in convert_stream_iterator(
                target, ProtocolKind.OPENAI_CHAT, _anthropic_events(events), "m"
            )
        ]
    )
    if target == ProtocolKind.ANTHROPIC:
        deltas = [
            p["delta"]["text"]
            for p in payloads
            if p.get("delta", {}).get("type") == "text_delta"
        ]
    else:
        deltas = [
            p["delta"]
            for p in payloads
            if p.get("type") == "response.output_text.delta"
        ]
    assert deltas == ["answer"]
    assert content == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("finished", [False, True])
async def test_chat_to_anthropic_root_error_prevents_success(finished: bool) -> None:
    async def source():
        yield (
            "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {"content": "answer"},
                            "finish_reason": "stop" if finished else None,
                        }
                    ]
                }
            )
            + "\n\n"
        ).encode()
        yield b'data: {"error":{"message":"failed"}}\n\n'
        yield b"data: [DONE]\n\n"

    emitted: list[bytes] = []
    with pytest.raises(ValueError, match="Chat stream returned an error"):
        async for chunk in chat_stream_to_anthropic_stream(source(), "model"):
            emitted.append(chunk)

    payloads = _chat_event_payloads(emitted)
    assert any(event.get("delta", {}).get("text") == "answer" for event in payloads)
    assert not any(
        event["type"] in {"message_delta", "message_stop"} for event in payloads
    )


@pytest.mark.asyncio
async def test_chat_stream_filter_is_anthropic_refusal() -> None:
    events = [{"choices": [{"delta": {}, "finish_reason": "content_filter"}]}]
    payloads = _chat_event_payloads(
        [
            chunk
            async for chunk in chat_stream_to_anthropic_stream(
                _anthropic_events(events), "model"
            )
        ]
    )
    assert (
        next(event for event in payloads if event["type"] == "message_delta")["delta"][
            "stop_reason"
        ]
        == "refusal"
    )


@pytest.mark.parametrize("cache_total,expected", [(100, 100), (None, 100), (0, 0)])
@pytest.mark.asyncio
async def test_anthropic_cache_creation_total_precedes_ttl_breakdown(
    cache_total: int | None, expected: int
) -> None:
    usage = {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 60,
            "ephemeral_1h_input_tokens": 40,
        },
    }
    if cache_total is not None:
        usage["cache_creation_input_tokens"] = cache_total
    expected_usage = {
        "prompt_tokens": 10 + expected,
        "completion_tokens": 2,
        "total_tokens": 12 + expected,
        "prompt_tokens_details": {
            "cached_tokens": 0,
            "cache_creation_tokens": expected,
        },
    }
    assert (
        anthropic_response_to_chat({"usage": usage}, "model")["usage"] == expected_usage
    )
    events = [
        {"type": "message_start", "message": {"usage": usage}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    payloads = _chat_event_payloads(
        [
            chunk
            async for chunk in anthropic_stream_to_chat_stream(
                _anthropic_events(events), "model"
            )
        ]
    )
    assert [event["usage"] for event in payloads if "usage" in event] == [
        expected_usage
    ]


@pytest.mark.parametrize("metadata", [False, True])
@pytest.mark.parametrize("signature", ["opaque", ""])
@pytest.mark.asyncio
async def test_anthropic_empty_thinking_preserves_only_signed_blocks(
    metadata: bool, signature: str
) -> None:
    block = {"type": "thinking", "thinking": "", "signature": signature}
    reasoning = (
        {"reasoning_blocks": [block]}
        if metadata
        else {"reasoning_content": "", "reasoning_signature": signature}
    )
    message = {"role": "assistant", "content": "answer", **reasoning}
    expected = ([block] if signature else []) + [{"type": "text", "text": "answer"}]
    request = chat_request_to_anthropic({"messages": [message]})
    assert request["messages"][-1]["content"] == expected
    response = chat_response_to_anthropic({"choices": [{"message": message}]}, "model")
    assert response["content"] == expected
    events = [
        {"choices": [{"delta": reasoning}]},
        {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]},
    ]
    chunks = [
        chunk
        async for chunk in chat_stream_to_anthropic_stream(
            _anthropic_events(events), "model"
        )
    ]
    payloads = _chat_event_payloads(chunks)
    assert len([event for event in payloads if event["type"] == "message_stop"]) == 1
    starts = [
        event["index"] for event in payloads if event["type"] == "content_block_start"
    ]
    stops = [
        event["index"] for event in payloads if event["type"] == "content_block_stop"
    ]
    assert starts == stops
    assert len(starts) == len(expected)

    async def source():
        for chunk in chunks:
            yield chunk

    restored = _chat_event_payloads(
        [chunk async for chunk in anthropic_stream_to_chat_stream(source(), "model")]
    )
    blocks = [
        block
        for event in restored
        for block in event["choices"][0]["delta"].get("reasoning_blocks", [])
    ]
    assert blocks == ([block] if signature else [])


@pytest.mark.parametrize("reset_to_zero", [False, True])
@pytest.mark.asyncio
async def test_chat_to_anthropic_stream_preserves_tail_usage_snapshots(
    reset_to_zero: bool,
) -> None:
    chat_usage = {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 8, "cache_creation_tokens": 3},
        "completion_tokens_details": {"reasoning_tokens": 3},
    }
    final_usage = (
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 0, "cache_creation_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        if reset_to_zero
        else {"completion_tokens": 10}
    )
    expected_chat_usage = {**chat_usage, **final_usage}
    expected_chat_usage["total_tokens"] = (
        expected_chat_usage["prompt_tokens"] + expected_chat_usage["completion_tokens"]
    )
    expected_anthropic_usage = {
        "input_tokens": 0 if reset_to_zero else 9,
        "output_tokens": 0 if reset_to_zero else 10,
        "cache_read_input_tokens": 0 if reset_to_zero else 8,
        "cache_creation_input_tokens": 0 if reset_to_zero else 3,
        "output_tokens_details": {"thinking_tokens": 0 if reset_to_zero else 3},
    }
    drained = False

    async def source():
        nonlocal drained
        events = [
            {"choices": [{"delta": {"content": "answer"}}], "usage": chat_usage},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": final_usage},
        ]
        async for chunk in _anthropic_events(events):
            yield chunk
        yield b"data: [DONE]" + bytes([10, 10])
        drained = True

    chunks = [
        chunk async for chunk in chat_stream_to_anthropic_stream(source(), "model")
    ]
    assert drained
    payloads = _chat_event_payloads(chunks)
    assert [
        event["usage"] for event in payloads if event["type"] == "message_delta"
    ] == [expected_anthropic_usage]
    assert len([event for event in payloads if event["type"] == "message_stop"]) == 1
    assert (
        len([event for event in payloads if event["type"] == "content_block_stop"]) == 1
    )
    response = chat_response_to_anthropic({"usage": expected_chat_usage}, "model")
    assert response["usage"] == expected_anthropic_usage

    async def converted_source():
        for chunk in chunks:
            yield chunk

    restored_chunks = [
        chunk
        async for chunk in anthropic_stream_to_chat_stream(converted_source(), "model")
    ]
    restored = _chat_event_payloads(restored_chunks)
    assert [event["usage"] for event in restored if "usage" in event] == [
        expected_chat_usage
    ]
    assert (
        "".join(event["choices"][0]["delta"].get("content", "") for event in restored)
        == "answer"
    )
    assert (
        sum(bool(event["choices"][0].get("finish_reason")) for event in restored) == 1
    )
    assert b"".join(restored_chunks).count(b"data: [DONE]") == 1


@pytest.mark.asyncio
async def test_anthropic_empty_thinking_with_split_pending_signature() -> None:
    events = [
        {"choices": [{"delta": {"reasoning_content": ""}}]},
        {"choices": [{"delta": {"reasoning_signature": "sig-"}}]},
        {"choices": [{"delta": {"reasoning_signature": "only"}}]},
        {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]},
    ]
    chunks = [
        chunk
        async for chunk in chat_stream_to_anthropic_stream(
            _anthropic_events(events), "model"
        )
    ]
    payloads = _chat_event_payloads(chunks)
    assert [
        event["delta"]["signature"]
        for event in payloads
        if event.get("delta", {}).get("type") == "signature_delta"
    ] == ["sig-only"]
    starts = [
        event["index"] for event in payloads if event["type"] == "content_block_start"
    ]
    stops = [
        event["index"] for event in payloads if event["type"] == "content_block_stop"
    ]
    assert starts == stops
    assert len(starts) == 2

    async def source():
        for chunk in chunks:
            yield chunk

    restored = _chat_event_payloads(
        [chunk async for chunk in anthropic_stream_to_chat_stream(source(), "model")]
    )
    assert [
        block
        for event in restored
        for block in event["choices"][0]["delta"].get("reasoning_blocks", [])
    ] == [{"type": "thinking", "thinking": "", "signature": "sig-only"}]
