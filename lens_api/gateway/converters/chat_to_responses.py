import hashlib
import json
import time
import uuid
from typing import Any, AsyncIterator

from ._chat_stream import ChatToolCalls, chat_choice_index
from ._shared import (
    _clean_thinking_effort,
    _copy_cache_control,
    _parse_sse_stream,
    _responses_input_to_chat_parts,
    format_sse_event,
    responses_tools_to_chat_tools,
)

_RESPONSES_STATUS_TO_CHAT_FINISH: dict[str | None, str] = {
    "completed": "stop",
    "incomplete": "length",
}

# Local mapping: shared mappings are also used by other converter workers.
FINISH_REASON_CHAT_TO_RESPONSES = {
    "stop": "completed",
    "length": "incomplete",
    "tool_calls": "completed",
    "content_filter": "incomplete",
}


# Keys both protocols accept, so they can be copied straight through.
_RESPONSES_CACHE_AND_ROUTING_KEYS = (
    "cache_control",
    "prompt_cache_key",
    "prompt_cache_retention",
    "safety_identifier",
    "service_tier",
    "parallel_tool_calls",
)

# Responses-only fields (truncation, max_tool_calls, background,
# previous_response_id, conversation, context_management) have no Chat
# Completions counterpart: forwarding them makes strict Chat upstreams reject
# the body, so only _RESPONSES_CACHE_AND_ROUTING_KEYS are copied through.


def responses_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    chat: dict[str, Any] = {}

    messages: list[dict[str, Any]] = []
    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    embedded_tools: list[dict[str, Any]] = []
    configuration_effort: str | None = None
    input_val = body.get("input", [])
    if isinstance(input_val, str):
        messages.append({"role": "user", "content": input_val})
    elif isinstance(input_val, list):
        input_messages, embedded_tools, configuration_effort = (
            _responses_input_to_chat_parts(input_val)
        )
        messages.extend(input_messages)
    chat["messages"] = messages

    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            chat[key] = body[key]

    if "tools" in body:
        chat["tools"] = responses_tools_to_chat_tools(body["tools"])
    if embedded_tools:
        # additional_tools items accumulate and replace same-name definitions
        # among themselves, but cannot replace a top-level tool (pi semantics).
        merged = responses_tools_to_chat_tools(embedded_tools)
        if merged:
            by_name = {(t.get("function") or {}).get("name", ""): t for t in merged}
            for tool in chat.get("tools") or []:
                by_name[(tool.get("function") or {}).get("name", "")] = tool
            chat["tools"] = list(by_name.values())
    if "tool_choice" in body:
        tool_choice = body["tool_choice"]
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            tool_choice = {
                "type": "function",
                "function": {"name": tool_choice.get("name", "")},
            }
        chat["tool_choice"] = tool_choice
    text_config = body.get("text")
    if isinstance(text_config, dict):
        response_format = _responses_text_to_chat_response_format(text_config)
        if response_format is not None:
            chat["response_format"] = response_format
    for key in _RESPONSES_CACHE_AND_ROUTING_KEYS:
        if key in body:
            chat[key] = body[key]

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = _clean_thinking_effort(reasoning.get("effort"))
        if effort:
            chat["reasoning_effort"] = effort
    # pi keeps the top-level reasoning.effort fixed (medium) for prompt-cache
    # stability and signals the real level via configuration_update items; the
    # in-band value wins for the converted request.
    if configuration_effort:
        chat["reasoning_effort"] = configuration_effort

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
            native_reasoning = _native_reasoning_items(message)
            input_items.extend(native_reasoning)
            model_name = str(body.get("model") or "").lower()
            if not native_reasoning and (
                "deepseek-v4" in model_name or "deepseek-reasoner" in model_name
            ):
                reasoning = message.get("reasoning_content") or message.get("reasoning")
                if not isinstance(reasoning, str) or not reasoning:
                    reasoning = " " if tool_calls else ""
                if reasoning:
                    input_items.append(
                        {
                            "type": "reasoning",
                            "content": [{"type": "reasoning_text", "text": reasoning}],
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
        "include",
        "client_metadata",
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
        tool_choice = body["tool_choice"]
        if (
            isinstance(tool_choice, dict)
            and tool_choice.get("type") == "function"
            and isinstance(tool_choice.get("function"), dict)
        ):
            tool_choice = {
                "type": "function",
                "name": tool_choice["function"].get("name", ""),
            }
        responses["tool_choice"] = tool_choice
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
    incomplete_details = _chat_finish_to_incomplete_details(finish_reason)

    output: list[dict[str, Any]] = _native_reasoning_items(message)
    msg_item: dict[str, Any] = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [],
    }
    text = _chat_content_to_text(message.get("content"))
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
        input_details: dict[str, Any] = {
            "cached_tokens": _usage_detail_int(
                usage, "prompt_tokens_details", "cached_tokens"
            )
        }
        cache_write = _usage_detail_int(
            usage, "prompt_tokens_details", "cache_creation_tokens"
        )
        if cache_write:
            input_details["cache_write_tokens"] = cache_write
        response_usage["input_tokens_details"] = input_details
    reasoning_tokens = _usage_detail_int(
        usage, "completion_tokens_details", "reasoning_tokens"
    )
    if reasoning_tokens:
        response_usage["output_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    response_body: dict[str, Any] = {
        "id": chat_body.get("id", f"resp_{uuid.uuid4().hex[:24]}"),
        "object": "response",
        "created_at": int(time.time()),
        "model": chat_body.get("model", original_model),
        "status": status,
        "output": output,
        "usage": response_usage,
    }
    if incomplete_details is not None:
        response_body["incomplete_details"] = incomplete_details
    return response_body


def responses_response_to_chat(
    responses_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    _responses_status_to_chat_finish(responses_body.get("status"))
    reasoning_items = _native_reasoning_items(
        {"_responses_reasoning_items": responses_body.get("output")}
    )
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
                if part_type == "refusal":
                    text = part.get("refusal")
                elif part_type in {"output_text", "text"}:
                    text = part.get("text")
                else:
                    text = None
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
    if reasoning_items:
        message["_responses_reasoning_items"] = reasoning_items
    reasoning_text = "\n\n".join(
        _responses_reasoning_text(item)
        for item in responses_body.get("output") or []
        if isinstance(item, dict) and item.get("type") == "reasoning"
    )
    if reasoning_text:
        message["reasoning_content"] = reasoning_text
    if tool_calls:
        message["tool_calls"] = tool_calls

    finish_reason = _responses_status_to_chat_finish(
        responses_body.get("status"),
        _responses_incomplete_reason(responses_body),
    )
    if tool_calls and finish_reason == "stop":
        # pi promotes to toolUse only when the mapped reason is stop.
        finish_reason = "tool_calls"

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
                "finish_reason": finish_reason,
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
    incomplete_details: dict[str, Any] | None = None

    next_output_index = 0
    reasoning_oi: int | None = None
    reasoning_id = f"rs_{uuid.uuid4().hex[:24]}"
    reasoning_text = ""
    native_reasoning: list[dict[str, Any]] = []
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
            if "_responses_reasoning_items" in delta:
                native_reasoning = _native_reasoning_items(delta)
            text_delta = _chat_content_to_text(delta.get("content"))
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

    if not finish_reason:
        raise ValueError("Chat stream ended without a finish reason")

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
    for index, item in enumerate(native_reasoning):
        if index == 0 and reasoning_oi is not None:
            oi = reasoning_oi
        else:
            oi = next_output_index
            next_output_index += 1
            yield format_sse_event(
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "output_index": oi,
                    "item": item,
                },
            )
        final_items[oi] = item
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
        truncated = finish_reason in {"length", "content_filter"}
        if state["args"].strip() and not truncated:
            try:
                json.loads(state["args"])
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid tool call arguments JSON") from exc
        state["item"]["status"] = "incomplete" if truncated else "completed"
        state["item"]["arguments"] = state["args"]
        final_items[oi] = state["item"]

    for oi in sorted(final_items):
        if final_items[oi]["type"] == "reasoning":
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
    incomplete_details = _chat_finish_to_incomplete_details(finish_reason)
    terminal_event = (
        "response.incomplete" if status == "incomplete" else "response.completed"
    )
    total = input_tokens + output_tokens
    usage_payload: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
    }
    if input_token_details_seen:
        usage_payload["input_tokens_details"] = {"cached_tokens": cached_input_tokens}
    terminal_response: dict[str, Any] = {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "model": resolved_model,
        "status": status,
        "output": [final_items[oi] for oi in sorted(final_items)],
        "usage": usage_payload,
    }
    if incomplete_details is not None:
        terminal_response["incomplete_details"] = incomplete_details
    yield format_sse_event(
        terminal_event,
        {"type": terminal_event, "response": terminal_response},
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
    completed_tool_indices: set[int] = set()

    text_by_index: dict[int, str] = {}
    reasoning_by_index: dict[int, str] = {}
    native_reasoning_by_index: dict[int, dict[str, Any]] = {}
    summary_parts: dict[tuple[int, int], str] = {}
    summary_parts_done: set[tuple[int, int]] = set()
    tool_by_index: dict[int, dict[str, Any]] = {}
    tool_oi_to_chat_index: dict[int, int] = {}
    emitted_args: dict[int, str] = {}
    next_tool_index = 0
    usage: dict[str, Any] = {}

    async def terminal_output_events() -> AsyncIterator[dict[str, Any]]:
        async for payload in _parse_sse_stream(raw_iterator):
            response = payload.get("response")
            if (
                not done_sent
                and payload.get("type") in ("response.completed", "response.incomplete")
                and isinstance(response, dict)
            ):
                _responses_status_to_chat_finish(
                    response.get("status")
                    or (
                        "incomplete"
                        if payload["type"] == "response.incomplete"
                        else "completed"
                    ),
                    _responses_incomplete_reason(response),
                )
                for output_index, item in enumerate(response.get("output") or []):
                    if isinstance(item, dict):
                        yield {
                            "type": "response.output_item.done",
                            "output_index": output_index,
                            "item": item,
                            "response": response,
                        }
            yield payload

    async for payload in terminal_output_events():
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
                if payload_type == "response.reasoning_summary_text.delta":
                    key = (
                        output_index,
                        _coerce_index(payload.get("summary_index"), default=0),
                    )
                    summary_parts[key] = summary_parts.get(key, "") + delta
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
            key = (output_index, _coerce_index(payload.get("summary_index"), default=0))
            if key in summary_parts_done:
                continue
            summary_parts_done.add(key)
            part = payload.get("part")
            full = part.get("text", "") if isinstance(part, dict) else ""
            emitted = summary_parts.get(key, "")
            suffix = (
                full[len(emitted) :]
                if isinstance(full, str) and full.startswith(emitted)
                else ""
            )
            delta = suffix + "\n\n"
            reasoning_by_index[output_index] = (
                reasoning_by_index.get(output_index, "") + delta
            )
            yield _chat_stream_event(
                response_id, model, created, {"reasoning_content": delta}
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
                    str(part.get("refusal") or part.get("text") or "")
                    for part in parts
                    if isinstance(part, dict)
                    and part.get("type") in ("output_text", "text", "refusal")
                )
                if full_text:
                    # pi treats the completed item as authoritative. Emit only the
                    # part that delta events did not already deliver, so text that
                    # arrives solely in this event is not lost.
                    emitted = text_by_index.get(output_index, "")
                    remaining = (
                        full_text[len(emitted) :]
                        if full_text.startswith(emitted)
                        else "" if emitted else full_text
                    )
                    text_by_index[output_index] = emitted + remaining
                    if remaining:
                        yield _chat_stream_event(
                            response_id, model, created, {"content": remaining}
                        )
            elif item_type == "function_call":
                completed_tool_indices.add(output_index)
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
                    previous_id, previous_name = state["id"], state["name"]
                    if item.get("call_id") or item.get("id"):
                        state["id"] = (
                            item.get("call_id") or item.get("id") or state["id"]
                        )
                    if item.get("name"):
                        state["name"] = item["name"]
                    if state["id"] != previous_id or state["name"] != previous_name:
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
                                        "function": {"name": state["name"]},
                                    }
                                ]
                            },
                        )
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
                native = _native_reasoning_items({"_responses_reasoning_items": [item]})
                if native:
                    native_reasoning_by_index[output_index] = {
                        **native_reasoning_by_index.get(output_index, {}),
                        **native[0],
                    }
                full = _responses_reasoning_text(item)
                emitted = reasoning_by_index.get(output_index, "")
                remaining = full[len(emitted) :] if full.startswith(emitted) else ""
                if remaining:
                    reasoning_by_index[output_index] = emitted + remaining
                    yield _chat_stream_event(
                        response_id, model, created, {"reasoning_content": remaining}
                    )
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
                base_finish = _responses_status_to_chat_finish(
                    status, _responses_incomplete_reason(response)
                )
            else:
                base_finish = "stop"
            finish_reason = base_finish
            if tool_call_seen and base_finish == "stop":
                if tool_by_index.keys() - completed_tool_indices:
                    raise ValueError(
                        "Responses stream completed with unfinished tool calls"
                    )
                # pi promotes to toolUse only when the mapped reason is stop;
                # a truncated or filtered turn must keep its real finish reason.
                finish_reason = "tool_calls"
            yield _chat_stream_event(
                response_id,
                model,
                created,
                (
                    {
                        "_responses_reasoning_items": [
                            native_reasoning_by_index[index]
                            for index in sorted(native_reasoning_by_index)
                        ]
                    }
                    if native_reasoning_by_index
                    else {}
                ),
                finish_reason=finish_reason,
                usage=_responses_usage_to_chat_usage(usage),
            )
            yield b"data: [DONE]\n\n"
            done_sent = True
            continue

        if payload_type in ("response.failed", "response.cancelled"):
            raise ValueError(
                f"Upstream Responses stream {payload_type.removeprefix('response.')}"
            )
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
            elif isinstance(item, dict) and item.get("thought") is not True:
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _native_reasoning_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    items = message.get("_responses_reasoning_items")
    if not isinstance(items, list):
        return []
    return [
        dict(item)
        for item in items
        if isinstance(item, dict)
        and item.get("type") == "reasoning"
        and isinstance(item.get("id"), str)
        and item["id"]
        and isinstance(item.get("summary"), list)
    ]


def _responses_reasoning_text(item: dict[str, Any]) -> str:
    parts = item.get("summary") or item.get("content") or []
    return "\n\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


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


def _responses_text_to_chat_response_format(
    text_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Mirror of _chat_response_format_to_responses_text for the reverse direction."""
    fmt = text_config.get("format")
    if not isinstance(fmt, dict):
        return None
    fmt_type = fmt.get("type")
    if fmt_type == "json_object":
        return {"type": "json_object"}
    if fmt_type == "json_schema":
        json_schema: dict[str, Any] = {
            key: value for key, value in fmt.items() if key != "type"
        }
        return {"type": "json_schema", "json_schema": json_schema}
    return None


def _chat_reasoning_to_responses(body: dict[str, Any]) -> dict[str, Any] | None:
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        return dict(reasoning)
    effort = body.get("reasoning_effort")
    if isinstance(effort, str) and effort:
        return {"effort": effort}
    return None


def _responses_status_to_chat_finish(status: Any, incomplete_reason: Any = None) -> str:
    value = status if isinstance(status, str) else None
    if value in {"failed", "cancelled"}:
        raise ValueError(f"Upstream Responses response {value}")
    if value == "incomplete":
        # pi keeps max-output truncation and content filtering distinct; Chat can
        # express both, so do not collapse every incomplete reason into "length".
        reason = incomplete_reason if isinstance(incomplete_reason, str) else None
        return "length" if reason == "max_output_tokens" else "content_filter"
    return _RESPONSES_STATUS_TO_CHAT_FINISH.get(value, "stop")


def _chat_finish_to_incomplete_details(finish_reason: Any) -> dict[str, Any] | None:
    """Mirror of _responses_incomplete_reason for the reverse direction.

    Without this, a converted "incomplete" response carries no reason and the
    next conversion has to guess, which turns a length truncation into a
    content_filter.
    """
    if finish_reason == "length":
        return {"reason": "max_output_tokens"}
    if finish_reason == "content_filter":
        return {"reason": "content_filter"}
    return None


def _responses_incomplete_reason(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    details = response.get("incomplete_details")
    if not isinstance(details, dict):
        return None
    reason = details.get("reason")
    return reason if isinstance(reason, str) else None


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
        input_details: dict[str, Any] = {
            "cached_tokens": _usage_detail_int(
                usage, "input_tokens_details", "cached_tokens"
            )
        }
        cache_write = _usage_detail_int(
            usage, "input_tokens_details", "cache_write_tokens"
        )
        if cache_write:
            input_details["cache_creation_tokens"] = cache_write
        result["prompt_tokens_details"] = input_details
    reasoning_tokens = _usage_detail_int(
        usage, "output_tokens_details", "reasoning_tokens"
    )
    if reasoning_tokens:
        result["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
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
