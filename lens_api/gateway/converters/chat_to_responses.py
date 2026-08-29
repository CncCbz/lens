import hashlib
import json
import time
import uuid
from typing import Any, AsyncIterator

from ._chat_stream import ChatToolCalls, chat_choice_index
from ._shared import (
    FINISH_REASON_CHAT_TO_RESPONSES,
    _clean_thinking_effort,
    _copy_cache_control,
    _parse_sse_stream,
    format_sse_event,
    responses_input_to_chat_messages,
    responses_tools_to_chat_tools,
)

_RESPONSES_STATUS_TO_CHAT_FINISH: dict[str | None, str] = {
    "completed": "stop",
    "incomplete": "length",
    "failed": "content_filter",
    "cancelled": "stop",
}

_RESPONSES_CACHE_AND_ROUTING_KEYS = (
    "cache_control",
    "prompt_cache_key",
    "prompt_cache_retention",
    "safety_identifier",
    "service_tier",
    "include",
    "client_metadata",
    "parallel_tool_calls",
    "truncation",
    "max_tool_calls",
    "background",
    "previous_response_id",
    "conversation",
    "context_management",
)


def responses_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    chat: dict[str, Any] = {}

    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    input_val = body.get("input", [])
    if isinstance(input_val, str):
        messages.append({"role": "user", "content": input_val})
    elif isinstance(input_val, list):
        messages.extend(responses_input_to_chat_messages(input_val))
    chat["messages"] = messages

    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            chat[key] = body[key]

    if "tools" in body:
        chat["tools"] = responses_tools_to_chat_tools(body["tools"])
    if "tool_choice" in body:
        chat["tool_choice"] = body["tool_choice"]
    for key in _RESPONSES_CACHE_AND_ROUTING_KEYS:
        if key in body:
            chat[key] = body[key]

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = _clean_thinking_effort(reasoning.get("effort"))
        if effort:
            chat["reasoning_effort"] = effort

    return chat


def chat_request_to_responses(
    body: dict[str, Any], *, preserve_reasoning: bool = False
) -> dict[str, Any]:
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Chat request messages must be a list")

    input_items: list[dict[str, Any]] = []
    for message in raw_messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if role in {"system", "developer"}:
            content = _chat_content_to_responses_content(
                message.get("content"), output=False
            )
            _apply_message_cache_control(content, message)
            input_items.append({"role": role, "content": content})
            continue
        if role == "tool":
            item = {
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": _chat_content_to_text(message.get("content")),
            }
            _copy_cache_control(item, message)
            if "cache_control" not in item:
                _copy_cache_control_from_chat_content(item, message.get("content"))
            input_items.append(item)
            continue
        tool_calls = message.get("tool_calls")
        if role == "assistant" and preserve_reasoning:
            reasoning_text = _chat_message_reasoning_text(message)
            if not reasoning_text and isinstance(tool_calls, list) and tool_calls:
                # DeepSeek rejects missing or empty reasoning_text on tool-call replay.
                reasoning_text = " "
            if reasoning_text:
                input_items.append(
                    {
                        "type": "reasoning",
                        "content": [{"type": "reasoning_text", "text": reasoning_text}],
                    }
                )
        if role == "assistant" and isinstance(tool_calls, list):
            text = _chat_content_to_text(message.get("content"))
            if text:
                content = _chat_content_to_responses_content(
                    message.get("content"), output=True
                )
                _apply_message_cache_control(content, message)
                input_items.append({"role": "assistant", "content": content})
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                item = {
                    "type": "function_call",
                    "call_id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                    "name": function.get("name") or "",
                    "arguments": _json_arguments(function.get("arguments")),
                }
                _copy_cache_control(item, tool_call)
                if "cache_control" not in item:
                    _copy_cache_control(item, function)
                input_items.append(item)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        content = _chat_content_to_responses_content(
            message.get("content"), output=role == "assistant"
        )
        _apply_message_cache_control(content, message)
        input_items.append({"role": role, "content": content})

    responses: dict[str, Any] = {
        "model": body.get("model") or "",
        "input": input_items or "",
    }
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions:
        responses["instructions"] = instructions
    max_tokens = body.get("max_completion_tokens", body.get("max_tokens"))
    if (
        isinstance(max_tokens, int)
        and not isinstance(max_tokens, bool)
        and max_tokens > 0
    ):
        responses["max_output_tokens"] = max_tokens
    for key in (
        "temperature",
        "top_p",
        "stream",
        "metadata",
        "store",
        "user",
        *_RESPONSES_CACHE_AND_ROUTING_KEYS,
    ):
        if key in body:
            responses[key] = body[key]
    if "prompt_cache_key" not in responses:
        prompt_cache_key = _derive_prompt_cache_key(body, input_items)
        if prompt_cache_key:
            responses["prompt_cache_key"] = prompt_cache_key
    if "tools" in body:
        responses["tools"] = _chat_tools_to_responses_tools(body["tools"])
    if "tool_choice" in body:
        responses["tool_choice"] = body["tool_choice"]
    text_config = _chat_response_format_to_responses_text(body.get("response_format"))
    if text_config is not None:
        responses["text"] = text_config
    reasoning = _chat_reasoning_to_responses(body)
    if reasoning is not None:
        responses["reasoning"] = reasoning
    return responses


def chat_response_to_responses(
    chat_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason")
    status = FINISH_REASON_CHAT_TO_RESPONSES.get(finish_reason, "completed")

    output: list[dict[str, Any]] = []
    msg_item: dict[str, Any] = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [],
    }
    text = message.get("content")
    if text:
        msg_item["content"].append(
            {
                "type": "output_text",
                "text": text,
                "annotations": [],
            }
        )
    output.append(msg_item)

    tool_calls = message.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            func = tc.get("function", {})
            output.append(
                {
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "type": "function_call",
                    "status": "completed",
                    "name": func.get("name", ""),
                    "arguments": _json_arguments(func.get("arguments")),
                    "call_id": tc.get("id", ""),
                }
            )

    usage = chat_body.get("usage", {})
    inp = _int_value(usage.get("prompt_tokens"))
    out = _int_value(usage.get("completion_tokens"))
    response_usage: dict[str, Any] = {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": _int_value(usage.get("total_tokens")) or inp + out,
    }
    if isinstance(usage.get("prompt_tokens_details"), dict):
        response_usage["input_tokens_details"] = {
            "cached_tokens": _usage_detail_int(
                usage, "prompt_tokens_details", "cached_tokens"
            )
        }
    return {
        "id": chat_body.get("id", f"resp_{uuid.uuid4().hex[:24]}"),
        "object": "response",
        "created_at": int(time.time()),
        "model": chat_body.get("model", original_model),
        "status": status,
        "output": output,
        "usage": response_usage,
    }


def responses_response_to_chat(
    responses_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in responses_body.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type in {"output_text", "text", "refusal"}:
                    text = part.get("text")
                    if isinstance(text, str):
                        content_parts.append(text)
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id")
                    or item.get("id")
                    or f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "",
                        "arguments": _json_arguments(item.get("arguments")),
                    },
                }
            )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) or None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = (
        responses_body.get("usage")
        if isinstance(responses_body.get("usage"), dict)
        else {}
    )
    return {
        "id": responses_body.get("id") or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": responses_body.get("model") or original_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _responses_status_to_chat_finish(
                    responses_body.get("status")
                ),
            }
        ],
        "usage": _responses_usage_to_chat_usage(usage),
    }


async def chat_stream_to_responses_stream(
    raw_iterator: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    resp_id = f"resp_{uuid.uuid4().hex[:24]}"
    resolved_model = original_model
    input_tokens = 0
    output_tokens = 0
    cached_input_tokens = 0
    input_token_details_seen = False
    finish_reason: str | None = None

    next_output_index = 0
    reasoning_oi: int | None = None
    reasoning_id = f"rs_{uuid.uuid4().hex[:24]}"
    reasoning_text = ""
    text_oi: int | None = None
    text_id = f"msg_{uuid.uuid4().hex[:24]}"
    text_content = ""
    tool_items: dict[int, dict[str, Any]] = {}  # oi -> {item, args}
    tool_calls_state = ChatToolCalls()

    yield format_sse_event(
        "response.created",
        {
            "type": "response.created",
            "response": {
                "id": resp_id,
                "object": "response",
                "created_at": int(time.time()),
                "model": resolved_model,
                "status": "in_progress",
                "output": [],
                "usage": None,
            },
        },
    )

    async for payload in _parse_sse_stream(raw_iterator):
        if payload.get("model"):
            resolved_model = payload["model"]
        usage = payload.get("usage") or {}
        if usage.get("prompt_tokens"):
            input_tokens = usage["prompt_tokens"]
        if usage.get("completion_tokens"):
            output_tokens = usage["completion_tokens"]
        if isinstance(usage.get("prompt_tokens_details"), dict):
            input_token_details_seen = True
            cached_input_tokens = _usage_detail_int(
                usage, "prompt_tokens_details", "cached_tokens"
            )

        for choice in payload.get("choices", []):
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                continue
            text_delta = delta.get("content")
            refusal_delta = delta.get("refusal")
            reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning")
            tc_deltas = delta.get("tool_calls")

            if isinstance(reasoning_delta, str) and reasoning_delta:
                if reasoning_oi is None:
                    reasoning_oi = next_output_index
                    next_output_index += 1
                    yield format_sse_event(
                        "response.output_item.added",
                        {
                            "type": "response.output_item.added",
                            "output_index": reasoning_oi,
                            "item": {
                                "id": reasoning_id,
                                "type": "reasoning",
                                "status": "in_progress",
                                "summary": [],
                                "content": [],
                            },
                        },
                    )
                reasoning_text += reasoning_delta
                yield format_sse_event(
                    "response.reasoning_text.delta",
                    {
                        "type": "response.reasoning_text.delta",
                        "output_index": reasoning_oi,
                        "delta": reasoning_delta,
                    },
                )

            merged_text = (text_delta if isinstance(text_delta, str) else "") + (
                refusal_delta if isinstance(refusal_delta, str) else ""
            )
            if merged_text:
                if text_oi is None:
                    text_oi = next_output_index
                    next_output_index += 1
                    yield format_sse_event(
                        "response.output_item.added",
                        {
                            "type": "response.output_item.added",
                            "output_index": text_oi,
                            "item": {
                                "id": text_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            },
                        },
                    )
                    yield format_sse_event(
                        "response.content_part.added",
                        {
                            "type": "response.content_part.added",
                            "output_index": text_oi,
                            "content_index": 0,
                            "part": {
                                "type": "output_text",
                                "text": "",
                                "annotations": [],
                            },
                        },
                    )
                text_content += merged_text
                yield format_sse_event(
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "output_index": text_oi,
                        "content_index": 0,
                        "delta": merged_text,
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
                            oi = next_output_index
                            next_output_index += 1
                            tool_call.target_index = oi
                            item = {
                                "id": f"fc_{uuid.uuid4().hex[:24]}",
                                "type": "function_call",
                                "status": "in_progress",
                                "name": tool_call.name,
                                "arguments": "",
                                "call_id": tool_call.call_id
                                or f"call_{uuid.uuid4().hex[:24]}",
                            }
                            tool_items[oi] = {"item": item, "args": ""}
                            yield format_sse_event(
                                "response.output_item.added",
                                {
                                    "type": "response.output_item.added",
                                    "output_index": oi,
                                    "item": item,
                                },
                            )
                        oi = tool_call.target_index
                        arguments_delta = tool_call.take_argument_delta()
                        if arguments_delta:
                            tool_items[oi]["args"] += arguments_delta
                            yield format_sse_event(
                                "response.function_call_arguments.delta",
                                {
                                    "type": "response.function_call_arguments.delta",
                                    "output_index": oi,
                                    "delta": arguments_delta,
                                },
                            )

    # Finalize completed output items and emit their done events in output index
    # order so a Responses client can reconstruct the full response.
    final_items: dict[int, dict[str, Any]] = {}
    if reasoning_oi is not None:
        reasoning_item = {
            "id": reasoning_id,
            "type": "reasoning",
            "status": "completed",
            "summary": (
                [{"type": "summary_text", "text": reasoning_text}]
                if reasoning_text
                else []
            ),
            "content": (
                [{"type": "reasoning_text", "text": reasoning_text}]
                if reasoning_text
                else []
            ),
        }
        final_items[reasoning_oi] = reasoning_item
    if text_oi is not None:
        final_items[text_oi] = {
            "id": text_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": text_content,
                    "annotations": [],
                }
            ],
        }
    for oi in sorted(tool_items):
        state = tool_items[oi]
        if state["args"].strip():
            try:
                json.loads(state["args"])
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid tool call arguments JSON") from exc
        state["item"]["status"] = "completed"
        state["item"]["arguments"] = state["args"]
        final_items[oi] = state["item"]

    for oi in sorted(final_items):
        if oi == reasoning_oi:
            yield format_sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": oi,
                    "item": final_items[oi],
                },
            )
        elif oi == text_oi:
            yield format_sse_event(
                "response.output_text.done",
                {
                    "type": "response.output_text.done",
                    "output_index": oi,
                    "content_index": 0,
                    "text": text_content,
                },
            )
            yield format_sse_event(
                "response.content_part.done",
                {
                    "type": "response.content_part.done",
                    "output_index": oi,
                    "content_index": 0,
                    "part": final_items[oi]["content"][0],
                },
            )
            yield format_sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": oi,
                    "item": final_items[oi],
                },
            )
        else:
            yield format_sse_event(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": oi,
                    "arguments": final_items[oi]["arguments"],
                },
            )
            yield format_sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": oi,
                    "item": final_items[oi],
                },
            )

    status = FINISH_REASON_CHAT_TO_RESPONSES.get(finish_reason, "completed")
    total = input_tokens + output_tokens
    usage_payload: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
    }
    if input_token_details_seen:
        usage_payload["input_tokens_details"] = {"cached_tokens": cached_input_tokens}
    yield format_sse_event(
        "response.completed",
        {
            "type": "response.completed",
            "response": {
                "id": resp_id,
                "object": "response",
                "created_at": int(time.time()),
                "model": resolved_model,
                "status": status,
                "output": [final_items[oi] for oi in sorted(final_items)],
                "usage": usage_payload,
            },
        },
    )


async def responses_stream_to_chat_stream(
    raw_iterator: AsyncIterator[bytes],
    original_model: str,
) -> AsyncIterator[bytes]:
    response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    model = original_model
    created = int(time.time())
    role_sent = False
    done_sent = False
    tool_call_seen = False

    text_by_index: dict[int, str] = {}
    reasoning_by_index: dict[int, str] = {}
    tool_by_index: dict[int, dict[str, Any]] = {}
    tool_oi_to_chat_index: dict[int, int] = {}
    emitted_args: dict[int, str] = {}
    next_tool_index = 0
    usage: dict[str, Any] = {}

    async for payload in _parse_sse_stream(raw_iterator):
        if done_sent:
            continue
        payload_type = str(payload.get("type") or "")
        response = payload.get("response")
        if isinstance(response, dict):
            response_id = str(response.get("id") or response_id)
            model = str(response.get("model") or model)
        if not role_sent and payload_type.startswith("response."):
            role_sent = True
            yield _chat_stream_event(response_id, model, created, {"role": "assistant"})

        if payload_type == "response.output_item.added":
            output_index = _coerce_index(payload.get("output_index"))
            item = payload.get("item")
            if output_index is None or not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                tool_call_seen = True
                if output_index not in tool_by_index:
                    tool_oi_to_chat_index[output_index] = next_tool_index
                    next_tool_index += 1
                    tool_by_index[output_index] = {
                        "index": tool_oi_to_chat_index[output_index],
                        "id": item.get("call_id")
                        or item.get("id")
                        or f"call_{uuid.uuid4().hex[:24]}",
                        "name": item.get("name") or "",
                        "args": item.get("arguments") or "",
                    }
                    emitted_args[output_index] = item.get("arguments") or ""
                    yield _chat_stream_event(
                        response_id,
                        model,
                        created,
                        {
                            "tool_calls": [
                                {
                                    "index": tool_by_index[output_index]["index"],
                                    "id": tool_by_index[output_index]["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tool_by_index[output_index]["name"],
                                        "arguments": tool_by_index[output_index][
                                            "args"
                                        ],
                                    },
                                }
                            ]
                        },
                    )
            elif item_type == "message":
                text_by_index.setdefault(output_index, "")
            elif item_type == "reasoning":
                reasoning_by_index.setdefault(output_index, "")
            continue

        if payload_type == "response.output_text.delta":
            output_index = _coerce_index(payload.get("output_index"), default=0)
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                text_by_index[output_index] = (
                    text_by_index.get(output_index, "") + delta
                )
                yield _chat_stream_event(
                    response_id, model, created, {"content": delta}
                )
            continue

        if payload_type == "response.refusal.delta":
            output_index = _coerce_index(payload.get("output_index"), default=0)
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                text_by_index[output_index] = (
                    text_by_index.get(output_index, "") + delta
                )
                yield _chat_stream_event(
                    response_id,
                    model,
                    created,
                    {"content": delta, "refusal": delta},
                )
            continue

        if payload_type in (
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
        ):
            output_index = _coerce_index(payload.get("output_index"), default=0)
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                reasoning_by_index[output_index] = (
                    reasoning_by_index.get(output_index, "") + delta
                )
                yield _chat_stream_event(
                    response_id,
                    model,
                    created,
                    {"reasoning_content": delta},
                )
            continue

        if payload_type == "response.reasoning_summary_part.done":
            output_index = _coerce_index(payload.get("output_index"), default=0)
            reasoning_by_index[output_index] = (
                reasoning_by_index.get(output_index, "") + "\n\n"
            )
            yield _chat_stream_event(
                response_id, model, created, {"reasoning_content": "\n\n"}
            )
            continue

        if payload_type == "response.function_call_arguments.delta":
            output_index = _coerce_index(payload.get("output_index"))
            delta = payload.get("delta")
            if output_index is not None and isinstance(delta, str) and delta:
                if output_index not in tool_by_index:
                    tool_call_seen = True
                    tool_oi_to_chat_index[output_index] = next_tool_index
                    next_tool_index += 1
                    tool_by_index[output_index] = {
                        "index": tool_oi_to_chat_index[output_index],
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "name": "",
                        "args": "",
                    }
                tool_by_index[output_index]["args"] += delta
                emitted_args[output_index] = emitted_args.get(output_index, "") + delta
                yield _chat_stream_event(
                    response_id,
                    model,
                    created,
                    {
                        "tool_calls": [
                            {
                                "index": tool_by_index[output_index]["index"],
                                "function": {"arguments": delta},
                            }
                        ]
                    },
                )
            continue

        if payload_type == "response.function_call_arguments.done":
            output_index = _coerce_index(payload.get("output_index"))
            arguments = payload.get("arguments")
            if output_index is not None and isinstance(arguments, str):
                if output_index not in tool_by_index:
                    tool_call_seen = True
                    tool_oi_to_chat_index[output_index] = next_tool_index
                    next_tool_index += 1
                    tool_by_index[output_index] = {
                        "index": tool_oi_to_chat_index[output_index],
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "name": "",
                        "args": arguments,
                    }
                    emitted_args[output_index] = arguments
                    yield _chat_stream_event(
                        response_id,
                        model,
                        created,
                        {
                            "tool_calls": [
                                {
                                    "index": tool_by_index[output_index]["index"],
                                    "function": {"arguments": arguments},
                                }
                            ]
                        },
                    )
                else:
                    prev = emitted_args.get(output_index, "")
                    if arguments.startswith(prev):
                        suffix = arguments[len(prev) :]
                        tool_by_index[output_index]["args"] = arguments
                        emitted_args[output_index] = arguments
                        if suffix:
                            yield _chat_stream_event(
                                response_id,
                                model,
                                created,
                                {
                                    "tool_calls": [
                                        {
                                            "index": tool_by_index[output_index][
                                                "index"
                                            ],
                                            "function": {"arguments": suffix},
                                        }
                                    ]
                                },
                            )
                    else:
                        # A non-extending replacement cannot be expressed in a
                        # Chat delta stream; record the final value only.
                        tool_by_index[output_index]["args"] = arguments
                        emitted_args[output_index] = arguments
            continue

        if payload_type == "response.output_item.done":
            output_index = _coerce_index(payload.get("output_index"))
            item = payload.get("item")
            if output_index is None or not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                parts = item.get("content") or []
                full_text = "".join(
                    part.get("text", "")
                    for part in parts
                    if isinstance(part, dict)
                    and part.get("type") in ("output_text", "text", "refusal")
                )
                if full_text:
                    text_by_index[output_index] = full_text
            elif item_type == "function_call":
                tool_call_seen = True
                if output_index not in tool_by_index:
                    tool_oi_to_chat_index[output_index] = next_tool_index
                    next_tool_index += 1
                    tool_by_index[output_index] = {
                        "index": tool_oi_to_chat_index[output_index],
                        "id": item.get("call_id")
                        or item.get("id")
                        or f"call_{uuid.uuid4().hex[:24]}",
                        "name": item.get("name") or "",
                        "args": item.get("arguments") or "",
                    }
                    emitted_args[output_index] = item.get("arguments") or ""
                    state = tool_by_index[output_index]
                    yield _chat_stream_event(
                        response_id,
                        model,
                        created,
                        {
                            "tool_calls": [
                                {
                                    "index": state["index"],
                                    "id": state["id"],
                                    "type": "function",
                                    "function": {
                                        "name": state["name"],
                                        "arguments": state["args"],
                                    },
                                }
                            ]
                        },
                    )
                else:
                    state = tool_by_index[output_index]
                    if item.get("call_id") or item.get("id"):
                        state["id"] = (
                            item.get("call_id") or item.get("id") or state["id"]
                        )
                    if item.get("name"):
                        state["name"] = item["name"]
                    args = item.get("arguments")
                    if isinstance(args, str):
                        prev = emitted_args.get(output_index, "")
                        if args.startswith(prev):
                            suffix = args[len(prev) :]
                            state["args"] = args
                            emitted_args[output_index] = args
                            if suffix:
                                yield _chat_stream_event(
                                    response_id,
                                    model,
                                    created,
                                    {
                                        "tool_calls": [
                                            {
                                                "index": state["index"],
                                                "function": {"arguments": suffix},
                                            }
                                        ]
                                    },
                                )
                        else:
                            state["args"] = args
                            emitted_args[output_index] = args
            elif item_type == "reasoning":
                summary = item.get("summary") or item.get("content") or []
                full = "\n\n".join(
                    part.get("text", "") for part in summary if isinstance(part, dict)
                )
                if full:
                    reasoning_by_index[output_index] = full
            continue

        if payload_type in ("response.completed", "response.incomplete"):
            if isinstance(response, dict):
                if isinstance(response.get("usage"), dict):
                    usage = response["usage"]
                status = response.get("status") or (
                    "incomplete"
                    if payload_type == "response.incomplete"
                    else "completed"
                )
                base_finish = _responses_status_to_chat_finish(status)
            else:
                base_finish = "stop"
            finish_reason = "tool_calls" if tool_call_seen else base_finish
            yield _chat_stream_event(
                response_id,
                model,
                created,
                {},
                finish_reason=finish_reason,
                usage=_responses_usage_to_chat_usage(usage),
            )
            yield b"data: [DONE]\n\n"
            done_sent = True
            continue

        if payload_type == "response.failed":
            raise ValueError("Upstream Responses stream failed")
        if payload_type == "error":
            raise ValueError("Upstream Responses stream error")

    if not done_sent:
        raise ValueError("Responses stream ended before a terminal event")


def _derive_prompt_cache_key(
    body: dict[str, Any], input_items: list[dict[str, Any]]
) -> str | None:
    for key in ("session_id", "conversation_id", "user"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return _prompt_cache_key_hash(value.strip())

    seed: dict[str, Any] = {
        "model": body.get("model") or "",
        "tools": body.get("tools") if isinstance(body.get("tools"), list) else [],
        "prefix": input_items[:2],
    }
    encoded = json.dumps(
        seed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _prompt_cache_key_hash(encoded) if encoded else None


def _prompt_cache_key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _apply_message_cache_control(
    parts: list[dict[str, Any]], message: dict[str, Any]
) -> None:
    if not parts or any(isinstance(part.get("cache_control"), dict) for part in parts):
        return
    _copy_cache_control(parts[-1], message)


def _copy_cache_control_from_chat_content(target: dict[str, Any], content: Any) -> None:
    if not isinstance(content, list):
        return
    for item in reversed(content):
        if isinstance(item, dict):
            _copy_cache_control(target, item)
            if "cache_control" in target:
                return


def _chat_content_to_responses_content(
    content: Any, *, output: bool
) -> list[dict[str, Any]]:
    text_type = "output_text" if output else "input_text"
    if content is None:
        return [{"type": text_type, "text": ""}]
    if isinstance(content, str):
        return [{"type": text_type, "text": content}]
    if not isinstance(content, list):
        return [{"type": text_type, "text": str(content)}]

    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            parts.append({"type": text_type, "text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                part = {"type": text_type, "text": text}
                _copy_cache_control(part, item)
                parts.append(part)
        elif item_type == "image_url" and not output:
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if isinstance(url, str):
                part = {"type": "input_image", "image_url": url}
                _copy_cache_control(part, item)
                parts.append(part)
    return parts or [{"type": text_type, "text": ""}]


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


def _chat_message_reasoning_text(message: dict[str, Any]) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _chat_tools_to_responses_tools(raw_tools: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tools, list):
        return []
    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        item: dict[str, Any] = {
            "type": "function",
            "name": function.get("name") or "",
            "parameters": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
        if function.get("description") is not None:
            item["description"] = function["description"]
        if function.get("strict") is not None:
            item["strict"] = function["strict"]
        _copy_cache_control(item, tool)
        if "cache_control" not in item:
            _copy_cache_control(item, function)
        tools.append(item)
    return tools


def _chat_response_format_to_responses_text(
    response_format: Any,
) -> dict[str, Any] | None:
    if not isinstance(response_format, dict):
        return None
    response_type = response_format.get("type")
    if response_type == "json_object":
        return {"format": {"type": "json_object"}}
    if response_type == "json_schema":
        json_schema = response_format.get("json_schema")
        if isinstance(json_schema, dict):
            return {"format": {"type": "json_schema", **json_schema}}
    return None


def _chat_reasoning_to_responses(body: dict[str, Any]) -> dict[str, Any] | None:
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        return dict(reasoning)
    effort = body.get("reasoning_effort")
    if isinstance(effort, str) and effort:
        return {"effort": effort}
    return None


def _responses_status_to_chat_finish(status: Any) -> str:
    value = status if isinstance(status, str) else None
    return _RESPONSES_STATUS_TO_CHAT_FINISH.get(value, "stop")


def _responses_usage_to_chat_usage(raw_usage: Any) -> dict[str, Any]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = _int_value(usage.get("input_tokens"))
    output_tokens = _int_value(usage.get("output_tokens"))
    total_tokens = _int_value(usage.get("total_tokens")) or input_tokens + output_tokens
    result: dict[str, Any] = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if isinstance(usage.get("input_tokens_details"), dict):
        result["prompt_tokens_details"] = {
            "cached_tokens": _usage_detail_int(
                usage, "input_tokens_details", "cached_tokens"
            )
        }
    return result


def _json_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False)


def _usage_detail_int(usage: dict[str, Any], detail_key: str, key: str) -> int:
    details = usage.get(detail_key)
    if not isinstance(details, dict):
        return 0
    return _int_value(details.get(key))


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _coerce_index(value: Any, default: int | None = None) -> int | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    if value is None:
        return default
    return default


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
