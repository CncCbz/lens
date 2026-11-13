from __future__ import annotations

import base64
import json
import struct
import time
import uuid
import zlib
from copy import deepcopy
from typing import Any, AsyncIterator

from ._shared import (
    _anthropic_budget_to_effort,
    _clean_thinking_effort,
    _extract_chat_reasoning_effort,
    _parse_sse_stream,
    format_sse_event,
)
from .chat_to_anthropic import (
    _chat_reasoning_to_anthropic_blocks,
    chat_request_to_anthropic,
)
from .chat_to_pi import (
    _chat_chunk,
    _chat_media_to_pi,
    _chat_usage_to_pi,
    _json_arguments,
    _pi_blocks_to_chat,
    _pi_usage_to_chat,
    chat_stream_to_pi_stream,
)

__all__ = [
    "bedrock_request_to_chat",
    "chat_request_to_bedrock",
    "bedrock_response_to_chat",
    "chat_response_to_bedrock",
    "bedrock_stream_to_chat_stream",
    "chat_stream_to_bedrock_stream",
    "bedrock_eventstream_to_sse",
    "bedrock_sse_to_eventstream",
]

_STOP_TO_CHAT = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
}
_CHAT_TO_STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
_MAX_FRAME = 32 * 1024 * 1024
_MAX_HEADERS = 128 * 1024


def _base64_bytes(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, list)):
        try:
            return base64.b64encode(bytes(value)).decode("ascii")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid Bedrock binary content") from exc
    raise ValueError("Bedrock binary content must be base64 or bytes")


def _cache_part(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "text",
        "text": "",
        "cache_control": {
            "type": "ephemeral",
            **({"ttl": "1h"} if point.get("ttl") == "1h" else {}),
        },
        "_bedrock_cache_point": deepcopy(point),
    }


def _bedrock_content_to_chat(
    content: list[dict[str, Any]], role: str
) -> list[dict[str, Any]]:
    if any("toolResult" in block for block in content):
        messages: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for block in content:
            if "toolResult" in block:
                if pending:
                    messages.extend(_bedrock_content_to_chat(pending, role))
                    pending = []
                tool = block["toolResult"]
                converted = _bedrock_content_to_chat(tool.get("content") or [], "tool")
                result = converted[0] if converted else {"role": "tool", "content": ""}
                result.update(
                    role="tool",
                    tool_call_id=tool.get("toolUseId", ""),
                    is_error=tool.get("status") == "error",
                )
                messages.append(result)
            elif (
                "cachePoint" in block
                and not pending
                and messages
                and messages[-1]["role"] == "tool"
            ):
                messages[-1]["_bedrock_result_cache_point"] = deepcopy(
                    block["cachePoint"]
                )
            else:
                pending.append(block)
        if pending:
            messages.extend(_bedrock_content_to_chat(pending, role))
        return messages
    pi_blocks: list[dict[str, Any]] = []
    extra_parts: list[dict[str, Any]] = []
    # Keep cache points and native media in their original position among visible parts.
    visible_parts: list[dict[str, Any]] = []
    for block in content:
        if "text" in block:
            pi_blocks.append({"type": "text", "text": block["text"]})
            visible_parts.append({"type": "text", "text": block["text"]})
        elif "image" in block:
            image = block["image"]
            source = image.get("source") or {}
            if "bytes" in source:
                fmt = image.get("format", "png")
                data = _base64_bytes(source["bytes"])
                pi_blocks.append(
                    {"type": "image", "mimeType": f"image/{fmt}", "data": data}
                )
                visible_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{fmt};base64,{data}"},
                    }
                )
            else:
                part = {"type": "bedrock", "block": deepcopy(block)}
                visible_parts.append(part)
                extra_parts.append(part)
        elif "toolUse" in block:
            tool = block["toolUse"]
            pi_blocks.append(
                {
                    "type": "toolCall",
                    "id": tool.get("toolUseId", ""),
                    "name": tool.get("name", ""),
                    "arguments": deepcopy(tool.get("input", {})),
                }
            )
        elif "reasoningContent" in block:
            reasoning = block["reasoningContent"]
            if "redactedContent" in reasoning:
                pi_blocks.append(
                    {
                        "type": "thinking",
                        "thinking": "",
                        "thinkingSignature": _base64_bytes(
                            reasoning["redactedContent"]
                        ),
                        "redacted": True,
                    }
                )
            else:
                text = reasoning.get("reasoningText") or {}
                pi_blocks.append(
                    {
                        "type": "thinking",
                        "thinking": text.get("text", ""),
                        **(
                            {"thinkingSignature": text["signature"]}
                            if "signature" in text
                            else {}
                        ),
                    }
                )
        elif "cachePoint" in block:
            part = _cache_part(block["cachePoint"])
            visible_parts.append(part)
            extra_parts.append(part)
        elif "json" in block:
            text = json.dumps(block["json"], ensure_ascii=False)
            visible_parts.append(
                {"type": "text", "text": text, "_bedrock_json": deepcopy(block["json"])}
            )
            extra_parts.append(visible_parts[-1])
        else:
            part = {"type": "bedrock", "block": deepcopy(block)}
            visible_parts.append(part)
            extra_parts.append(part)
    message = _pi_blocks_to_chat(pi_blocks, role)
    if extra_parts:
        message["content"] = visible_parts
    if (
        message["content"] is not None
        or message.get("reasoning_blocks")
        or message.get("tool_calls")
    ):
        return [message]
    return []


def _chat_content_to_bedrock(content: Any) -> list[dict[str, Any]]:
    parts = (
        [{"type": "text", "text": content}]
        if isinstance(content, str)
        else content or []
    )
    blocks: list[dict[str, Any]] = []
    for part in parts:
        kind = part.get("type")
        if "_bedrock_json" in part:
            blocks.append({"json": deepcopy(part["_bedrock_json"])})
        elif kind == "text":
            if part.get("text"):
                blocks.append({"text": part["text"]})
        elif kind == "image_url":
            image = _chat_media_to_pi([part])[0]
            fmt = image["mimeType"].removeprefix("image/")
            if fmt == "jpg":
                fmt = "jpeg"
            if fmt not in ("png", "jpeg", "gif", "webp"):
                raise ValueError(f"Unsupported Bedrock image format: {fmt}")
            blocks.append(
                {"image": {"format": fmt, "source": {"bytes": image["data"]}}}
            )
        elif kind == "bedrock":
            blocks.append(deepcopy(part["block"]))
        else:
            raise ValueError(f"Unsupported Chat content for Bedrock: {kind}")
        if "_bedrock_cache_point" in part:
            blocks.append({"cachePoint": deepcopy(part["_bedrock_cache_point"])})
        elif isinstance(part.get("cache_control"), dict):
            cache = part["cache_control"]
            blocks.append(
                {
                    "cachePoint": {
                        "type": "default",
                        **({"ttl": "1h"} if cache.get("ttl") == "1h" else {}),
                    }
                }
            )
    return blocks


def _chat_message_to_bedrock(
    message: dict[str, Any], model: str = "", *, replay: bool = False
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    claude = "claude" in model.lower()
    for thinking in _chat_reasoning_to_anthropic_blocks(message, replay=False) or []:
        if thinking["type"] == "redacted_thinking":
            blocks.append(
                {"reasoningContent": {"redactedContent": thinking.get("data", "")}}
            )
        elif replay and claude and not thinking.get("signature"):
            if thinking.get("thinking"):
                blocks.append({"text": thinking["thinking"]})
        else:
            text = {"text": thinking.get("thinking", "")}
            if "signature" in thinking and (not replay or claude):
                text["signature"] = thinking["signature"]
            blocks.append({"reasoningContent": {"reasoningText": text}})
    blocks.extend(_chat_content_to_bedrock(message.get("content")))
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        blocks.append(
            {
                "toolUse": {
                    "toolUseId": call.get("id", ""),
                    "name": function.get("name", ""),
                    "input": _json_arguments(function.get("arguments", "{}")),
                }
            }
        )
        if isinstance(call.get("cache_control"), dict):
            blocks.append(
                {
                    "cachePoint": {
                        "type": "default",
                        **(
                            {"ttl": "1h"}
                            if call["cache_control"].get("ttl") == "1h"
                            else {}
                        ),
                    }
                }
            )
    if isinstance(message.get("cache_control"), dict):
        blocks.append(
            {
                "cachePoint": {
                    "type": "default",
                    **(
                        {"ttl": "1h"}
                        if message["cache_control"].get("ttl") == "1h"
                        else {}
                    ),
                }
            }
        )
    return blocks


def bedrock_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if body.get("system"):
        messages.extend(_bedrock_content_to_chat(body["system"], "system"))
    for message in body.get("messages") or []:
        messages.extend(
            _bedrock_content_to_chat(
                message.get("content") or [], message.get("role", "user")
            )
        )
    chat: dict[str, Any] = {
        "model": body.get("modelId", body.get("model", "")),
        "messages": messages,
    }
    for source, target in (
        ("maxTokens", "max_tokens"),
        ("temperature", "temperature"),
        ("topP", "top_p"),
        ("stopSequences", "stop"),
    ):
        if source in (body.get("inferenceConfig") or {}):
            chat[target] = deepcopy(body["inferenceConfig"][source])
    config = body.get("toolConfig") or {}
    tools: list[dict[str, Any]] = []
    pending_cache: list[dict[str, Any]] = []
    for tool in config.get("tools") or []:
        if "toolSpec" in tool:
            spec = tool["toolSpec"]
            function = {
                "name": spec.get("name", ""),
                "description": spec.get("description", ""),
                "parameters": deepcopy(spec.get("inputSchema", {}).get("json", {})),
            }
            if "strict" in spec:
                function["strict"] = spec["strict"]
            tools.append({"type": "function", "function": function})
        elif "cachePoint" in tool and tools:
            tools[-1]["cache_control"] = _cache_part(tool["cachePoint"])[
                "cache_control"
            ]
        else:
            pending_cache.append(deepcopy(tool))
    if tools:
        chat["tools"] = tools
    if pending_cache:
        chat["_bedrock_tool_extras"] = pending_cache
    choice = config.get("toolChoice") or {}
    if "tool" in choice:
        chat["tool_choice"] = {
            "type": "function",
            "function": {"name": choice["tool"].get("name", "")},
        }
    elif "any" in choice:
        chat["tool_choice"] = "required"
    elif "auto" in choice:
        chat["tool_choice"] = "auto"
    additional = body.get("additionalModelRequestFields")
    if isinstance(additional, dict):
        chat["_bedrock_additional_model_request_fields"] = deepcopy(additional)
        thinking = additional.get("thinking") or {}
        effort = _clean_thinking_effort(
            (additional.get("output_config") or {}).get("effort")
            or additional.get("reasoning_effort")
            or (additional.get("reasoning") or {}).get("effort")
        )
        if thinking.get("type") == "disabled":
            effort = "none"
        elif thinking.get("type") == "enabled":
            effort = _anthropic_budget_to_effort(thinking.get("budget_tokens"))
        if effort:
            chat["reasoning_effort"] = effort
    for key in (
        "requestMetadata",
        "guardrailConfig",
        "additionalModelResponseFieldPaths",
        "performanceConfig",
        "serviceTier",
        "promptVariables",
    ):
        if key in body:
            chat[f"_bedrock_{key}"] = deepcopy(body[key])
    if "stream" in body:
        chat["stream"] = body["stream"]
    return chat


def chat_request_to_bedrock(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body.get("messages"), list):
        raise ValueError("Chat request messages must be a list")
    model = body.get("model", "")
    result: dict[str, Any] = {"modelId": model, "messages": []}
    system: list[dict[str, Any]] = []
    for message in body["messages"]:
        role = message.get("role", "user")
        if role in ("system", "developer"):
            system.extend(_chat_message_to_bedrock(message, model, replay=True))
            continue
        if role == "tool":
            blocks = _chat_content_to_bedrock(message.get("content")) or [
                {"text": "<empty>"}
            ]
            tool_result = {
                "toolUseId": message.get("tool_call_id", ""),
                "content": blocks,
                "status": "error" if message.get("is_error") else "success",
            }
            # Claude requires error-result images after all tool results.
            images = []
            if message.get("is_error") and "claude" in model.lower():
                images = [block for block in blocks if "image" in block]
                if images:
                    tool_result["content"] = [
                        block for block in blocks if "image" not in block
                    ] or [{"text": "(image attached after the tool results)"}]
            native = {"role": "user", "content": [{"toolResult": tool_result}]}
            if result["messages"] and result["messages"][-1].get("_tool_results"):
                previous = result["messages"][-1]
                image_start = next(
                    (
                        index
                        for index, block in enumerate(previous["content"])
                        if "toolResult" not in block
                    ),
                    len(previous["content"]),
                )
                previous["content"].insert(image_start, native["content"][0])
                previous["content"].extend(images)
            else:
                native["content"].extend(images)
                native["_tool_results"] = True
                result["messages"].append(native)
            if "_bedrock_result_cache_point" in message:
                result["messages"][-1]["content"].append(
                    {"cachePoint": deepcopy(message["_bedrock_result_cache_point"])}
                )
            elif isinstance(message.get("cache_control"), dict):
                result["messages"][-1]["content"].append(
                    {
                        "cachePoint": {
                            "type": "default",
                            **(
                                {"ttl": "1h"}
                                if message["cache_control"].get("ttl") == "1h"
                                else {}
                            ),
                        }
                    }
                )
            continue
        if role not in ("user", "assistant"):
            raise ValueError(f"Unsupported Chat role for Bedrock: {role}")
        blocks = _chat_message_to_bedrock(message, model, replay=True)
        if not blocks:
            if role == "assistant":
                continue
            blocks = [{"text": "<empty>"}]
        result["messages"].append({"role": role, "content": blocks})
    for message in result["messages"]:
        message.pop("_tool_results", None)
    if system:
        result["system"] = system
    inference: dict[str, Any] = {}
    for source, target in (("temperature", "temperature"), ("top_p", "topP")):
        if source in body:
            inference[target] = body[source]
    for key in ("max_completion_tokens", "max_tokens"):
        if key in body:
            inference["maxTokens"] = body[key]
            break
    if "stop" in body:
        inference["stopSequences"] = (
            deepcopy(body["stop"]) if isinstance(body["stop"], list) else [body["stop"]]
        )
    if inference:
        result["inferenceConfig"] = inference
    tools = deepcopy(body.get("_bedrock_tool_extras") or [])
    for tool in body.get("tools") or []:
        if tool.get("type") != "function":
            raise ValueError("Bedrock supports function tools only")
        function = tool.get("function") or {}
        spec = {
            "name": function.get("name", ""),
            "description": function.get("description", ""),
            "inputSchema": {"json": deepcopy(function.get("parameters", {}))},
        }
        if "strict" in function:
            spec["strict"] = function["strict"]
        tools.append({"toolSpec": spec})
        if isinstance(tool.get("cache_control"), dict):
            tools.append(
                {
                    "cachePoint": {
                        "type": "default",
                        **(
                            {"ttl": "1h"}
                            if tool["cache_control"].get("ttl") == "1h"
                            else {}
                        ),
                    }
                }
            )
    choice = body.get("tool_choice")
    if tools and choice != "none":
        config: dict[str, Any] = {"tools": tools}
        if isinstance(choice, dict):
            config["toolChoice"] = {
                "tool": {"name": choice.get("function", {}).get("name", "")}
            }
        elif choice in ("auto", "required"):
            config["toolChoice"] = {"auto" if choice == "auto" else "any": {}}
        result["toolConfig"] = config
    additional = deepcopy(body.get("_bedrock_additional_model_request_fields") or {})
    effort = _extract_chat_reasoning_effort(body)
    if effort and not additional:
        if "claude" in model.lower():
            anthropic = chat_request_to_anthropic({**body, "messages": []})
            for key in ("thinking", "output_config"):
                if key in anthropic:
                    additional[key] = anthropic[key]
            if additional.get("thinking", {}).get("type") == "enabled":
                additional["anthropic_beta"] = ["interleaved-thinking-2025-05-14"]
                result.setdefault("inferenceConfig", {})["maxTokens"] = anthropic[
                    "max_tokens"
                ]
        elif effort != "none" and "gpt-oss" in model.lower():
            additional["reasoning_effort"] = (
                "high"
                if effort in ("xhigh", "max")
                else "low" if effort == "minimal" else effort
            )
        elif effort != "none" and "gpt-" in model.lower():
            additional["reasoning"] = {
                "effort": "low" if effort == "minimal" else effort
            }
    if additional:
        result["additionalModelRequestFields"] = additional
    for key in (
        "requestMetadata",
        "guardrailConfig",
        "additionalModelResponseFieldPaths",
        "performanceConfig",
        "serviceTier",
        "promptVariables",
    ):
        if f"_bedrock_{key}" in body:
            result[key] = deepcopy(body[f"_bedrock_{key}"])
    if "stream" in body:
        result["stream"] = body["stream"]
    return result


def _bedrock_usage_to_chat(usage: dict[str, Any]) -> dict[str, Any]:
    native = {
        "input": usage.get("inputTokens", 0),
        "output": usage.get("outputTokens", 0),
        "cacheRead": usage.get("cacheReadInputTokens", 0),
        "cacheWrite": usage.get("cacheWriteInputTokens", 0),
    }
    if "cacheDetails" in usage:
        native["cacheWrite1h"] = sum(
            item.get("inputTokens", 0)
            for item in usage["cacheDetails"]
            if item.get("ttl") == "1h"
        )
    if "reasoningTokens" in usage:
        native["reasoning"] = usage["reasoningTokens"]
    result = _pi_usage_to_chat(native)
    result["_bedrock"] = deepcopy(usage)
    return result


def _chat_usage_to_bedrock(usage: dict[str, Any]) -> dict[str, Any]:
    native = _chat_usage_to_pi(usage)
    result = deepcopy(usage.get("_bedrock") or {})
    result.update(
        inputTokens=native["input"],
        outputTokens=native["output"],
        totalTokens=result.get("totalTokens", native["totalTokens"]),
    )
    for source, target in (
        ("cacheRead", "cacheReadInputTokens"),
        ("cacheWrite", "cacheWriteInputTokens"),
        ("reasoning", "reasoningTokens"),
    ):
        if native.get(source) or source == "reasoning" and source in native:
            result[target] = native[source]
    if "cacheWrite1h" in native and "cacheDetails" not in result:
        result["cacheDetails"] = [
            {"ttl": "1h", "inputTokens": native["cacheWrite1h"]},
            {
                "ttl": "5m",
                "inputTokens": max(native["cacheWrite"] - native["cacheWrite1h"], 0),
            },
        ]
    return result


def _bedrock_stop(reason: Any) -> str:
    if reason not in _STOP_TO_CHAT:
        raise ValueError(f"Bedrock provider stopped with: {reason}")
    return _STOP_TO_CHAT[reason]


def bedrock_response_to_chat(
    body: dict[str, Any], original_model: str = ""
) -> dict[str, Any]:
    if "error" in body or any(key.endswith("Exception") for key in body):
        raise ValueError(f"Bedrock response error: {body}")
    finish = _bedrock_stop(body.get("stopReason"))
    native = (body.get("output") or {}).get("message")
    if not isinstance(native, dict):
        raise ValueError("Bedrock response missing output.message")
    messages = _bedrock_content_to_chat(native.get("content") or [], "assistant")
    message = messages[0] if messages else {"role": "assistant", "content": None}
    return {
        "id": body.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": original_model or body.get("model", ""),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _bedrock_usage_to_chat(body.get("usage") or {}),
        "_bedrock_response": {
            key: deepcopy(value)
            for key, value in body.items()
            if key not in ("output", "stopReason", "usage")
        },
        "_bedrock_stop_reason": body["stopReason"],
    }


def chat_response_to_bedrock(
    body: dict[str, Any], original_model: str = ""
) -> dict[str, Any]:
    if "error" in body:
        raise ValueError(str(body["error"]))
    choice = (body.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    if finish not in _CHAT_TO_STOP:
        raise ValueError(f"Unsupported Chat finish reason for Bedrock: {finish}")
    raw_reason = body.get("_bedrock_stop_reason")
    stop = (
        raw_reason if _STOP_TO_CHAT.get(raw_reason) == finish else _CHAT_TO_STOP[finish]
    )
    result = deepcopy(body.get("_bedrock_response") or {})
    result.update(
        output={
            "message": {
                "role": "assistant",
                "content": _chat_message_to_bedrock(
                    choice.get("message") or {}, original_model or body.get("model", "")
                ),
            }
        },
        stopReason=stop,
        usage=_chat_usage_to_bedrock(body.get("usage") or {}),
    )
    return result


async def bedrock_stream_to_chat_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    started = False
    finish: str | None = None
    failed = False
    blocks: dict[int, dict[str, Any]] = {}
    tool_count = 0
    usage: dict[str, Any] | None = None
    async for event in _parse_sse_stream(raw_iterator):
        if failed:
            continue
        error_key = next(
            (key for key in event if key.endswith("Exception") or key == "error"), None
        )
        if error_key:
            failed = True
            value = event[error_key]
            yield format_sse_event(
                None,
                {
                    "error": {
                        "message": (
                            value.get("message", str(value))
                            if isinstance(value, dict)
                            else str(value)
                        ),
                        "type": error_key,
                    }
                },
            )
            continue
        if "messageStart" in event:
            if started or event["messageStart"].get("role") != "assistant":
                raise ValueError("Invalid Bedrock messageStart")
            started = True
            yield _chat_chunk({"role": "assistant", "content": ""}, original_model)
        elif "metadata" in event:
            if "usage" in event["metadata"]:
                usage = _bedrock_usage_to_chat(event["metadata"]["usage"])
        elif "messageStop" in event:
            if not started or finish:
                raise ValueError("Invalid Bedrock messageStop")
            finish = _bedrock_stop(event["messageStop"].get("stopReason"))
        else:
            if not started or finish:
                raise ValueError("Bedrock content outside an active message")
            kind = next(iter(event), "")
            if kind not in (
                "contentBlockStart",
                "contentBlockDelta",
                "contentBlockStop",
            ):
                raise ValueError(f"Unsupported Bedrock stream event: {kind}")
            value = event[kind]
            index = value.get("contentBlockIndex")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("Invalid Bedrock contentBlockIndex")
            state = blocks.get(index)
            if kind == "contentBlockStart":
                if state is not None:
                    raise ValueError("Duplicate Bedrock content block")
                start = value.get("start") or {}
                tool = start.get("toolUse")
                if tool:
                    state = {
                        "kind": "tool",
                        "index": tool_count,
                        "arguments": "",
                        "ended": False,
                    }
                    blocks[index] = state
                    tool_count += 1
                    yield _chat_chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": state["index"],
                                    "id": tool.get("toolUseId", ""),
                                    "type": "function",
                                    "function": {
                                        "name": tool.get("name", ""),
                                        "arguments": "",
                                    },
                                }
                            ]
                        },
                        original_model,
                    )
                elif not start:
                    blocks[index] = {"kind": "unknown", "ended": False}
                else:
                    raise ValueError("Unsupported Bedrock block start")
            elif kind == "contentBlockDelta":
                delta = value.get("delta") or {}
                block_kind = (
                    "text"
                    if "text" in delta
                    else (
                        "reasoning"
                        if "reasoningContent" in delta
                        else "tool" if "toolUse" in delta else "unknown"
                    )
                )
                if state is None or state["kind"] == "unknown":
                    if block_kind not in ("text", "reasoning"):
                        raise ValueError("Bedrock tool delta without tool start")
                    state = {
                        "kind": block_kind,
                        "text": "",
                        "signature": "",
                        "redacted": bytearray(),
                        "ended": False,
                    }
                    blocks[index] = state
                if state["ended"] or state["kind"] != block_kind:
                    raise ValueError("Invalid Bedrock content block delta")
                if block_kind == "text":
                    state["text"] += delta["text"]
                    yield _chat_chunk({"content": delta["text"]}, original_model)
                elif block_kind == "tool":
                    arguments = delta["toolUse"].get("input", "")
                    state["arguments"] += arguments
                    yield _chat_chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": state["index"],
                                    "function": {"arguments": arguments},
                                }
                            ]
                        },
                        original_model,
                    )
                else:
                    reasoning = delta["reasoningContent"]
                    if "text" in reasoning:
                        state["text"] += reasoning["text"]
                        yield _chat_chunk(
                            {"reasoning_content": reasoning["text"]}, original_model
                        )
                    if "signature" in reasoning:
                        if state["redacted"]:
                            raise ValueError(
                                "Mixed signed and redacted Bedrock reasoning"
                            )
                        state["signature"] += reasoning["signature"]
                        yield _chat_chunk(
                            {"reasoning_signature": reasoning["signature"]},
                            original_model,
                        )
                    if "redactedContent" in reasoning:
                        if state["signature"]:
                            raise ValueError(
                                "Mixed signed and redacted Bedrock reasoning"
                            )
                        state["redacted"].extend(
                            base64.b64decode(
                                _base64_bytes(reasoning["redactedContent"]),
                                validate=True,
                            )
                        )
            else:
                if state is None or state["ended"]:
                    raise ValueError("Invalid Bedrock contentBlockStop")
                state["ended"] = True
                if state["kind"] == "tool":
                    _json_arguments(state["arguments"] or "{}")
                elif state["kind"] == "reasoning":
                    block = (
                        {
                            "type": "redacted_thinking",
                            "data": base64.b64encode(state["redacted"]).decode("ascii"),
                        }
                        if state["redacted"]
                        else {
                            "type": "thinking",
                            "thinking": state["text"],
                            **(
                                {"signature": state["signature"]}
                                if state["signature"]
                                else {}
                            ),
                        }
                    )
                    yield _chat_chunk({"reasoning_blocks": [block]}, original_model)
    if failed:
        return
    if finish is None:
        raise ValueError("Bedrock stream ended without a stop reason")
    for state in blocks.values():
        if state["kind"] == "tool" and not state["ended"]:
            _json_arguments(state["arguments"] or "{}")
        elif state["kind"] == "reasoning" and not state["ended"]:
            block = (
                {
                    "type": "redacted_thinking",
                    "data": base64.b64encode(state["redacted"]).decode("ascii"),
                }
                if state["redacted"]
                else {
                    "type": "thinking",
                    "thinking": state["text"],
                    **({"signature": state["signature"]} if state["signature"] else {}),
                }
            )
            yield _chat_chunk({"reasoning_blocks": [block]}, original_model)
    yield _chat_chunk({}, original_model, finish=finish, usage=usage)
    yield format_sse_event(None, "[DONE]")


async def chat_stream_to_bedrock_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    async for event in _parse_sse_stream(
        chat_stream_to_pi_stream(raw_iterator, original_model)
    ):
        kind = event["type"]
        index = event.get("contentIndex", 0)
        if kind == "start":
            yield format_sse_event(None, {"messageStart": {"role": "assistant"}})
        elif kind == "toolcall_start":
            yield format_sse_event(
                None,
                {
                    "contentBlockStart": {
                        "contentBlockIndex": index,
                        "start": {
                            "toolUse": {
                                "toolUseId": event["id"],
                                "name": event["toolName"],
                            }
                        },
                    }
                },
            )
        elif kind in ("text_delta", "thinking_delta", "toolcall_delta"):
            delta = (
                {"text": event["delta"]}
                if kind == "text_delta"
                else (
                    {"reasoningContent": {"text": event["delta"]}}
                    if kind == "thinking_delta"
                    else {"toolUse": {"input": event["delta"]}}
                )
            )
            yield format_sse_event(
                None,
                {"contentBlockDelta": {"contentBlockIndex": index, "delta": delta}},
            )
        elif kind in ("text_end", "thinking_end", "toolcall_end"):
            if kind == "thinking_end" and "contentSignature" in event:
                reasoning = {
                    "redactedContent" if event.get("redacted") else "signature": event[
                        "contentSignature"
                    ]
                }
                yield format_sse_event(
                    None,
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": index,
                            "delta": {"reasoningContent": reasoning},
                        }
                    },
                )
            yield format_sse_event(
                None, {"contentBlockStop": {"contentBlockIndex": index}}
            )
        elif kind == "done":
            stop = {"stop": "end_turn", "length": "max_tokens", "toolUse": "tool_use"}[
                event["reason"]
            ]
            yield format_sse_event(None, {"messageStop": {"stopReason": stop}})
            yield format_sse_event(
                None,
                {
                    "metadata": {
                        "usage": _chat_usage_to_bedrock(
                            _pi_usage_to_chat(event["usage"])
                        )
                    }
                },
            )
        elif kind == "error":
            yield format_sse_event(
                None,
                {
                    "modelStreamErrorException": {
                        "message": event.get("errorMessage", "Chat stream failed")
                    }
                },
            )


def _decode_headers(data: bytes) -> dict[str, Any]:
    offset = 0
    headers: dict[str, Any] = {}

    def take(length: int) -> bytes:
        nonlocal offset
        if length < 0 or offset + length > len(data):
            raise ValueError("Truncated AWS eventstream header")
        value = data[offset : offset + length]
        offset += length
        return value

    while offset < len(data):
        name_length = take(1)[0]
        if not name_length:
            raise ValueError("Empty AWS eventstream header name")
        name = take(name_length).decode("utf-8")
        if name in headers:
            raise ValueError("Duplicate AWS eventstream header")
        kind = take(1)[0]
        if kind in (0, 1):
            value: Any = kind == 0
        elif kind in (2, 3, 4, 5, 8):
            sizes = {
                2: (1, ">b"),
                3: (2, ">h"),
                4: (4, ">i"),
                5: (8, ">q"),
                8: (8, ">q"),
            }
            length, fmt = sizes[kind]
            value = struct.unpack(fmt, take(length))[0]
        elif kind in (6, 7):
            value = take(struct.unpack(">H", take(2))[0])
            if kind == 7:
                value = value.decode("utf-8")
        elif kind == 9:
            value = take(16)
        else:
            raise ValueError("Invalid AWS eventstream header type")
        headers[name] = value
    return headers


def _encode_header(name: str, value: str) -> bytes:
    encoded_name = name.encode("utf-8")
    encoded_value = value.encode("utf-8")
    if not 0 < len(encoded_name) <= 255 or len(encoded_value) > 65535:
        raise ValueError("Invalid AWS eventstream header length")
    return (
        bytes([len(encoded_name)])
        + encoded_name
        + b"\x07"
        + struct.pack(">H", len(encoded_value))
        + encoded_value
    )


async def bedrock_eventstream_to_sse(
    raw_iterator: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Decode AWS eventstream framing into internal SSE JSON union events."""
    buffer = bytearray()
    async for chunk in raw_iterator:
        buffer.extend(chunk)
        while len(buffer) >= 12:
            total, header_length, prelude_crc = struct.unpack(">III", buffer[:12])
            if (
                total < 16
                or total > _MAX_FRAME
                or header_length > _MAX_HEADERS
                or header_length > total - 16
            ):
                raise ValueError("Invalid AWS eventstream frame length")
            if zlib.crc32(buffer[:8]) & 0xFFFFFFFF != prelude_crc:
                raise ValueError("AWS eventstream prelude CRC mismatch")
            if len(buffer) < total:
                break
            frame = bytes(buffer[:total])
            del buffer[:total]
            if (
                zlib.crc32(frame[:-4]) & 0xFFFFFFFF
                != struct.unpack(">I", frame[-4:])[0]
            ):
                raise ValueError("AWS eventstream message CRC mismatch")
            headers = _decode_headers(frame[12 : 12 + header_length])
            message_type = headers.get(":message-type")
            if message_type not in ("event", "exception", "error"):
                raise ValueError("Invalid AWS eventstream message type")
            name = (
                headers.get(":event-type")
                if message_type == "event"
                else headers.get(":exception-type") or headers.get(":error-code")
            )
            if not isinstance(name, str) or not name:
                raise ValueError("AWS eventstream missing event type")
            payload = frame[12 + header_length : -4]
            if message_type == "error" and not payload:
                value = {"message": headers.get(":error-message", name)}
            else:
                try:
                    value = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("Invalid AWS eventstream JSON payload") from exc
                if not isinstance(value, dict):
                    raise ValueError("AWS eventstream payload must be an object")
            if message_type == "error" and not name.endswith("Exception"):
                value = {"type": name, **value}
                name = "error"
            yield format_sse_event(None, {name: value})
    if buffer:
        raise ValueError("Truncated AWS eventstream frame")


async def bedrock_sse_to_eventstream(
    raw_iterator: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """Encode internal SSE JSON union events as AWS eventstream frames."""
    async for event in _parse_sse_stream(raw_iterator):
        if len(event) != 1:
            raise ValueError("Bedrock SSE event must have one union member")
        name, value = next(iter(event.items()))
        if not isinstance(value, dict):
            raise ValueError("Bedrock SSE event value must be an object")
        exception = name.endswith("Exception")
        headers = b"".join(
            _encode_header(key, item)
            for key, item in (
                (
                    ":message-type",
                    (
                        "exception"
                        if exception
                        else "error" if name == "error" else "event"
                    ),
                ),
                (
                    (
                        ":exception-type"
                        if exception
                        else ":error-code" if name == "error" else ":event-type"
                    ),
                    name,
                ),
                (":content-type", "application/json"),
            )
        )
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        total = 16 + len(headers) + len(payload)
        if total > _MAX_FRAME or len(headers) > _MAX_HEADERS:
            raise ValueError("AWS eventstream frame too large")
        prelude = struct.pack(">II", total, len(headers))
        frame = (
            prelude
            + struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
            + headers
            + payload
        )
        yield frame + struct.pack(">I", zlib.crc32(frame) & 0xFFFFFFFF)
