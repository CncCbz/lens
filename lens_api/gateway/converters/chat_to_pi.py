from __future__ import annotations

import json
import time
import uuid
from copy import deepcopy
from typing import Any, AsyncIterator

from ._chat_stream import ChatToolCalls, chat_choice_index
from ._shared import _extract_chat_reasoning_effort, _parse_sse_stream, format_sse_event
from .chat_to_anthropic import (
    _anthropic_usage_to_chat_usage,
    _chat_reasoning_to_anthropic_blocks,
    _chat_usage_to_anthropic_usage,
)

__all__ = [
    "pi_request_to_chat",
    "chat_request_to_pi",
    "pi_response_to_chat",
    "chat_response_to_pi",
    "pi_stream_to_chat_stream",
    "chat_stream_to_pi_stream",
]

_PI_FINISH = {"stop": "stop", "length": "length", "toolUse": "tool_calls"}
_CHAT_FINISH = {value: key for key, value in _PI_FINISH.items()}


def _json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid tool call arguments JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Tool call arguments must be a JSON object")
    return deepcopy(value)


def _same_json_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_json_value(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json_value(a, b) for a, b in zip(left, right)
        )
    return left == right


def _parse_pi_arguments(value: str) -> dict[str, Any]:
    try:
        return _json_arguments(value)
    except ValueError:
        repaired: list[str] = []
        in_string = False
        index = 0
        while index < len(value):
            char = value[index]
            if not in_string:
                repaired.append(char)
                in_string = char == '"'
            elif char == '"':
                repaired.append(char)
                in_string = False
            elif char == "\\":
                following = value[index + 1 : index + 2]
                digits = value[index + 2 : index + 6]
                if (
                    following == "u"
                    and len(digits) == 4
                    and all(c in "0123456789abcdefABCDEF" for c in digits)
                ):
                    repaired.append(value[index : index + 6])
                    index += 5
                elif following and following in '"\\/bfnrt':
                    repaired.append(value[index : index + 2])
                    index += 1
                else:
                    repaired.append("\\\\")
            else:
                repaired.append(json.dumps(char)[1:-1] if ord(char) < 32 else char)
            index += 1
        return _json_arguments("".join(repaired))


def _pi_usage_to_chat(usage: dict[str, Any]) -> dict[str, Any]:
    result = _anthropic_usage_to_chat_usage(
        {
            "input_tokens": usage.get("input", 0),
            "output_tokens": usage.get("output", 0),
            "cache_read_input_tokens": usage.get("cacheRead", 0),
            "cache_creation_input_tokens": usage.get("cacheWrite", 0),
        }
    )
    if "reasoning" in usage:
        result["completion_tokens_details"] = {"reasoning_tokens": usage["reasoning"]}
    if "cacheWrite1h" in usage:
        result["prompt_tokens_details"]["cache_creation_1h_tokens"] = usage[
            "cacheWrite1h"
        ]
    if "totalTokens" in usage:
        result["_pi_total_tokens"] = usage["totalTokens"]
    if "cost" in usage:
        result["cost"] = deepcopy(usage["cost"])
    return result


def _chat_usage_to_pi(usage: dict[str, Any]) -> dict[str, Any]:
    native = _chat_usage_to_anthropic_usage(usage)
    result: dict[str, Any] = {
        "input": native.get("input_tokens", 0),
        "output": native.get("output_tokens", 0),
        "cacheRead": native.get("cache_read_input_tokens", 0),
        "cacheWrite": native.get("cache_creation_input_tokens", 0),
        "totalTokens": usage.get("_pi_total_tokens", usage.get("total_tokens", 0)),
        "cost": deepcopy(usage.get("cost"))
        or {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    }
    if "total_tokens" not in usage and "_pi_total_tokens" not in usage:
        result["totalTokens"] = sum(
            result[key] for key in ("input", "output", "cacheRead", "cacheWrite")
        )
    details = usage.get("completion_tokens_details") or {}
    if "reasoning_tokens" in details:
        result["reasoning"] = details["reasoning_tokens"]
    prompt = usage.get("prompt_tokens_details") or {}
    if "cache_creation_1h_tokens" in prompt:
        result["cacheWrite1h"] = prompt["cache_creation_1h_tokens"]
    return result


def _pi_blocks_to_chat(blocks: Any, role: str = "assistant") -> dict[str, Any]:
    message: dict[str, Any] = {"role": role, "content": None}
    if isinstance(blocks, str):
        message["content"] = blocks
        return message
    parts: list[dict[str, Any]] = []
    reasoning: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for block in blocks or []:
        kind = block.get("type")
        if kind == "text":
            part = {"type": "text", "text": block.get("text", "")}
            if "textSignature" in block:
                part["text_signature"] = block["textSignature"]
            parts.append(part)
        elif kind == "image":
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block['mimeType']};base64,{block['data']}"
                    },
                }
            )
        elif kind == "thinking":
            if block.get("redacted"):
                reasoning.append(
                    {
                        "type": "redacted_thinking",
                        "data": block.get("thinkingSignature", ""),
                        "thinking": block.get("thinking", ""),
                    }
                )
            else:
                item = {"type": "thinking", "thinking": block.get("thinking", "")}
                if "thinkingSignature" in block:
                    item["signature"] = block["thinkingSignature"]
                reasoning.append(item)
        elif kind == "toolCall":
            call = {
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": (
                        block.get("arguments")
                        if isinstance(block.get("arguments"), str)
                        else json.dumps(block.get("arguments", {}), ensure_ascii=False)
                    ),
                },
            }
            for source, target in (
                ("thoughtSignature", "thought_signature"),
                ("namespace", "namespace"),
                ("argumentsError", "arguments_error"),
            ):
                if source in block:
                    call[target] = deepcopy(block[source])
            calls.append(call)
        else:
            raise ValueError(f"Unsupported pi content block: {kind}")
    if parts:
        message["content"] = (
            "".join(part["text"] for part in parts)
            if all(
                part["type"] == "text" and "text_signature" not in part
                for part in parts
            )
            else parts
        )
    if reasoning:
        message["reasoning_blocks"] = reasoning
        message["reasoning_content"] = "".join(
            block.get("thinking", "") for block in reasoning
        )
        if len(reasoning) == 1 and "signature" in reasoning[0]:
            message["reasoning_signature"] = reasoning[0]["signature"]
    if calls:
        message["tool_calls"] = calls
    return message


def _chat_media_to_pi(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    result: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type")
        if kind == "text":
            block = {"type": "text", "text": part.get("text", "")}
            if "text_signature" in part:
                block["textSignature"] = part["text_signature"]
            result.append(block)
        elif kind == "image_url":
            image = part.get("image_url", {})
            url = image.get("url", "") if isinstance(image, dict) else image
            if (
                not isinstance(url, str)
                or not url.startswith("data:")
                or ";base64," not in url
            ):
                raise ValueError("pi images require a base64 data URL")
            mime, data = url[5:].split(";base64,", 1)
            result.append({"type": "image", "mimeType": mime, "data": data})
        else:
            raise ValueError(f"Unsupported Chat content for pi: {kind}")
    return result


def _chat_blocks_to_pi(message: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    reasoning = message.get("reasoning_blocks")
    if not isinstance(reasoning, list) or not reasoning:
        reasoning = _chat_reasoning_to_anthropic_blocks(message, replay=False) or []
    for block in reasoning:
        if block["type"] == "redacted_thinking":
            result.append(
                {
                    "type": "thinking",
                    "thinking": block.get("thinking", ""),
                    "thinkingSignature": block.get("data", ""),
                    "redacted": True,
                }
            )
        else:
            item = {"type": "thinking", "thinking": block.get("thinking", "")}
            if "signature" in block:
                item["thinkingSignature"] = block["signature"]
            result.append(item)
    result.extend(_chat_media_to_pi(message.get("content")))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        item = {
            "type": "toolCall",
            "id": call.get("id", ""),
            "name": function.get("name", ""),
            "arguments": _json_arguments(function.get("arguments", "{}")),
        }
        for source, target in (
            ("thought_signature", "thoughtSignature"),
            ("namespace", "namespace"),
            ("arguments_error", "argumentsError"),
        ):
            if source in call:
                item[target] = deepcopy(call[source])
        result.append(item)
    return result


def pi_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    context = body.get("context") or {}
    if not isinstance(context.get("messages"), list):
        raise ValueError("pi context.messages must be a list")
    messages: list[dict[str, Any]] = []
    tools: dict[str, dict[str, Any]] = {}
    if context.get("systemPrompt"):
        messages.append({"role": "system", "content": context["systemPrompt"]})
    for tool in context.get("tools") or []:
        tools[tool["name"]] = deepcopy(tool)
    for native in context["messages"]:
        role = native.get("role")
        if role == "toolResult":
            message = _pi_blocks_to_chat(native.get("content"), "tool")
            message.update(
                tool_call_id=native.get("toolCallId", ""),
                name=native.get("toolName", ""),
                is_error=bool(native.get("isError")),
            )
        elif role in ("system", "user", "assistant"):
            message = _pi_blocks_to_chat(native.get("content"), role)
            if role == "system":
                for tool in native.get("toolsRemoved") or []:
                    tools.pop(tool["name"], None)
                for tool in native.get("toolsAdded") or []:
                    tools[tool["name"]] = deepcopy(tool)
                if "sections" in native:
                    message["_pi_sections"] = deepcopy(native["sections"])
                    section_text = "".join(
                        value
                        for value in native["sections"].values()
                        if isinstance(value, str)
                    )
                    if section_text:
                        message["content"] = (
                            (message.get("content") or "") + section_text
                            if isinstance(message.get("content"), str)
                            else [
                                *(message.get("content") or []),
                                {"type": "text", "text": section_text},
                            ]
                        )
        else:
            raise ValueError(f"Unsupported pi message role: {role}")
        message["_pi_metadata"] = {
            key: deepcopy(value)
            for key, value in native.items()
            if key not in ("role", "content", "toolCallId", "toolName", "isError")
        }
        messages.append(message)
    chat: dict[str, Any] = {"model": body.get("model", ""), "messages": messages}
    options = body.get("options") or {}
    for native, target in (
        ("temperature", "temperature"),
        ("maxTokens", "max_tokens"),
        ("reasoning", "reasoning_effort"),
        ("cacheRetention", "cache_retention"),
        ("sessionId", "session_id"),
        ("toolChoice", "tool_choice"),
    ):
        if native in options:
            chat[target] = deepcopy(options[native])
    if tools:
        chat["tools"] = [
            {"type": "function", "function": deepcopy(tool)} for tool in tools.values()
        ]
    if "stream" in body:
        chat["stream"] = body["stream"]
    return chat


def chat_request_to_pi(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body.get("messages"), list):
        raise ValueError("Chat request messages must be a list")
    messages: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for source in body["messages"]:
        role = source.get("role", "user")
        native: dict[str, Any] = deepcopy(source.get("_pi_metadata") or {})
        native.update(
            role="system" if role == "developer" else role,
            timestamp=native.get("timestamp", 0),
        )
        if role == "assistant":
            native.update(
                _assistant_message(source, str(body.get("model") or ""), native)
            )
            for call in source.get("tool_calls") or []:
                names[call.get("id", "")] = call.get("function", {}).get("name", "")
        elif role == "tool":
            call_id = source.get("tool_call_id", "")
            name = source.get("name") or names.get(call_id)
            if not name:
                raise ValueError(f"Missing tool name for tool result {call_id}")
            native.update(
                role="toolResult",
                toolCallId=call_id,
                toolName=name,
                content=_chat_media_to_pi(source.get("content")),
                isError=bool(source.get("is_error")),
            )
        elif role in ("system", "developer", "user"):
            native["content"] = (
                source.get("content")
                if isinstance(source.get("content"), str)
                else _chat_media_to_pi(source.get("content"))
            )
            if "_pi_sections" in source:
                # The rendered sections are already in the Chat prompt.
                native.pop("sections", None)
        else:
            raise ValueError(f"Unsupported Chat role for pi: {role}")
        messages.append(native)
    tools = []
    for entry in body.get("tools") or []:
        if entry.get("type") != "function":
            raise ValueError("pi supports function tools only")
        tool = entry.get("function") or {}
        declaration = {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": deepcopy(tool.get("parameters", {})),
        }
        if "constrainedSampling" in tool:
            declaration["constrainedSampling"] = deepcopy(tool["constrainedSampling"])
        elif tool.get("strict") is True:
            declaration["constrainedSampling"] = {
                "type": "json_schema",
                "strict": "require",
            }
        tools.append(declaration)
    if tools:
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": "", "timestamp": 0})
        # Preserve historical declarations; make the current set authoritative.
        current: dict[str, dict[str, Any]] = {}
        for message in messages:
            if message["role"] == "system":
                for removed in message.get("toolsRemoved") or []:
                    current.pop(removed["name"], None)
                for added in message.get("toolsAdded") or []:
                    current[added["name"]] = added
        if current != {tool["name"]: tool for tool in tools}:
            for message in messages:
                message.pop("toolsAdded", None)
                message.pop("toolsRemoved", None)
            messages[0]["toolsAdded"] = tools
    options: dict[str, Any] = {}
    for source, target in (
        ("temperature", "temperature"),
        ("cache_retention", "cacheRetention"),
        ("session_id", "sessionId"),
        ("tool_choice", "toolChoice"),
    ):
        if source in body:
            options[target] = deepcopy(body[source])
    for key in ("max_completion_tokens", "max_tokens"):
        if key in body:
            options["maxTokens"] = body[key]
            break
    effort = _extract_chat_reasoning_effort(body)
    if effort and effort != "none":
        options["reasoning"] = effort
    result = {
        "model": body.get("model", ""),
        "context": {"messages": messages},
        "options": options,
    }
    if "stream" in body:
        result["stream"] = body["stream"]
    return result


def _assistant_message(
    message: dict[str, Any], model: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    result = deepcopy(metadata or {})
    result.update(
        role="assistant",
        content=_chat_blocks_to_pi(message),
        api=result.get("api", "pi-messages"),
        provider=result.get("provider", ""),
        model=result.get("model", model),
        usage=result.get("usage", _chat_usage_to_pi({})),
        stopReason=result.get(
            "stopReason", "toolUse" if message.get("tool_calls") else "stop"
        ),
        timestamp=result.get("timestamp", 0),
    )
    return result


def pi_response_to_chat(
    body: dict[str, Any], original_model: str = ""
) -> dict[str, Any]:
    reason = body.get("stopReason")
    if reason not in _PI_FINISH:
        raise ValueError(
            body.get("errorMessage") or f"Invalid pi response stop reason: {reason}"
        )
    message = _pi_blocks_to_chat(body.get("content"))
    return {
        "id": body.get("responseId") or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(body.get("timestamp", 0) / 1000),
        "model": original_model or body.get("responseModel") or body.get("model", ""),
        "choices": [
            {"index": 0, "message": message, "finish_reason": _PI_FINISH[reason]}
        ],
        "usage": _pi_usage_to_chat(body.get("usage") or {}),
        "_pi_metadata": {
            key: deepcopy(value)
            for key, value in body.items()
            if key not in ("role", "content", "usage", "stopReason")
        },
    }


def chat_response_to_pi(
    body: dict[str, Any], original_model: str = ""
) -> dict[str, Any]:
    if "error" in body:
        raise ValueError(str(body["error"]))
    choice = (body.get("choices") or [{}])[0]
    reason = _CHAT_FINISH.get(choice.get("finish_reason"))
    if reason is None:
        raise ValueError(
            f"Invalid Chat finish reason for pi: {choice.get('finish_reason')}"
        )
    result = _assistant_message(
        choice.get("message") or {},
        original_model or body.get("model", ""),
        body.get("_pi_metadata"),
    )
    result.update(
        usage=_chat_usage_to_pi(body.get("usage") or {}),
        stopReason=reason,
        responseId=body.get("id", ""),
        timestamp=(body.get("_pi_metadata") or {}).get(
            "timestamp", int(body.get("created", time.time()) * 1000)
        ),
    )
    return result


def _chat_chunk(
    delta: dict[str, Any],
    model: str,
    *,
    finish: str | None = None,
    usage: dict[str, Any] | None = None,
    response_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> bytes:
    result: dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        result["usage"] = usage
    if metadata:
        result["_pi_metadata"] = deepcopy(metadata)
    return format_sse_event(None, result)


async def pi_stream_to_chat_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    blocks: list[dict[str, Any]] = []
    terminal = False
    started = False
    call_count = 0
    async for event in _parse_sse_stream(raw_iterator):
        if terminal:
            continue
        kind = event.get("type", "")
        if kind == "error":
            terminal = True
            yield format_sse_event(
                None,
                {
                    "error": {
                        "message": event.get("errorMessage") or "pi stream failed",
                        "type": event.get("reason", "error"),
                    }
                },
            )
            continue
        if kind == "start":
            if started:
                raise ValueError("Duplicate pi stream start")
            started = True
            yield _chat_chunk({"role": "assistant", "content": ""}, original_model)
            continue
        if not started:
            raise ValueError("pi stream event before start")
        if kind == "done":
            if event.get("reason") not in _PI_FINISH:
                raise ValueError("Invalid pi terminal reason")
            for block in blocks:
                if block["kind"] != "toolcall" or block["ended"]:
                    continue
                if event["reason"] != "length":
                    raise ValueError("pi tool call ended before toolcall_end")
                if block["text"]:
                    yield _chat_chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": block["call_index"],
                                    "function": {"arguments": block["text"]},
                                }
                            ]
                        },
                        original_model,
                    )
            terminal = True
            yield _chat_chunk(
                {},
                original_model or event.get("responseModel", ""),
                finish=_PI_FINISH[event["reason"]],
                usage=_pi_usage_to_chat(event.get("usage") or {}),
                response_id=event.get("responseId", ""),
                metadata={
                    key: event[key]
                    for key in ("providerThinkingLevel", "rewrite", "responseModel")
                    if key in event
                },
            )
            yield format_sse_event(None, "[DONE]")
            continue
        index = event.get("contentIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("Invalid pi contentIndex")
        prefix, _, phase = kind.rpartition("_")
        if prefix not in ("text", "thinking", "toolcall") or phase not in (
            "start",
            "delta",
            "end",
        ):
            raise ValueError(f"Unsupported pi event: {kind}")
        if phase == "start":
            if index != len(blocks):
                raise ValueError("Invalid pi contentIndex for block start")
            state = {"kind": prefix, "text": "", "ended": False}
            blocks.append(state)
            if prefix == "toolcall":
                state.update(
                    id=event["id"], name=event["toolName"], call_index=call_count
                )
                call_count += 1
                yield _chat_chunk(
                    {
                        "tool_calls": [
                            {
                                "index": state["call_index"],
                                "id": state["id"],
                                "type": "function",
                                "function": {"name": state["name"], "arguments": ""},
                            }
                        ]
                    },
                    original_model,
                )
            continue
        if (
            index >= len(blocks)
            or blocks[index]["kind"] != prefix
            or blocks[index]["ended"]
        ):
            raise ValueError("Invalid pi content block state")
        state = blocks[index]
        value = event.get("delta", "") if phase == "delta" else event.get("content", "")
        if prefix == "toolcall" and phase == "end":
            call = event["toolCall"]
            if call.get("id") != state["id"] or call.get("name") != state["name"]:
                raise ValueError("pi tool call identity changed")
            if "argumentsError" in call:
                raise ValueError(call["argumentsError"])
            arguments = _json_arguments(call.get("arguments", {}))
            if state["text"] and not _same_json_value(
                _parse_pi_arguments(state["text"]), arguments
            ):
                raise ValueError("pi tool call end disagrees with deltas")
            value = json.dumps(arguments, ensure_ascii=False)
        elif phase == "end":
            if not isinstance(value, str) or not value.startswith(state["text"]):
                raise ValueError("pi block end disagrees with deltas")
            value = value[len(state["text"]) :]
        if not isinstance(value, str):
            raise ValueError("pi stream delta must be text")
        state["text"] += value
        if value and (prefix != "toolcall" or phase == "end"):
            delta = (
                {
                    "tool_calls": [
                        {"index": state["call_index"], "function": {"arguments": value}}
                    ]
                }
                if prefix == "toolcall"
                else {"content" if prefix == "text" else "reasoning_content": value}
            )
            yield _chat_chunk(delta, original_model)
        if phase == "end":
            state["ended"] = True
            if prefix == "thinking":
                block = (
                    {
                        "type": "redacted_thinking",
                        "data": event.get("contentSignature", ""),
                        "thinking": state["text"],
                    }
                    if event.get("redacted")
                    else {
                        "type": "thinking",
                        "thinking": state["text"],
                        **(
                            {"signature": event["contentSignature"]}
                            if "contentSignature" in event
                            else {}
                        ),
                    }
                )
                yield _chat_chunk(
                    {
                        "reasoning_blocks": [block],
                        **(
                            {"reasoning_signature": event["contentSignature"]}
                            if "contentSignature" in event and not event.get("redacted")
                            else {}
                        ),
                    },
                    original_model,
                )
            elif prefix == "text" and "contentSignature" in event:
                yield _chat_chunk(
                    {"text_signature": event["contentSignature"]}, original_model
                )
    if not terminal:
        raise ValueError("pi stream ended without a terminal event")


async def chat_stream_to_pi_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    yield format_sse_event(None, {"type": "start"})
    blocks: list[dict[str, Any]] = []
    active: dict[str, int] = {}
    tools = ChatToolCalls()
    usage: dict[str, Any] = {}
    finish: str | None = None
    response_id = ""
    response_model = original_model
    terminal_metadata: dict[str, Any] = {}
    error: str | None = None
    async for payload in _parse_sse_stream(raw_iterator):
        if error:
            continue
        if "error" in payload:
            error = (
                str(payload["error"].get("message", payload["error"]))
                if isinstance(payload["error"], dict)
                else str(payload["error"])
            )
            continue
        response_id = payload.get("id") or response_id
        response_model = payload.get("model") or response_model
        if isinstance(payload.get("_pi_metadata"), dict):
            terminal_metadata.update(
                {
                    key: deepcopy(value)
                    for key, value in payload["_pi_metadata"].items()
                    if key in ("providerThinkingLevel", "rewrite", "responseModel")
                }
            )
        if isinstance(payload.get("usage"), dict):
            usage.update(payload["usage"])
        for choice in payload.get("choices") or []:
            if chat_choice_index(choice) != 0:
                continue
            delta = choice.get("delta") or {}
            if finish and delta:
                raise ValueError("Chat content after finish reason")
            for kind, key in (("thinking", "reasoning_content"), ("text", "content")):
                value = delta.get(key)
                if kind == "thinking" and value is None:
                    value = delta.get("reasoning")
                if value is not None and not isinstance(value, str):
                    raise ValueError("Chat stream content must be text")
                if value is not None:
                    if kind not in active:
                        active[kind] = len(blocks)
                        blocks.append({"kind": kind, "text": ""})
                        yield format_sse_event(
                            None,
                            {"type": f"{kind}_start", "contentIndex": active[kind]},
                        )
                    index = active[kind]
                    blocks[index]["text"] += value
                    if value:
                        yield format_sse_event(
                            None,
                            {
                                "type": f"{kind}_delta",
                                "contentIndex": index,
                                "delta": value,
                            },
                        )
            if "reasoning_signature" in delta and "thinking" in active:
                blocks[active["thinking"]]["signature"] = (
                    blocks[active["thinking"]].get("signature", "")
                    + delta["reasoning_signature"]
                )
            if "text_signature" in delta and "text" in active:
                index = active.pop("text")
                state = blocks[index]
                state["signature"] = delta["text_signature"]
                state["ended"] = True
                yield format_sse_event(
                    None,
                    {
                        "type": "text_end",
                        "contentIndex": index,
                        "content": state["text"],
                        "contentSignature": state["signature"],
                    },
                )
            for block in delta.get("reasoning_blocks") or []:
                if "thinking" not in active:
                    active["thinking"] = len(blocks)
                    blocks.append({"kind": "thinking", "text": ""})
                    yield format_sse_event(
                        None,
                        {"type": "thinking_start", "contentIndex": active["thinking"]},
                    )
                state = blocks[active["thinking"]]
                if not state["text"] and block.get("thinking"):
                    state["text"] = block["thinking"]
                    yield format_sse_event(
                        None,
                        {
                            "type": "thinking_delta",
                            "contentIndex": active["thinking"],
                            "delta": state["text"],
                        },
                    )
                if block.get("type") == "redacted_thinking":
                    state.update(signature=block.get("data", ""), redacted=True)
                elif "signature" in block:
                    state["signature"] = block["signature"]
                index = active.pop("thinking")
                state["ended"] = True
                end_event: dict[str, Any] = {
                    "type": "thinking_end",
                    "contentIndex": index,
                    "content": state["text"],
                }
                if "signature" in state:
                    end_event["contentSignature"] = state["signature"]
                if state.get("redacted"):
                    end_event["redacted"] = True
                yield format_sse_event(None, end_event)
            for value in delta.get("tool_calls") or []:
                for tool in tools.update_many(value):
                    if not tool.announced and tool.call_id and tool.name:
                        tool.target_index = len(blocks)
                        blocks.append({"kind": "toolcall", "tool": tool})
                        tool.announced = True
                        yield format_sse_event(
                            None,
                            {
                                "type": "toolcall_start",
                                "contentIndex": tool.target_index,
                                "id": tool.call_id,
                                "toolName": tool.name,
                            },
                        )
                    if tool.announced:
                        arguments = tool.take_argument_delta()
                        if arguments:
                            yield format_sse_event(
                                None,
                                {
                                    "type": "toolcall_delta",
                                    "contentIndex": tool.target_index,
                                    "delta": arguments,
                                },
                            )
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
    if error or finish not in _CHAT_FINISH:
        yield format_sse_event(
            None,
            {
                "type": "error",
                "reason": "error",
                "usage": _chat_usage_to_pi(usage),
                "errorMessage": error
                or (
                    "Chat stream ended without a finish reason"
                    if finish is None
                    else f"Unsupported Chat finish reason: {finish}"
                ),
            },
        )
        return
    for tool in tools:
        if not tool.announced:
            raise ValueError("Incomplete Chat tool call identity")
        _json_arguments(tool.arguments or "{}")
    for index, state in enumerate(blocks):
        if state.get("ended"):
            continue
        kind = state["kind"]
        event: dict[str, Any] = {"type": f"{kind}_end", "contentIndex": index}
        if kind == "toolcall":
            tool = state["tool"]
            event["toolCall"] = {
                "type": "toolCall",
                "id": tool.call_id,
                "name": tool.name,
                "arguments": _json_arguments(tool.arguments or "{}"),
            }
        else:
            event["content"] = state["text"]
            if "signature" in state:
                event["contentSignature"] = state["signature"]
            if state.get("redacted"):
                event["redacted"] = True
        yield format_sse_event(None, event)
    yield format_sse_event(
        None,
        {
            "type": "done",
            "reason": _CHAT_FINISH[finish],
            "usage": _chat_usage_to_pi(usage),
            "responseId": response_id,
            "responseModel": response_model,
            **terminal_metadata,
        },
    )
