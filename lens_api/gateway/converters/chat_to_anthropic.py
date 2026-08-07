import json
import time
import uuid
from typing import Any, AsyncIterator

from ._shared import (
    FINISH_REASON_CHAT_TO_ANTHROPIC,
    _parse_chat_sse_stream,
    anthropic_content_to_chat_messages,
    anthropic_tool_choice_to_chat,
    anthropic_tools_to_chat_tools,
    chat_tool_calls_to_anthropic_content,
    format_sse_event,
)

_ANTHROPIC_STOP_REASON_TO_CHAT: dict[str | None, str] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}
_DEFAULT_ANTHROPIC_MAX_TOKENS = 4096


def anthropic_request_to_chat(
    body: dict[str, Any], *, preserve_thinking: bool = False
) -> dict[str, Any]:
    chat: dict[str, Any] = {}

    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            system_message = anthropic_content_to_chat_messages(
                [{"role": "system", "content": system}]
            )
            messages.extend(system_message)

    src_messages = body.get("messages", [])
    messages.extend(
        anthropic_content_to_chat_messages(
            src_messages, preserve_thinking=preserve_thinking
        )
    )
    chat["messages"] = messages

    if "max_tokens" in body:
        chat["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            chat[key] = body[key]
    if "stop_sequences" in body:
        chat["stop"] = body["stop_sequences"]

    if "tools" in body:
        chat["tools"] = anthropic_tools_to_chat_tools(body["tools"])
    if "tool_choice" in body:
        tc = anthropic_tool_choice_to_chat(body["tool_choice"])
        if tc is not None:
            chat["tool_choice"] = tc
    cache_control = body.get("cache_control")
    if isinstance(cache_control, dict):
        chat["cache_control"] = dict(cache_control)

    return chat


def chat_request_to_anthropic(body: dict[str, Any]) -> dict[str, Any]:
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Chat request messages must be a list")

    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    system_block_groups: list[list[dict[str, Any]]] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role") or "user")
        if role in {"system", "developer"}:
            content = raw_message.get("content")
            system_text = _chat_content_to_text(content)
            if system_text:
                system_parts.append(system_text)
            system_blocks = _chat_system_content_to_anthropic_blocks(content)
            _apply_message_cache_control(system_blocks, raw_message)
            if system_blocks:
                system_block_groups.append(system_blocks)
            continue
        if role == "tool":
            tool_result = {
                "type": "tool_result",
                "tool_use_id": str(raw_message.get("tool_call_id") or ""),
                "content": _chat_content_to_text(raw_message.get("content")) or "...",
            }
            _copy_cache_control(tool_result, raw_message)
            if "cache_control" not in tool_result:
                _copy_cache_control_from_content(
                    tool_result, raw_message.get("content")
                )
            _append_anthropic_message(messages, "user", [tool_result])
            continue

        anthropic_role = "assistant" if role == "assistant" else "user"
        blocks = _chat_content_to_anthropic_blocks(raw_message.get("content"))
        tool_calls = raw_message.get("tool_calls")
        if anthropic_role == "assistant" and isinstance(tool_calls, list):
            blocks.extend(chat_tool_calls_to_anthropic_content(tool_calls))
        _apply_message_cache_control(blocks, raw_message)
        _append_anthropic_message(messages, anthropic_role, blocks)

    if not messages or messages[0].get("role") != "user":
        messages.insert(
            0, {"role": "user", "content": [{"type": "text", "text": "..."}]}
        )

    anthropic: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages,
        "max_tokens": _chat_max_tokens(body),
    }
    if system_block_groups and _blocks_have_cache_control(system_block_groups):
        anthropic["system"] = _join_system_block_groups(system_block_groups)
    elif system_parts:
        anthropic["system"] = "\n\n".join(system_parts)
    cache_control = body.get("cache_control")
    if isinstance(cache_control, dict):
        anthropic["cache_control"] = dict(cache_control)
    for key in ("temperature", "top_p", "top_k", "stream"):
        if key in body:
            anthropic[key] = body[key]
    if "stop" in body:
        stop = body["stop"]
        anthropic["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    tools = _chat_tools_to_anthropic_tools(body.get("tools"))
    if tools:
        anthropic["tools"] = tools
    tool_choice = _chat_tool_choice_to_anthropic(body.get("tool_choice"))
    if tool_choice is not None:
        anthropic["tool_choice"] = tool_choice
    return anthropic


def chat_response_to_anthropic(
    chat_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason")
    stop_reason = FINISH_REASON_CHAT_TO_ANTHROPIC.get(finish_reason, "end_turn")

    content: list[dict[str, Any]] = []
    has_reasoning, reasoning = _chat_message_reasoning_content(message)
    if has_reasoning:
        content.append({"type": "thinking", "thinking": reasoning})
    text = message.get("content")
    if text:
        content.append({"type": "text", "text": text})
    tool_calls = message.get("tool_calls")
    if tool_calls:
        content.extend(chat_tool_calls_to_anthropic_content(tool_calls))

    usage = chat_body.get("usage", {})
    return {
        "id": chat_body.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "model": chat_body.get("model", original_model),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def anthropic_response_to_chat(
    anthropic_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in anthropic_body.get("content") or []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                content_parts.append(text)
        elif block_type == "thinking":
            thinking = block.get("thinking")
            if isinstance(thinking, str):
                reasoning_parts.append(thinking)
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(
                            block.get("input") or {}, ensure_ascii=False
                        ),
                    },
                }
            )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) if not tool_calls else None,
    }
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": anthropic_body.get("id") or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": anthropic_body.get("model") or original_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _anthropic_stop_reason_to_chat(
                    anthropic_body.get("stop_reason")
                ),
            }
        ],
        "usage": _anthropic_usage_to_chat_usage(anthropic_body.get("usage")),
    }


async def chat_stream_to_anthropic_stream(
    raw_iterator: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    output_tokens = 0
    text_started = False
    thinking_index: int | None = None
    tool_index: dict[str, int] = {}
    next_block_index = 0
    finish_reason: str | None = None

    yield format_sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": original_model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    yield format_sse_event("ping", {"type": "ping"})

    async for payload in _parse_chat_sse_stream(raw_iterator):
        usage = payload.get("usage") or {}
        if usage.get("completion_tokens"):
            output_tokens = usage["completion_tokens"]

        for choice in payload.get("choices", []):
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta", {})
            reasoning_delta = _chat_delta_reasoning_content(delta)
            text_delta = delta.get("content")
            tc_deltas = delta.get("tool_calls")

            if reasoning_delta is not None:
                if thinking_index is None:
                    thinking_index = next_block_index
                    next_block_index += 1
                    yield format_sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": thinking_index,
                            "content_block": {"type": "thinking", "thinking": ""},
                        },
                    )
                if reasoning_delta:
                    yield format_sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": thinking_index,
                            "delta": {
                                "type": "thinking_delta",
                                "thinking": reasoning_delta,
                            },
                        },
                    )

            if text_delta:
                if not text_started:
                    text_started = True
                    yield format_sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": next_block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                    next_block_index += 1
                yield format_sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": next_block_index - 1,
                        "delta": {"type": "text_delta", "text": text_delta},
                    },
                )

            if tc_deltas:
                for tc in tc_deltas:
                    call_id = tc.get("id") or ""
                    tc_idx = tc.get("index", 0)
                    key = call_id or str(tc_idx)
                    if key not in tool_index:
                        func = tc.get("function", {})
                        tool_index[key] = next_block_index
                        next_block_index += 1
                        yield format_sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": tool_index[key],
                                "content_block": {
                                    "type": "tool_use",
                                    "id": call_id or f"toolu_{uuid.uuid4().hex[:24]}",
                                    "name": func.get("name", ""),
                                    "input": {},
                                },
                            },
                        )
                    args_delta = (tc.get("function") or {}).get("arguments", "")
                    if args_delta:
                        yield format_sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": tool_index[key],
                                "delta": {
                                    "type": "input_json_delta",
                                    "partial_json": args_delta,
                                },
                            },
                        )

    for i in range(next_block_index):
        yield format_sse_event(
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": i,
            },
        )

    stop_reason = FINISH_REASON_CHAT_TO_ANTHROPIC.get(finish_reason, "end_turn")
    yield format_sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield format_sse_event("message_stop", {"type": "message_stop"})


async def anthropic_stream_to_chat_stream(
    raw_iterator: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    message_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    model = original_model
    created = int(time.time())
    block_types: dict[int, str] = {}
    tool_call_indexes: dict[int, int] = {}
    next_tool_call_index = 0
    usage: dict[str, Any] = {}
    finish_reason: str | None = None
    finish_sent = False
    done_sent = False

    async for payload in _parse_anthropic_sse_stream(raw_iterator):
        if done_sent:
            continue
        payload_type = str(payload.get("type") or "")
        if payload_type == "message_start":
            message = payload.get("message")
            if isinstance(message, dict):
                message_id = str(message.get("id") or message_id)
                model = str(message.get("model") or model)
                _merge_usage(usage, message.get("usage"))
            yield _chat_stream_event(message_id, model, created, {"role": "assistant"})
            continue

        if payload_type == "content_block_start":
            index = _coerce_index(payload.get("index"))
            block = payload.get("content_block")
            if index is None or not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            block_types[index] = block_type
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    yield _chat_stream_event(
                        message_id, model, created, {"content": text}
                    )
            elif block_type == "thinking":
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking:
                    yield _chat_stream_event(
                        message_id, model, created, {"reasoning_content": thinking}
                    )
            elif block_type == "tool_use":
                tool_call_indexes[index] = next_tool_call_index
                next_tool_call_index += 1
                yield _chat_stream_event(
                    message_id,
                    model,
                    created,
                    {
                        "tool_calls": [
                            {
                                "index": tool_call_indexes[index],
                                "id": block.get("id")
                                or f"call_{uuid.uuid4().hex[:24]}",
                                "type": "function",
                                "function": {
                                    "name": block.get("name") or "",
                                    "arguments": "",
                                },
                            }
                        ]
                    },
                )
            continue

        if payload_type == "content_block_delta":
            index = _coerce_index(payload.get("index"))
            delta = payload.get("delta")
            if index is None or not isinstance(delta, dict):
                continue
            delta_type = str(delta.get("type") or "")
            if delta_type == "text_delta":
                text = delta.get("text")
                if isinstance(text, str) and text:
                    yield _chat_stream_event(
                        message_id, model, created, {"content": text}
                    )
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking")
                if isinstance(thinking, str) and thinking:
                    yield _chat_stream_event(
                        message_id, model, created, {"reasoning_content": thinking}
                    )
            elif (
                delta_type == "input_json_delta"
                and block_types.get(index) == "tool_use"
            ):
                arguments = delta.get("partial_json")
                if isinstance(arguments, str) and arguments:
                    yield _chat_stream_event(
                        message_id,
                        model,
                        created,
                        {
                            "tool_calls": [
                                {
                                    "index": tool_call_indexes.get(index, 0),
                                    "function": {"arguments": arguments},
                                }
                            ]
                        },
                    )
            continue

        if payload_type == "message_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                finish_reason = _anthropic_stop_reason_to_chat(delta.get("stop_reason"))
            _merge_usage(usage, payload.get("usage"))
            if finish_reason:
                yield _chat_stream_event(
                    message_id,
                    model,
                    created,
                    {},
                    finish_reason=finish_reason,
                    usage=_anthropic_usage_to_chat_usage(usage),
                )
                finish_sent = True
            continue

        if payload_type == "message_stop":
            if not finish_sent:
                yield _chat_stream_event(
                    message_id,
                    model,
                    created,
                    {},
                    finish_reason=finish_reason or "stop",
                    usage=_anthropic_usage_to_chat_usage(usage),
                )
            yield b"data: [DONE]\n\n"
            done_sent = True
            continue

    if not done_sent:
        if not finish_sent:
            yield _chat_stream_event(
                message_id,
                model,
                created,
                {},
                finish_reason=finish_reason or "stop",
                usage=_anthropic_usage_to_chat_usage(usage),
            )
        yield b"data: [DONE]\n\n"


def _chat_message_reasoning_content(message: dict[str, Any]) -> tuple[bool, str]:
    for key in ("reasoning_content", "reasoning"):
        if key not in message:
            continue
        value = message.get(key)
        if isinstance(value, str):
            return True, value
    return False, ""


def _chat_delta_reasoning_content(delta: dict[str, Any]) -> str | None:
    for key in ("reasoning_content", "reasoning"):
        if key not in delta:
            continue
        value = delta.get(key)
        if isinstance(value, str):
            return value
    return None


def _chat_max_tokens(body: dict[str, Any]) -> Any:
    for key in ("max_tokens", "max_completion_tokens"):
        value = body.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
    return _DEFAULT_ANTHROPIC_MAX_TOKENS


def _append_anthropic_message(
    messages: list[dict[str, Any]], role: str, blocks: list[dict[str, Any]]
) -> None:
    normalized_blocks = blocks or [{"type": "text", "text": "..."}]
    if messages and messages[-1].get("role") == role:
        previous = messages[-1].setdefault("content", [])
        if isinstance(previous, list):
            previous.extend(normalized_blocks)
            return
    messages.append({"role": role, "content": normalized_blocks})


def _chat_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _chat_content_to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content or "..."}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]

    blocks: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            if item:
                blocks.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str) and text:
                block = {"type": "text", "text": text}
                _copy_cache_control(block, item)
                blocks.append(block)
        elif item_type == "image_url":
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str) and url:
                block = _image_url_to_anthropic_block(url)
                _copy_cache_control(block, item)
                blocks.append(block)
        elif item_type == "image" and isinstance(item.get("source"), dict):
            block = {"type": "image", "source": item["source"]}
            _copy_cache_control(block, item)
            blocks.append(block)
    return blocks


def _chat_system_content_to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    return [
        block
        for block in _chat_content_to_anthropic_blocks(content)
        if block.get("type") == "text"
    ]


def _blocks_have_cache_control(block_groups: list[list[dict[str, Any]]]) -> bool:
    return any(
        isinstance(block.get("cache_control"), dict)
        for block_group in block_groups
        for block in block_group
    )


def _join_system_block_groups(
    block_groups: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block_group in block_groups:
        if blocks:
            blocks.append({"type": "text", "text": "\n\n"})
        blocks.extend(block_group)
    return blocks


def _copy_cache_control(target: dict[str, Any], source: dict[str, Any]) -> None:
    cache_control = source.get("cache_control")
    if isinstance(cache_control, dict):
        target["cache_control"] = dict(cache_control)


def _copy_cache_control_from_content(target: dict[str, Any], content: Any) -> None:
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        _copy_cache_control(target, item)
        if "cache_control" in target:
            return


def _apply_message_cache_control(
    blocks: list[dict[str, Any]], message: dict[str, Any]
) -> None:
    if not blocks or _blocks_have_cache_control([blocks]):
        return
    _copy_cache_control(blocks[-1], message)


def _image_url_to_anthropic_block(url: str) -> dict[str, Any]:
    if url.startswith("data:") and ";base64," in url:
        metadata, data = url[5:].split(",", 1)
        media_type = metadata.split(";", 1)[0] or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _chat_tools_to_anthropic_tools(raw_tools: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tools, list):
        return []
    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        entry: dict[str, Any] = {
            "name": function.get("name") or "",
            "input_schema": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
        if function.get("description") is not None:
            entry["description"] = function["description"]
        _copy_cache_control(entry, tool)
        if "cache_control" not in entry:
            _copy_cache_control(entry, function)
        tools.append(entry)
    return tools


def _chat_tool_choice_to_anthropic(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice in {"auto", "none"}:
            return {"type": tool_choice}
        return None
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if tool_choice.get("type") == "function" and isinstance(function, dict):
            return {"type": "tool", "name": function.get("name") or ""}
    return None


def _anthropic_stop_reason_to_chat(stop_reason: Any) -> str:
    reason = stop_reason if isinstance(stop_reason, str) else None
    return _ANTHROPIC_STOP_REASON_TO_CHAT.get(reason, "stop")


def _anthropic_usage_to_chat_usage(raw_usage: Any) -> dict[str, Any]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = _int_value(usage.get("input_tokens"))
    cache_read = _int_value(usage.get("cache_read_input_tokens"))
    cache_creation = _int_value(usage.get("cache_creation_input_tokens"))
    cache_creation_obj = usage.get("cache_creation")
    if isinstance(cache_creation_obj, dict):
        cache_creation += _int_value(
            cache_creation_obj.get("ephemeral_5m_input_tokens")
        )
        cache_creation += _int_value(
            cache_creation_obj.get("ephemeral_1h_input_tokens")
        )
    prompt_tokens = input_tokens + cache_read + cache_creation
    completion_tokens = _int_value(usage.get("output_tokens"))
    result: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cache_read or cache_creation:
        result["prompt_tokens_details"] = {
            "cached_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
        }
    return result


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _merge_usage(target: dict[str, Any], raw_usage: Any) -> None:
    if not isinstance(raw_usage, dict):
        return
    for key, value in raw_usage.items():
        if isinstance(value, int) and not isinstance(value, bool):
            target[key] = _int_value(target.get(key)) + max(value, 0)
        elif isinstance(value, dict):
            current = target.setdefault(key, {})
            if isinstance(current, dict):
                _merge_usage(current, value)
        elif key not in target:
            target[key] = value


def _coerce_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _chat_stream_event(
    message_id: str,
    model: str,
    created: int,
    delta: dict[str, Any],
    *,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "id": message_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return format_sse_event(None, payload)


async def _parse_anthropic_sse_stream(
    raw_iterator: AsyncIterator[bytes],
) -> AsyncIterator[dict[str, Any]]:
    buffer = b""
    async for chunk in raw_iterator:
        buffer += chunk
        while b"\n\n" in buffer:
            block, buffer = buffer.split(b"\n\n", 1)
            payload = _parse_anthropic_sse_block(block)
            if payload is not None:
                yield payload
    if buffer:
        payload = _parse_anthropic_sse_block(buffer)
        if payload is not None:
            yield payload


def _parse_anthropic_sse_block(block: bytes) -> dict[str, Any] | None:
    data_parts: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data_parts.append(line[5:].strip())
    if not data_parts:
        return None
    data = "\n".join(data_parts)
    if data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid stream JSON") from exc
    return payload if isinstance(payload, dict) else None
