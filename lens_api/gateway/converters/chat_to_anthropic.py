import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from ._chat_stream import ChatToolCalls, chat_choice_index
from ._shared import (
    _normalize_anthropic_tool_id,
    FINISH_REASON_CHAT_TO_ANTHROPIC,
    _anthropic_budget_to_effort,
    _clean_thinking_effort,
    _effort_to_anthropic_budget,
    _extract_chat_reasoning_effort,
    _parse_sse_stream,
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
    # pi reports a refusal as an error; Chat can carry the reason as content_filter.
    "refusal": "content_filter",
    "sensitive": "content_filter",
    # The caller should resubmit the turn; Chat has no dedicated member for it.
    "pause_turn": "stop",
}
_DEFAULT_ANTHROPIC_MAX_TOKENS = 4096
# Fragment rules from pi-ai 1.5.1 catalog/families/anthropic.ts.
_ADAPTIVE_THINKING_FRAGMENTS = (
    "opus-4-6",
    "opus-4.6",
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-4-6",
    "sonnet-4.6",
    "sonnet-5",
    "sonnet.5",
    "haiku-5",
    "haiku.5",
    "fable-5",
    "mythos-5",
)
_MAX_EFFORT_ONLY_FRAGMENTS = ("opus-4-6", "opus-4.6", "sonnet-4-6", "sonnet-4.6")
_XHIGH_AND_MAX_EFFORT_FRAGMENTS = (
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-5",
    "sonnet.5",
    "haiku-5",
    "haiku.5",
    "fable-5",
)


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

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "")
        if thinking_type == "disabled":
            chat["reasoning_effort"] = "none"
        elif thinking_type == "adaptive":
            output_config = body.get("output_config")
            effort = (
                _clean_thinking_effort(output_config.get("effort"))
                if isinstance(output_config, dict)
                else None
            )
            if effort:
                chat["reasoning_effort"] = effort
        elif thinking_type == "enabled":
            effort = _anthropic_budget_to_effort(thinking.get("budget_tokens"))
            if effort:
                chat["reasoning_effort"] = effort

    return chat


def chat_request_to_anthropic(body: dict[str, Any]) -> dict[str, Any]:
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Chat request messages must be a list")

    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    system_block_groups: list[list[dict[str, Any]]] = []
    error_images: list[dict[str, Any]] = []
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        role = str(raw_message.get("role") or "user")
        if role != "tool" and error_images:
            _append_anthropic_message(messages, "user", error_images)
            error_images = []
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
                # Anthropic requires ids to match [a-zA-Z0-9_-] and be at most
                # 64 characters, so foreign ids are normalized like pi does.
                "tool_use_id": _normalize_anthropic_tool_id(
                    str(raw_message.get("tool_call_id") or "")
                ),
                "content": _tool_result_content(raw_message.get("content")),
                # is_error is always present on the wire, never omitted.
                "is_error": bool(raw_message.get("is_error")),
            }
            result_content = tool_result["content"]
            if tool_result["is_error"] and isinstance(result_content, list):
                text_blocks: list[dict[str, Any]] = []
                images: list[dict[str, Any]] = []
                for block in result_content:
                    if block.get("type") == "image":
                        images.append(block)
                        if (
                            not text_blocks
                            or text_blocks[-1].get("text")
                            != "(image attached after the tool results)"
                        ):
                            text_blocks.append(
                                {
                                    "type": "text",
                                    "text": "(image attached after the tool results)",
                                }
                            )
                    else:
                        text_blocks.append(block)
                if images:
                    tool_result["content"] = text_blocks
                    error_images.extend(
                        [
                            {
                                "type": "text",
                                "text": f"Images from the error result of tool call {tool_result['tool_use_id']}:",
                            },
                            *images,
                        ]
                    )
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
        if anthropic_role == "assistant":
            reasoning_blocks = _chat_reasoning_to_anthropic_blocks(raw_message)
            if reasoning_blocks:
                blocks = reasoning_blocks + blocks
            if isinstance(tool_calls, list):
                blocks.extend(chat_tool_calls_to_anthropic_content(tool_calls))
        _apply_message_cache_control(blocks, raw_message)
        _append_anthropic_message(messages, anthropic_role, blocks)

    if error_images:
        _append_anthropic_message(messages, "user", error_images)

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
        # pi always sends system as an array of text blocks, never a bare string,
        # so cache_control can be attached per block.
        anthropic["system"] = [{"type": "text", "text": "\n\n".join(system_parts)}]
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

    effort = _extract_chat_reasoning_effort(body)
    if effort:
        if effort == "none":
            anthropic["thinking"] = {"type": "disabled"}
        elif any(
            fragment in anthropic["model"] for fragment in _ADAPTIVE_THINKING_FRAGMENTS
        ) or re.match(r"^~anthropic/claude-[a-z]+-latest$", anthropic["model"]):
            anthropic["thinking"] = {"type": "adaptive", "display": "summarized"}
            adaptive_effort = "low" if effort == "minimal" else effort
            # Runtime defaults xhigh/max to high without an explicit family mapping.
            if effort in {"xhigh", "max"} and not (
                any(
                    fragment in anthropic["model"]
                    for fragment in _XHIGH_AND_MAX_EFFORT_FRAGMENTS
                )
                or (
                    effort == "max"
                    and any(
                        fragment in anthropic["model"]
                        for fragment in _MAX_EFFORT_ONLY_FRAGMENTS
                    )
                )
            ):
                adaptive_effort = "high"
            anthropic["output_config"] = {"effort": adaptive_effort}
        else:
            budget = _effort_to_anthropic_budget(effort)
            if budget:
                anthropic["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                    # Older Claude 4 models default to "summarized"; pi sets it
                    # explicitly so providers with a different default match.
                    "display": "summarized",
                }
                # Extended thinking requires max_tokens to comfortably exceed
                # the budget so the final response is not truncated.
                current_max = anthropic.get("max_tokens")
                if isinstance(current_max, int) and current_max < budget + 1024:
                    anthropic["max_tokens"] = budget + 1024

    thinking = anthropic.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") != "disabled":
        # Anthropic rejects a temperature with extended thinking enabled.
        anthropic.pop("temperature", None)

    return anthropic


def chat_response_to_anthropic(
    chat_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason")
    stop_reason = FINISH_REASON_CHAT_TO_ANTHROPIC.get(finish_reason, "end_turn")

    content: list[dict[str, Any]] = []
    content.extend(_chat_reasoning_to_anthropic_blocks(message, replay=False) or [])
    text = _chat_content_to_text(message.get("content"))
    if text:
        content.append({"type": "text", "text": text})
    tool_calls = message.get("tool_calls")
    if tool_calls:
        content.extend(chat_tool_calls_to_anthropic_content(tool_calls))

    return {
        "id": chat_body.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "model": chat_body.get("model", original_model),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _chat_usage_to_anthropic_usage(chat_body.get("usage")),
    }


def anthropic_response_to_chat(
    anthropic_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_blocks: list[dict[str, Any]] = []
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
            reasoning_blocks.append(
                {
                    key: block[key]
                    for key in ("type", "thinking", "signature")
                    if key in block
                }
            )
            thinking = block.get("thinking")
            if isinstance(thinking, str):
                reasoning_parts.append(thinking)
        elif block_type == "redacted_thinking":
            reasoning_blocks.append(
                {"type": "redacted_thinking", "data": block.get("data", "")}
            )
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
        "content": "".join(content_parts) or None,
    }
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if reasoning_blocks:
        message["reasoning_blocks"] = reasoning_blocks
        signed = [block for block in reasoning_blocks if block["type"] == "thinking"]
        if len(signed) == 1 and signed[0].get("signature"):
            message["reasoning_signature"] = signed[0]["signature"]
        redacted = [
            block for block in reasoning_blocks if block["type"] == "redacted_thinking"
        ]
        if redacted:
            message["reasoning_redacted"] = redacted
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
    usage: dict[str, Any] = {}
    text_block_index: int | None = None
    thinking_index: int | None = None
    closed_blocks: set[int] = set()
    pending_signature = ""
    tool_calls_state = ChatToolCalls()
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

    async for payload in _parse_sse_stream(raw_iterator):
        if payload.get("error"):
            raise ValueError("Chat stream returned an error")
        _merge_usage(usage, payload.get("usage"))

        for choice in payload.get("choices", []):
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta", {})
            reasoning_delta = _chat_delta_reasoning_content(delta)
            text_delta = _chat_content_to_text(delta.get("content"))
            tc_deltas = delta.get("tool_calls")

            signature = delta.get("reasoning_signature")
            if reasoning_delta or (
                reasoning_delta is not None
                and (
                    pending_signature
                    or (isinstance(signature, str) and signature.strip())
                )
            ):
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

            if isinstance(signature, str):
                pending_signature += signature
            metadata = delta.get("reasoning_blocks")
            if isinstance(metadata, list):
                for block in (
                    _chat_reasoning_to_anthropic_blocks(
                        {"reasoning_blocks": metadata}, replay=False
                    )
                    or []
                ):
                    if block["type"] == "thinking" and thinking_index is not None:
                        signature = block.get("signature", pending_signature)
                        if signature:
                            yield format_sse_event(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": thinking_index,
                                    "delta": {
                                        "type": "signature_delta",
                                        "signature": signature,
                                    },
                                },
                            )
                        yield format_sse_event(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": thinking_index},
                        )
                        closed_blocks.add(thinking_index)
                        thinking_index = None
                    else:
                        index = next_block_index
                        next_block_index += 1
                        yield format_sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": index,
                                "content_block": block,
                            },
                        )
                        yield format_sse_event(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": index},
                        )
                        closed_blocks.add(index)
                    pending_signature = ""

            if text_delta:
                if text_block_index is None:
                    text_block_index = next_block_index
                    next_block_index += 1
                    yield format_sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": text_block_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                yield format_sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_block_index,
                        "delta": {"type": "text_delta", "text": text_delta},
                    },
                )

            if tc_deltas:
                for tc in tc_deltas:
                    if not isinstance(tc, dict):
                        continue
                    for tool_call in tool_calls_state.update_many(
                        tc, choice_index=chat_choice_index(choice)
                    ):
                        if tool_call.target_index is None:
                            tool_call.target_index = next_block_index
                            next_block_index += 1
                        if not tool_call.announced:
                            yield format_sse_event(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": tool_call.target_index,
                                    "content_block": {
                                        "type": "tool_use",
                                        "id": tool_call.call_id
                                        or f"toolu_{uuid.uuid4().hex[:24]}",
                                        "name": tool_call.name,
                                        "input": {},
                                    },
                                },
                            )
                            tool_call.announced = True
                        arguments_delta = tool_call.take_argument_delta()
                        if arguments_delta:
                            yield format_sse_event(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": tool_call.target_index,
                                    "delta": {
                                        "type": "input_json_delta",
                                        "partial_json": arguments_delta,
                                    },
                                },
                            )

    if not finish_reason:
        raise ValueError("Chat stream ended without a finish reason")

    for tool_call in tool_calls_state:
        if not tool_call.announced:
            yield format_sse_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": (
                        tool_call.target_index
                        if tool_call.target_index is not None
                        else 0
                    ),
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_call.call_id or f"toolu_{uuid.uuid4().hex[:24]}",
                        "name": tool_call.name,
                        "input": {},
                    },
                },
            )
            tool_call.announced = True
        leftover = tool_call.take_argument_delta()
        if leftover:
            yield format_sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": tool_call.target_index or 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": leftover,
                    },
                },
            )
        if not tool_call.arguments.strip() or finish_reason in {
            "length",
            "content_filter",
        }:
            continue
        try:
            json.loads(tool_call.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid tool call arguments JSON") from exc

    if pending_signature and thinking_index is None:
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
    if pending_signature and thinking_index is not None:
        yield format_sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": thinking_index,
                "delta": {"type": "signature_delta", "signature": pending_signature},
            },
        )
    for i in range(next_block_index):
        if i in closed_blocks:
            continue
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
            "usage": _chat_usage_to_anthropic_usage(usage),
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
    reasoning_blocks: dict[int, dict[str, Any]] = {}
    tool_call_indexes: dict[int, int] = {}
    next_tool_call_index = 0
    usage: dict[str, Any] = {}
    finish_reason: str | None = None
    finish_sent = False
    done_sent = False

    async for payload in _parse_sse_stream(raw_iterator):
        payload_type = str(payload.get("type") or "")
        if payload_type == "error":
            raise ValueError(f"Anthropic stream error: {payload.get('error')}")
        if done_sent:
            continue
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
                reasoning_blocks[index] = {
                    "type": "thinking",
                    "thinking": block.get("thinking") or "",
                    "signature": block.get("signature") or "",
                }
                if block.get("signature"):
                    yield _chat_stream_event(
                        message_id,
                        model,
                        created,
                        {"reasoning_signature": block["signature"]},
                    )
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking:
                    yield _chat_stream_event(
                        message_id, model, created, {"reasoning_content": thinking}
                    )
            elif block_type == "redacted_thinking":
                reasoning_blocks[index] = {
                    "type": "redacted_thinking",
                    "data": block.get("data", ""),
                }
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
            elif delta_type == "signature_delta" and index in reasoning_blocks:
                signature = delta.get("signature")
                if isinstance(signature, str):
                    reasoning_blocks[index]["signature"] = (
                        reasoning_blocks[index].get("signature", "") + signature
                    )
                    yield _chat_stream_event(
                        message_id, model, created, {"reasoning_signature": signature}
                    )
            elif delta_type == "thinking_delta":
                thinking = delta.get("thinking")
                if isinstance(thinking, str) and index in reasoning_blocks:
                    reasoning_blocks[index]["thinking"] += thinking
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

        if payload_type == "content_block_stop":
            index = _coerce_index(payload.get("index"))
            block = reasoning_blocks.pop(index, None)
            if block is not None:
                yield _chat_stream_event(
                    message_id, model, created, {"reasoning_blocks": [block]}
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
            if not finish_reason:
                raise ValueError("Anthropic stream ended without a stop reason")
            if not finish_sent:
                yield _chat_stream_event(
                    message_id,
                    model,
                    created,
                    {},
                    finish_reason=finish_reason,
                    usage=_anthropic_usage_to_chat_usage(usage),
                )
            yield b"data: [DONE]\n\n"
            done_sent = True
            continue

    if not done_sent:
        raise ValueError("Anthropic stream ended before message_stop")


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
            elif isinstance(item, dict) and item.get("thought") is not True:
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


def _chat_reasoning_to_anthropic_blocks(
    message: dict[str, Any], *, replay: bool = True
) -> list[dict[str, Any]] | None:
    """Turn Chat reasoning_content into thinking blocks for replay.

    Anthropic requires a signature on assistant thinking blocks. When the
    signature is missing the block cannot be replayed as thinking, so it is
    downgraded to plain text, which is what pi does for unsigned blocks.
    """
    metadata = message.get("reasoning_blocks")
    if isinstance(metadata, list) and metadata:
        blocks: list[dict[str, Any]] = []
        for block in metadata:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "redacted_thinking" and isinstance(
                block.get("data"), str
            ):
                blocks.append({"type": "redacted_thinking", "data": block["data"]})
            elif block.get("type") == "thinking" and isinstance(
                block.get("thinking"), str
            ):
                blocks.extend(
                    _chat_reasoning_to_anthropic_blocks(
                        {
                            "reasoning_content": block["thinking"],
                            "reasoning_signature": block.get("signature"),
                        },
                        replay=replay,
                    )
                    or []
                )
        return blocks or None
    has_reasoning, reasoning = _chat_message_reasoning_content(message)
    redacted = message.get("reasoning_redacted")
    blocks: list[dict[str, Any]] = []
    if isinstance(redacted, list):
        blocks.extend(
            block
            for block in redacted
            if isinstance(block, dict) and block.get("type") == "redacted_thinking"
        )
    if not has_reasoning:
        return blocks or None
    signature = message.get("reasoning_signature")
    if isinstance(signature, str) and signature.strip():
        blocks.append(
            {
                "type": "thinking",
                "thinking": reasoning,
                "signature": signature,
            }
        )
    elif reasoning:
        blocks.append(
            {"type": "text", "text": reasoning}
            if replay
            else {"type": "thinking", "thinking": reasoning}
        )
    return blocks


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


def _tool_result_content(content: Any) -> Any:
    """Anthropic accepts a string for text-only results and blocks when images
    are present; an image-only result needs a placeholder text block."""
    blocks = _chat_content_to_anthropic_blocks(content)
    has_image = any(block.get("type") == "image" for block in blocks)
    if not has_image:
        return _chat_content_to_text(content) or "..."
    if not any(block.get("type") == "text" for block in blocks):
        blocks.insert(0, {"type": "text", "text": "(see attached image)"})
    return blocks


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


def _chat_usage_to_anthropic_usage(raw_usage: Any) -> dict[str, Any]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_tokens = _int_value(usage.get("prompt_tokens"))
    prompt_details = usage.get("prompt_tokens_details")
    cache_read = (
        _int_value(prompt_details.get("cached_tokens"))
        if isinstance(prompt_details, dict)
        else 0
    )
    cache_write = (
        _int_value(prompt_details.get("cache_creation_tokens"))
        if isinstance(prompt_details, dict)
        else 0
    )
    # Anthropic reports input_tokens as non-cached input, with cache reads and
    # writes as separate fields. OpenAI-style prompt_tokens includes cached
    # tokens, so subtract them to keep client-visible cache accounting correct.
    anthropic_usage: dict[str, Any] = {
        "input_tokens": max(prompt_tokens - cache_read - cache_write, 0),
        "output_tokens": _int_value(usage.get("completion_tokens")),
    }
    if isinstance(prompt_details, dict) and (
        "cached_tokens" in prompt_details or "cache_creation_tokens" in prompt_details
    ):
        anthropic_usage["cache_read_input_tokens"] = cache_read
        anthropic_usage["cache_creation_input_tokens"] = cache_write
    completion_details = usage.get("completion_tokens_details")
    reasoning_tokens = (
        _int_value(completion_details.get("reasoning_tokens"))
        if isinstance(completion_details, dict)
        else 0
    )
    if (
        isinstance(completion_details, dict)
        and "reasoning_tokens" in completion_details
    ):
        # Anthropic reports thinking tokens as a subset of output_tokens.
        anthropic_usage["output_tokens_details"] = {"thinking_tokens": reasoning_tokens}
    return anthropic_usage


def _anthropic_usage_to_chat_usage(raw_usage: Any) -> dict[str, Any]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = _int_value(usage.get("input_tokens"))
    cache_read = _int_value(usage.get("cache_read_input_tokens"))
    cache_creation = _int_value(usage.get("cache_creation_input_tokens"))
    cache_creation_obj = usage.get("cache_creation")
    if "cache_creation_input_tokens" not in usage and isinstance(
        cache_creation_obj, dict
    ):
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
    if (
        "cache_read_input_tokens" in usage
        or "cache_creation_input_tokens" in usage
        or isinstance(cache_creation_obj, dict)
    ):
        result["prompt_tokens_details"] = {
            "cached_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
        }
    output_details = usage.get("output_tokens_details")
    thinking_tokens = (
        _int_value(output_details.get("thinking_tokens"))
        if isinstance(output_details, dict)
        else 0
    )
    if isinstance(output_details, dict) and "thinking_tokens" in output_details:
        # Anthropic reports thinking tokens as a subset of output tokens.
        result["completion_tokens_details"] = {"reasoning_tokens": thinking_tokens}
    return result


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _merge_usage(target: dict[str, Any], raw_usage: Any) -> None:
    """Merge upstream usage with last-value-wins semantics.

    Anthropic stream usage is cumulative (message_delta carries the final
    totals), so summing would double count. Fields absent from a later event
    keep their earlier value, matching pi's field-wise update behavior.
    """
    if not isinstance(raw_usage, dict):
        return
    for key, value in raw_usage.items():
        if isinstance(value, dict):
            current = target.setdefault(key, {})
            if isinstance(current, dict):
                _merge_usage(current, value)
        else:
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
