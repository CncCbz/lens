from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest

from lens_api.gateway.converters._shared import _parse_sse_stream
from lens_api.gateway.converters.chat_to_responses import (
    responses_stream_to_chat_stream,
)
from lens_api.gateway.converters.provider_variants import (
    chat_request_to_mistral,
    chat_response_to_mistral,
    chat_stream_to_mistral_stream,
    codex_stream_to_responses_stream,
    mistral_request_to_chat,
    mistral_response_to_chat,
    mistral_stream_to_chat_stream,
    normalize_azure_responses_request,
    normalize_codex_request,
)


async def _source(
    payloads: list[dict[str, Any]], *, done: bool = True
) -> AsyncIterator[bytes]:
    wire = b"".join(
        f"data: {json.dumps(payload, ensure_ascii=False)}\r\n\r\n".encode()
        for payload in payloads
    )
    if done:
        wire += b"data: [DONE]\r\n\r\n"
    for offset in range(0, len(wire), 7):
        yield wire[offset : offset + 7]


async def _collect(iterator: AsyncIterator[bytes]) -> list[dict[str, Any]]:
    return [payload async for payload in _parse_sse_stream(iterator)]


def _run(iterator: AsyncIterator[bytes]) -> list[dict[str, Any]]:
    return asyncio.run(_collect(iterator))


def _choice(
    delta: dict[str, Any], finish: str | None = None, index: int = 0
) -> dict[str, Any]:
    return {"index": index, "delta": delta, "finish_reason": finish}


def _thinking(text: str, **extra: Any) -> dict[str, Any]:
    return {"type": "thinking", "thinking": [{"type": "text", "text": text}], **extra}


def test_mistral_request_thinking_images_options_and_no_mutation() -> None:
    body = {
        "model": "any-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": "data:image/png;base64,AA=="}
                ],
            },
            {
                "role": "assistant",
                "content": [
                    _thinking("分析", signature=""),
                    {"type": "text", "text": "visible"},
                    _thinking("", signature="signed"),
                ],
            },
        ],
        "prompt_mode": "reasoning",
        "reasoning_effort": "none",
        "safe_prompt": False,
        "stream": False,
    }
    original = copy.deepcopy(body)
    chat = mistral_request_to_chat(body)
    message = chat["messages"][1]
    assert message["content"] == "visible"
    assert message["reasoning_content"] == "分析"
    assert [block["signature"] for block in message["reasoning_blocks"]] == [
        "",
        "signed",
    ]
    assert chat["messages"][0]["content"][0]["image_url"] == {
        "url": "data:image/png;base64,AA=="
    }
    native = chat_request_to_mistral(chat)
    assert native == body
    assert body == original


def test_mistral_chat_reasoning_empty_signature_and_effort_are_per_request() -> None:
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "",
                "reasoning_signature": "",
            }
        ],
        "reasoning": {"effort": " HIGH "},
    }
    native = chat_request_to_mistral(body)
    assert native["reasoning_effort"] == "high"
    assert "reasoning" not in native
    assert "prompt_mode" not in native
    assert native["messages"][0]["content"] == [
        _thinking("", signature=""),
        {"type": "text", "text": ""},
    ]
    chat = mistral_request_to_chat(native)
    assert chat["messages"][0]["reasoning_content"] == ""
    assert chat["messages"][0]["reasoning_signature"] == ""
    assert "reasoning_effort" not in chat_request_to_mistral({"messages": []})


def test_mistral_tool_ids_are_deterministic_collision_safe_and_paired() -> None:
    identifiers = ["abcdefghi", "abc-defghi", "abc_defghi", "long-tool-call-id", "!!!"]
    calls = [
        {
            "id": identifier,
            "type": "function",
            "function": {"name": "f", "arguments": {"x": 1}},
        }
        for identifier in identifiers
    ]
    body = {
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": calls},
            *[
                {"role": "tool", "tool_call_id": identifier, "content": "ok"}
                for identifier in reversed(identifiers)
            ],
        ]
    }
    original = copy.deepcopy(body)
    native = chat_request_to_mistral(body)
    ids = [call["id"] for call in native["messages"][0]["tool_calls"]]
    assert len(set(ids)) == len(identifiers)
    assert all(re.fullmatch(r"[A-Za-z0-9]{9}", identifier) for identifier in ids)
    assert ids[0] == identifiers[0]
    assert [message["tool_call_id"] for message in native["messages"][1:]] == list(
        reversed(ids)
    )
    assert json.loads(
        native["messages"][0]["tool_calls"][0]["function"]["arguments"]
    ) == {"x": 1}
    assert native == chat_request_to_mistral(body)
    assert body == original


@pytest.mark.parametrize(
    "finish, expected",
    [
        ("stop", "stop"),
        ("model_length", "length"),
        ("length", "length"),
        ("tool_calls", "tool_calls"),
        (None, None),
    ],
)
def test_mistral_responses_preserve_envelope_and_real_finish(
    finish: str | None, expected: str | None
) -> None:
    body = {
        "id": "r",
        "model": "upstream",
        "created": 1,
        "usage": {"prompt_tokens": 4},
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": [
                        _thinking("secret"),
                        {"type": "text", "text": "public"},
                    ],
                },
                "finish_reason": finish,
            }
        ],
    }
    original = copy.deepcopy(body)
    chat = mistral_response_to_chat(body, "client-model")
    assert chat["model"] == "client-model"
    assert chat["choices"][0]["message"]["content"] == "public"
    assert chat["choices"][0]["message"]["reasoning_content"] == "secret"
    assert chat["choices"][0]["finish_reason"] == expected
    native = chat_response_to_mistral(chat)
    assert native["choices"][0]["message"] == body["choices"][0]["message"]
    assert native["usage"] == body["usage"]
    assert body == original


@pytest.mark.parametrize(
    "converter", [mistral_response_to_chat, chat_response_to_mistral]
)
@pytest.mark.parametrize("finish", ["error", "unexpected"])
def test_mistral_response_errors_raise(converter: Any, finish: str) -> None:
    with pytest.raises(ValueError, match="finish"):
        converter({"choices": [{"message": {}, "finish_reason": finish}]})
    with pytest.raises(ValueError, match="error"):
        converter({"error": {"message": "bad"}})


@pytest.mark.parametrize(
    "converter", [mistral_stream_to_chat_stream, chat_stream_to_mistral_stream]
)
def test_mistral_stream_requires_finish_and_drains_source(converter: Any) -> None:
    drained: list[bool] = []

    async def source() -> AsyncIterator[bytes]:
        async for chunk in _source([{"choices": [_choice({"content": "hi"}, "stop")]}]):
            yield chunk
        drained.append(True)

    payloads = _run(converter(source(), "m"))
    assert payloads[0]["choices"][0]["finish_reason"] == "stop"
    assert drained == [True]
    with pytest.raises(ValueError, match="finish"):
        _run(converter(_source([{"choices": [_choice({"content": "partial"})]}])))
    with pytest.raises(ValueError, match="finish"):
        _run(converter(_source([])))


@pytest.mark.parametrize(
    "converter", [mistral_stream_to_chat_stream, chat_stream_to_mistral_stream]
)
@pytest.mark.parametrize(
    "payload",
    [
        {"error": {"message": "broken"}},
        {"type": "error", "message": "broken"},
        {"choices": [_choice({}, "error")]},
        {"choices": [_choice({}, "unknown")]},
    ],
)
def test_mistral_stream_errors_are_not_success(
    converter: Any, payload: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        _run(converter(_source([payload])))


def test_mistral_stream_preserves_reasoning_empty_blocks_usage_and_fragmented_tools() -> (
    None
):
    chunks = [
        {
            "id": "r",
            "choices": [
                _choice(
                    {
                        "content": [
                            _thinking("", signature=""),
                            _thinking("思考"),
                            {"type": "text", "text": "answer"},
                        ]
                    }
                )
            ],
        },
        {
            "choices": [
                _choice(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "long-id",
                                "function": {"name": "f", "arguments": '{"x":'},
                            }
                        ]
                    }
                )
            ]
        },
        {
            "choices": [
                _choice(
                    {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]},
                    "model_length",
                )
            ]
        },
        {"choices": [], "usage": {"total_tokens": 10}},
    ]
    chat = _run(mistral_stream_to_chat_stream(_source(chunks), "client"))
    delta = chat[0]["choices"][0]["delta"]
    assert delta["content"] == "answer"
    assert delta["reasoning_content"] == "思考"
    assert len(delta["reasoning_blocks"]) == 2
    assert delta["reasoning_blocks"][0]["signature"] == ""
    assert chat[2]["choices"][0]["finish_reason"] == "length"
    assert chat[-1]["usage"] == {"total_tokens": 10}
    native = _run(chat_stream_to_mistral_stream(_source(chat), "client"))
    assert (
        native[0]["choices"][0]["delta"]["content"]
        == chunks[0]["choices"][0]["delta"]["content"]
    )
    assert re.fullmatch(
        r"[A-Za-z0-9]{9}", native[1]["choices"][0]["delta"]["tool_calls"][0]["id"]
    )
    assert "id" not in native[2]["choices"][0]["delta"]["tool_calls"][0]
    assert native[-1]["usage"] == {"total_tokens": 10}


def test_mistral_stream_all_choices_must_finish() -> None:
    with pytest.raises(ValueError, match="finish"):
        _run(
            mistral_stream_to_chat_stream(
                _source(
                    [
                        {
                            "choices": [
                                _choice({}, "stop"),
                                _choice({"content": "partial"}, index=1),
                            ]
                        }
                    ]
                )
            )
        )


def test_codex_request_preserves_native_items_and_tools_without_mutation() -> None:
    body = {
        "model": "codex",
        "stream": False,
        "store": True,
        "instructions": "existing",
        "max_output_tokens": 100,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": "system"}]},
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
                "encrypted_content": "encrypted",
            },
            {
                "type": "custom_tool_call",
                "id": "ctc_1",
                "call_id": "c",
                "name": "patch",
                "input": "raw patch",
            },
            {"type": "custom_tool_call_output", "call_id": "c", "output": "ok"},
            {"role": "developer", "content": "later instruction"},
            {"type": "configuration_update", "reasoning": {"effort": " HIGH "}},
        ],
        "tools": [{"type": "custom", "name": "patch", "format": {"type": "text"}}],
        "reasoning": {"effort": "medium", "summary": "concise"},
        "include": ["other.field"],
        "text": {"format": {"type": "text"}},
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "prompt_cache_key": "session",
        "temperature": 0.4,
    }
    original = copy.deepcopy(body)
    result = normalize_codex_request(body)
    assert result["store"] is False
    assert result["stream"] is True
    assert result["instructions"] == "existing\n\nsystem"
    assert result["input"] == body["input"][1:]
    assert result["tools"] == body["tools"]
    assert result["reasoning"] == {"effort": "high", "summary": "concise"}
    assert result["include"] == ["other.field", "reasoning.encrypted_content"]
    assert result["text"] == body["text"]
    assert result["parallel_tool_calls"] is False
    assert result["tool_choice"] == "required"
    assert result["temperature"] == 0.4
    assert "max_output_tokens" not in result
    assert result == normalize_codex_request(result)
    assert body == original


@pytest.mark.parametrize(
    "input_value, expected",
    [("hello", [{"role": "user", "content": "hello"}]), ([], [])],
)
def test_codex_request_minimum_defaults(
    input_value: Any, expected: list[dict[str, Any]]
) -> None:
    result = normalize_codex_request({"model": "codex", "input": input_value})
    assert result["input"] == expected
    assert result["instructions"] == "You are a helpful assistant."
    assert result["include"] == ["reasoning.encrypted_content"]
    assert result["stream"] is True
    assert result["store"] is False


def test_azure_normalizer_reuses_configuration_effort_without_transport_changes() -> (
    None
):
    body = {
        "model": "deployment",
        "stream": False,
        "store": True,
        "reasoning": {"summary": "auto"},
        "input": [{"type": "configuration_update", "reasoning": {"effort": "low"}}],
        "tools": [{"type": "custom", "name": "t"}],
    }
    original = copy.deepcopy(body)
    result = normalize_azure_responses_request(body)
    assert result == {**body, "reasoning": {"summary": "auto", "effort": "low"}}
    assert body == original


@pytest.mark.parametrize(
    "terminal, status, expected",
    [
        ("response.done", "completed", "response.completed"),
        ("response.done", "incomplete", "response.incomplete"),
        ("response.completed", "completed", "response.completed"),
        ("response.incomplete", "incomplete", "response.incomplete"),
        ("response.done", None, "response.completed"),
    ],
)
def test_codex_terminal_normalizer_reuses_responses_converter(
    terminal: str, status: str | None, expected: str
) -> None:
    response = {
        "id": "r",
        "model": "codex",
        "output": [
            {
                "id": "rs_1",
                "type": "reasoning",
                "summary": [],
                "encrypted_content": "secret",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            },
        ],
        "usage": {"input_tokens": 2, "output_tokens": 3},
        "end_turn": False,
    }
    if status:
        response["status"] = status
    if status == "incomplete":
        response["incomplete_details"] = {"reason": "max_output_tokens"}
    payloads = _run(
        codex_stream_to_responses_stream(
            _source([{"type": terminal, "response": response}]), "client"
        )
    )
    assert payloads[0]["type"] == expected
    assert payloads[0]["response"]["end_turn"] is False
    assert payloads[0]["response"]["output"] == response["output"]
    chat = _run(responses_stream_to_chat_stream(_source(payloads), "client"))
    assert chat[-1]["choices"][0]["finish_reason"] == (
        "length" if status == "incomplete" else "stop"
    )
    assert (
        chat[-1]["choices"][0]["delta"]["_responses_reasoning_items"][0][
            "encrypted_content"
        ]
        == "secret"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "error", "message": "broken"},
        {"type": "response.failed", "response": {"error": {"message": "broken"}}},
        {"type": "response.done", "response": {"status": "failed"}},
        {"type": "response.done", "response": {"status": "in_progress"}},
        {"type": "response.done"},
        {"type": "response.created", "response": {"id": "r"}},
    ],
)
def test_codex_sse_errors_and_missing_terminal_raise(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _run(codex_stream_to_responses_stream(_source([payload])))


def test_mistral_signature_on_existing_reasoning_block_is_retained() -> None:
    native = chat_request_to_mistral(
        {
            "messages": [
                {
                    "role": "assistant",
                    "reasoning_blocks": [{"type": "thinking", "thinking": ""}],
                    "reasoning_signature": "",
                    "content": None,
                }
            ]
        }
    )
    assert native["messages"][0]["content"] == [_thinking("", signature="")]


def test_mistral_signature_with_null_reasoning_is_retained() -> None:
    native = chat_request_to_mistral(
        {
            "messages": [
                {
                    "role": "assistant",
                    "reasoning_content": None,
                    "reasoning_signature": "signed",
                    "content": None,
                }
            ]
        }
    )
    assert native["messages"][0]["content"] == [_thinking("", signature="signed")]


def test_mistral_replay_respects_edited_visible_content() -> None:
    chat = mistral_request_to_chat(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [_thinking("secret"), {"type": "text", "text": "old"}],
                }
            ]
        }
    )
    chat["messages"][0]["content"] = "new"
    native = chat_request_to_mistral(chat)
    assert native["messages"][0]["content"] == [
        _thinking("secret"),
        {"type": "text", "text": "new"},
    ]


@pytest.mark.parametrize(
    "converter",
    [
        mistral_stream_to_chat_stream,
        chat_stream_to_mistral_stream,
        codex_stream_to_responses_stream,
    ],
)
def test_provider_stream_malformed_json_raises(converter: Any) -> None:
    async def source() -> AsyncIterator[bytes]:
        yield b"data: {broken}\n\n"

    with pytest.raises(ValueError, match="JSON"):
        _run(converter(source()))


@pytest.mark.parametrize(
    "converter", [mistral_stream_to_chat_stream, chat_stream_to_mistral_stream]
)
def test_mistral_stream_rejects_error_shaped_payload_after_finish(
    converter: Any,
) -> None:
    with pytest.raises(ValueError):
        _run(
            converter(
                _source(
                    [
                        {"choices": [_choice({}, "stop")]},
                        {"message": "upstream failed"},
                    ]
                )
            )
        )


def test_mistral_stream_assigns_indices_to_parallel_calls() -> None:
    native = _run(
        mistral_stream_to_chat_stream(
            _source(
                [
                    {
                        "choices": [
                            _choice(
                                {
                                    "tool_calls": [
                                        {
                                            "id": "abcdefghi",
                                            "function": {
                                                "name": "a",
                                                "arguments": "{}",
                                            },
                                        },
                                        {
                                            "id": "jklmnopqr",
                                            "function": {
                                                "name": "b",
                                                "arguments": "{}",
                                            },
                                        },
                                    ]
                                },
                                "tool_calls",
                            )
                        ]
                    }
                ]
            )
        )
    )
    assert [
        call["index"] for call in native[0]["choices"][0]["delta"]["tool_calls"]
    ] == [0, 1]
