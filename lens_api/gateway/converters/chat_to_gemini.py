import copy
import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from ._chat_stream import ChatToolCalls, chat_choice_index
from ._shared import (
    _extract_chat_reasoning_effort,
    _gemini_budget_to_effort,
    _gemini_thinking_config,
    _parse_sse_stream,
    format_sse_event,
)

_GEMINI_FINISH_TO_CHAT: dict[str | None, str] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    # Safety and blocklist reasons are content filtering.
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "IMAGE_SAFETY": "content_filter",
    "IMAGE_PROHIBITED_CONTENT": "content_filter",
    "IMAGE_RECITATION": "content_filter",
    "SPII": "content_filter",
}
_GEMINI_LEVEL_TO_CHAT: dict[str, str] = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "thinking_level_unspecified": "",
}

_CHAT_FINISH_TO_GEMINI: dict[str | None, str] = {
    "stop": "STOP",
    "length": "MAX_TOKENS",
    "content_filter": "SAFETY",
    "tool_calls": "STOP",
}


def chat_request_to_gemini(body: dict[str, Any]) -> dict[str, Any]:
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Chat request messages must be a list")

    contents: list[dict[str, Any]] = []
    system_parts: list[dict[str, Any]] = []
    tool_call_id_to_name: dict[str, str] = {}
    for message in raw_messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if role in {"system", "developer"}:
            text = _chat_content_to_text(message.get("content"))
            if text:
                system_parts.append({"text": text})
            continue
        if role == "tool":
            tool_name = tool_call_id_to_name.get(
                str(message.get("tool_call_id") or "")
            ) or str(message.get("name") or "tool")
            model = str(body.get("model") or "")
            text = _chat_content_to_text(message.get("content"))
            images = [
                part
                for part in _chat_content_to_gemini_parts(message.get("content"))
                if "inlineData" in part or "fileData" in part
            ]
            match = re.match(r"^gemini(?:-live)?-(\d+)", model.lower())
            multimodal = not match or int(match.group(1)) >= 3
            response: dict[str, Any] = {
                "name": tool_name,
                "response": {
                    "error" if message.get("is_error") else "output": (
                        text
                        if text.strip()
                        else "(see attached image)" if images else ""
                    )
                },
            }
            if message.get("tool_call_id") and _requires_gemini_tool_call_id(model):
                response["id"] = _normalize_gemini_tool_id(str(message["tool_call_id"]))
            if images and multimodal:
                response["parts"] = images
            _append_gemini_content(contents, "user", [{"functionResponse": response}])
            if images and not multimodal:
                contents.append(
                    {"role": "user", "parts": [{"text": "Tool result image:"}, *images]}
                )
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _chat_message_to_gemini_parts(message)
        tool_calls = message.get("tool_calls")
        if gemini_role == "model" and isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict) or not tc.get("id"):
                    continue
                function = tc.get("function")
                if isinstance(function, dict):
                    tool_call_id_to_name[str(tc["id"])] = str(
                        function.get("name") or ""
                    )
            parts.extend(
                _chat_tool_calls_to_gemini_parts(
                    tool_calls, str(body.get("model") or "")
                )
            )
        _append_gemini_content(contents, gemini_role, parts)

    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})

    gemini: dict[str, Any] = {
        "model": body.get("model") or "",
        "contents": contents,
    }
    if system_parts:
        gemini["systemInstruction"] = {"parts": system_parts}
    generation_config = _chat_generation_config(body)
    if generation_config:
        gemini["generationConfig"] = generation_config
    tools = _chat_tools_to_gemini_tools(body.get("tools"))
    if tools:
        gemini["tools"] = tools
    tool_config = _chat_tool_choice_to_gemini_config(body.get("tool_choice"))
    if tool_config:
        gemini["toolConfig"] = tool_config
    if "stream" in body:
        gemini["stream"] = body["stream"]
    return gemini


def gemini_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = body.get("systemInstruction")
    system_text = _gemini_parts_to_text(
        system.get("parts") if isinstance(system, dict) else None
    )
    if system_text:
        messages.append({"role": "system", "content": system_text})

    contents = body.get("contents")
    tool_ids = _GeminiToolIdState()
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = "assistant" if item.get("role") == "model" else "user"
            parts = item.get("parts")
            if isinstance(parts, list):
                pending: list[dict[str, Any]] = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    response = part.get("functionResponse") or part.get(
                        "function_response"
                    )
                    if isinstance(response, dict):
                        if pending:
                            messages.append(
                                _gemini_parts_to_chat_message(role, pending, tool_ids)
                            )
                            pending = []
                        result = response.get("response") or {}
                        is_error = isinstance(result, dict) and "error" in result
                        value = (
                            result.get("error" if is_error else "output", result)
                            if isinstance(result, dict)
                            else result
                        )
                        text = (
                            value
                            if isinstance(value, str)
                            else json.dumps(value, ensure_ascii=False)
                        )
                        converted = _gemini_parts_to_chat_message(
                            "tool",
                            [{"text": text}, *(response.get("parts") or [])],
                            tool_ids,
                        )
                        messages.append(
                            {
                                **(converted or {"role": "tool", "content": text}),
                                "tool_call_id": tool_ids.response_id(
                                    str(response.get("name") or ""), response.get("id")
                                ),
                                "name": str(response.get("name") or ""),
                                "is_error": is_error,
                            }
                        )
                    else:
                        pending.append(part)
                if pending:
                    messages.append(
                        _gemini_parts_to_chat_message(role, pending, tool_ids)
                    )

    chat: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages or [{"role": "user", "content": ""}],
    }
    generation_config = body.get("generationConfig")
    if isinstance(generation_config, dict):
        if "maxOutputTokens" in generation_config:
            chat["max_tokens"] = generation_config["maxOutputTokens"]
        for source, target in (
            ("temperature", "temperature"),
            ("topP", "top_p"),
            ("stopSequences", "stop"),
        ):
            if source in generation_config:
                chat[target] = generation_config[source]
        thinking_config = generation_config.get("thinkingConfig")
        if isinstance(thinking_config, dict):
            level = thinking_config.get("thinkingLevel") or thinking_config.get(
                "thinking_level"
            )
            if isinstance(level, str):
                # Gemini 3+ sends a discrete level instead of a token budget.
                effort = _GEMINI_LEVEL_TO_CHAT.get(level.lower())
            else:
                effort = _gemini_budget_to_effort(
                    thinking_config.get("thinkingBudget")
                    if "thinkingBudget" in thinking_config
                    else thinking_config.get("thinking_budget")
                )
            if effort:
                chat["reasoning_effort"] = effort
    if "stream" in body:
        chat["stream"] = body["stream"]
    tools = _gemini_tools_to_chat_tools(body.get("tools"))
    tool_config = body.get("toolConfig")
    calling_config = (
        tool_config.get("functionCallingConfig")
        if isinstance(tool_config, dict)
        else None
    )
    if isinstance(calling_config, dict):
        mode = calling_config.get("mode")
        tool_choice = {
            "NONE": "none",
            "AUTO": "auto",
            "ANY": "required",
            "VALIDATED": "auto",
        }.get(mode)
        if tool_choice is not None:
            chat["tool_choice"] = tool_choice
        if mode in ("ANY", "AUTO", "VALIDATED"):
            allowed_names = calling_config.get("allowedFunctionNames", [])
            if not isinstance(allowed_names, list) or any(
                not isinstance(name, str) or not name.strip() for name in allowed_names
            ):
                raise ValueError("Gemini allowedFunctionNames requires nonempty names")
            if allowed_names:
                if mode == "ANY" and len(allowed_names) == 1:
                    chat["tool_choice"] = {
                        "type": "function",
                        "function": {"name": allowed_names[0]},
                    }
                else:
                    declared_names = {tool["function"]["name"] for tool in tools}
                    if not set(allowed_names).issubset(declared_names):
                        raise ValueError(
                            "Gemini allowedFunctionNames requires declared functions"
                        )
                    tools = [
                        tool
                        for tool in tools
                        if tool["function"]["name"] in allowed_names
                    ]
    if tools:
        chat["tools"] = tools
    return chat


def chat_response_to_gemini(
    chat_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    parts = _chat_message_to_gemini_parts(message)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        parts.extend(
            _chat_tool_calls_to_gemini_parts(
                tool_calls, str(chat_body.get("model") or original_model)
            )
        )

    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": ""}]},
                "finishReason": _chat_finish_to_gemini(choice.get("finish_reason")),
                "index": 0,
            }
        ],
        "usageMetadata": _chat_usage_to_gemini_usage(chat_body.get("usage")),
        "modelVersion": chat_body.get("model") or original_model,
    }


def gemini_response_to_chat(
    gemini_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    candidate = (gemini_body.get("candidates") or [{}])[0]
    content = (
        candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
    )
    message = _gemini_parts_to_chat_message("assistant", content.get("parts")) or {
        "role": "assistant",
        "content": "",
    }
    usage = _gemini_usage_to_chat_usage(gemini_body.get("usageMetadata"))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": gemini_body.get("modelVersion")
        or gemini_body.get("model")
        or original_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _gemini_finish_to_chat(
                    candidate.get("finishReason"), bool(message.get("tool_calls"))
                ),
            }
        ],
        "usage": usage,
    }


async def chat_stream_to_gemini_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str
) -> AsyncIterator[bytes]:
    terminal: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    model = original_model
    tool_calls_state = ChatToolCalls()
    tool_metadata: dict[tuple[int, int], dict[str, Any]] = {}
    emitted_calls: set[tuple[int, int]] = set()
    async for payload in _parse_sse_stream(raw_iterator):
        if payload.get("error"):
            raise ValueError("Chat stream returned an error")
        model = str(payload.get("model") or model)
        _merge_gemini_stream_usage(usage, payload.get("usage"))
        for choice in payload.get("choices", []):
            mapped_finish = _chat_finish_to_gemini(choice.get("finish_reason"))
            if terminal is not None:
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            parts = _chat_message_to_gemini_parts(delta)
            choice_index = chat_choice_index(choice)
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        for call in tool_calls_state.update_many(
                            tc, choice_index=choice_index
                        ):
                            metadata = tool_metadata.setdefault(
                                (choice_index, call.index), {}
                            )
                            if "thoughtSignature" in tc:
                                metadata["thoughtSignature"] = tc["thoughtSignature"]
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                for call in tool_calls_state:
                    key = (choice_index, call.index)
                    if call.choice_index != choice_index or key in emitted_calls:
                        continue
                    emitted_calls.add(key)
                    # A truncated call is not a valid Gemini functionCall.
                    if not call.arguments_complete:
                        if finish_reason in {"length", "content_filter"}:
                            continue
                        raise ValueError(
                            "Gemini function call requires complete JSON object arguments"
                        )
                    parts.extend(
                        _chat_tool_calls_to_gemini_parts(
                            [
                                {
                                    "id": call.call_id,
                                    "function": {
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    },
                                    **tool_metadata.get(key, {}),
                                }
                            ],
                            model,
                        )
                    )
            if parts:
                candidate: dict[str, Any] = {
                    "content": {"role": "model", "parts": parts},
                    "index": 0,
                }
                item: dict[str, Any] = {
                    "candidates": [candidate],
                    "modelVersion": model,
                }
                yield format_sse_event(None, item)
            if mapped_finish is not None:
                terminal = {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": []},
                            "index": 0,
                            "finishReason": mapped_finish,
                        }
                    ],
                    "modelVersion": model,
                }

    if terminal is None:
        raise ValueError("Chat stream ended without a finish reason")
    if usage:
        terminal["usageMetadata"] = _chat_usage_to_gemini_usage(usage)
    yield format_sse_event(None, terminal)


async def gemini_stream_to_chat_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str
) -> AsyncIterator[bytes]:
    message_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    model = original_model
    created = int(time.time())
    role_sent = False
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    tool_ids = _GeminiToolIdState()
    has_tool_calls = False

    async for payload in _parse_sse_stream(raw_iterator, allow_raw_json=True):
        if payload.get("error"):
            raise ValueError("Gemini stream returned an error")
        _merge_gemini_stream_usage(usage, payload.get("usageMetadata"))
        candidate = (payload.get("candidates") or [{}])[0]
        candidate_finish = candidate.get("finishReason")
        if isinstance(candidate_finish, str) and candidate_finish:
            _gemini_finish_to_chat(candidate_finish)
        if finish_reason is not None:
            continue
        model = str(payload.get("modelVersion") or payload.get("model") or model)
        if not role_sent:
            role_sent = True
            yield _chat_stream_event(message_id, model, created, {"role": "assistant"})
        content = (
            candidate.get("content")
            if isinstance(candidate.get("content"), dict)
            else {}
        )
        delta = _gemini_parts_to_chat_delta(content.get("parts"), tool_ids)
        has_tool_calls = has_tool_calls or bool(delta.get("tool_calls"))
        if isinstance(candidate_finish, str) and candidate_finish:
            finish_reason = _gemini_finish_to_chat(candidate_finish, has_tool_calls)
        if delta:
            yield _chat_stream_event(message_id, model, created, delta)

    if finish_reason is None:
        raise ValueError("Gemini stream ended without a finish reason")
    yield _chat_stream_event(
        message_id,
        model,
        created,
        {},
        finish_reason=finish_reason,
        usage=_gemini_usage_to_chat_usage(usage),
    )
    yield b"data: [DONE]\n\n"


def _merge_gemini_stream_usage(target: dict[str, Any], update: Any) -> None:
    if not isinstance(update, dict):
        return
    for key, value in update.items():
        if value is None:
            continue
        if isinstance(value, dict):
            previous = target.setdefault(key, {})
            if isinstance(previous, dict):
                _merge_gemini_stream_usage(previous, value)
        else:
            target[key] = value


def _append_gemini_content(
    contents: list[dict[str, Any]], role: str, parts: list[dict[str, Any]]
) -> None:
    normalized_parts = parts or [{"text": ""}]
    if contents and contents[-1].get("role") == role:
        previous = contents[-1].setdefault("parts", [])
        if isinstance(previous, list) and (
            any("functionResponse" in part for part in previous)
            == any("functionResponse" in part for part in normalized_parts)
        ):
            previous.extend(normalized_parts)
            return
    contents.append({"role": role, "parts": normalized_parts})


def _chat_message_to_gemini_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts = _chat_content_to_gemini_parts(message.get("content"))
    original = message.get("_gemini_text_parts")
    if isinstance(original, list):
        # Original text parts are authoritative, including empty signed text.
        return copy.deepcopy(original) + [part for part in parts if "text" not in part]
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if (
        message.get("role", "assistant") == "assistant"
        and isinstance(reasoning, str)
        and reasoning
        and not any(part.get("thought") for part in parts)
    ):
        parts.insert(0, {"text": reasoning, "thought": True})
    return parts


def _chat_content_to_gemini_parts(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    if not isinstance(content, list):
        return [{"text": str(content)}]

    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            parts.append({"text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type in {"text", "input_text", "output_text"}:
            text = item.get("text")
            if isinstance(text, str):
                parts.append(
                    {
                        "text": text,
                        **{
                            key: item[key]
                            for key in ("thoughtSignature", "thought")
                            if key in item
                        },
                    }
                )
        elif item_type == "image_url":
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            image_part = _image_url_to_gemini_part(url)
            if image_part is not None:
                if "thoughtSignature" in item:
                    image_part["thoughtSignature"] = item["thoughtSignature"]
                parts.append(image_part)
    return parts


def _requires_gemini_tool_call_id(model: str) -> bool:
    """Match pi\'s conditional tool-call ID emission by model family."""
    identifier = model.lower()
    if identifier.startswith("claude-") or identifier.startswith("gpt-oss-"):
        return True
    match = re.match(r"^gemini(?:-live)?-(\d+)", identifier)
    return bool(match and int(match.group(1)) >= 3)


def _normalize_gemini_tool_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", value)[:64]


def _chat_tool_calls_to_gemini_parts(
    tool_calls: list[dict[str, Any]],
    model: str = "",
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    include_id = _requires_gemini_tool_call_id(model)
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        function_call: dict[str, Any] = {
            "name": function.get("name") or "",
            "args": _json_arguments_object(function.get("arguments")),
        }
        if include_id and tool_call.get("id"):
            function_call["id"] = _normalize_gemini_tool_id(str(tool_call["id"]))
        parts.append(
            {
                "functionCall": function_call,
                **(
                    {"thoughtSignature": tool_call["thoughtSignature"]}
                    if "thoughtSignature" in tool_call
                    else {}
                ),
            }
        )
    return parts


def _gemini_parts_to_chat_message(
    role: str, parts: Any, tool_ids: "_GeminiToolIdState | None" = None
) -> dict[str, Any] | None:
    text_parts: list[str] = []
    thought_parts: list[str] = []
    image_parts: list[dict[str, Any]] = []
    signed_text_parts: list[dict[str, Any]] = []
    has_signed_text = False
    tool_calls: list[dict[str, Any]] = []
    if tool_ids is None:
        tool_ids = _GeminiToolIdState()
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            signed_text_parts.append(
                {
                    "text": text,
                    **{
                        key: copy.deepcopy(part[key])
                        for key in ("thoughtSignature", "thought")
                        if key in part
                    },
                }
            )
            has_signed_text = has_signed_text or "thoughtSignature" in part
            # Gemini marks thinking with thought:true; thoughtSignature can sit on
            # any part, so only the flag decides whether text is reasoning.
            if part.get("thought") is True:
                thought_parts.append(text)
            elif text:
                text_parts.append(text)
        inline_data = part.get("inlineData") or part.get("inline_data")
        if isinstance(inline_data, dict):
            mime_type = (
                inline_data.get("mimeType")
                or inline_data.get("mime_type")
                or "image/png"
            )
            data = inline_data.get("data")
            if isinstance(data, str):
                image_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{data}"},
                        **(
                            {"thoughtSignature": part["thoughtSignature"]}
                            if "thoughtSignature" in part
                            else {}
                        ),
                    }
                )
        file_data = part.get("fileData") or part.get("file_data")
        if isinstance(file_data, dict):
            uri = file_data.get("fileUri") or file_data.get("file_uri")
            if isinstance(uri, str):
                image_parts.append({"type": "image_url", "image_url": {"url": uri}})
        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or "")
            call_id = tool_ids.call_id(name, function_call.get("id"))
            tool_calls.append(
                {
                    "id": call_id,
                    **(
                        {"thoughtSignature": part["thoughtSignature"]}
                        if "thoughtSignature" in part
                        else {}
                    ),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            function_call.get("args") or {}, ensure_ascii=False
                        ),
                    },
                }
            )
    message: dict[str, Any] = {"role": role}
    if image_parts:
        content: list[dict[str, Any]] = []
        if text_parts:
            content.append({"type": "text", "text": "".join(text_parts)})
        content.extend(image_parts)
        message["content"] = content
    else:
        message["content"] = "".join(text_parts)
    if has_signed_text or len(thought_parts) > 1:
        message["_gemini_text_parts"] = signed_text_parts
    if tool_calls:
        message["content"] = message.get("content") or None
        message["tool_calls"] = tool_calls
    if thought_parts:
        message["reasoning_content"] = "".join(thought_parts)
    return message


def _gemini_parts_to_chat_delta(
    parts: Any, tool_ids: "_GeminiToolIdState | None" = None
) -> dict[str, Any]:
    if tool_ids is None:
        tool_ids = _GeminiToolIdState()
    if not isinstance(parts, list):
        return {}
    text_parts: list[str] = []
    thought_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            if part.get("thought") is True:
                thought_parts.append(text)
            else:
                text_parts.append(text)
        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or "")
            call_id = tool_ids.call_id(name, function_call.get("id"))
            tool_calls.append(
                {
                    "index": tool_ids.index(call_id),
                    "id": call_id,
                    **(
                        {"thoughtSignature": part["thoughtSignature"]}
                        if "thoughtSignature" in part
                        else {}
                    ),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            function_call.get("args") or {}, ensure_ascii=False
                        ),
                    },
                }
            )
    delta: dict[str, Any] = {}
    converted = _gemini_parts_to_chat_message("assistant", parts)
    if converted and "_gemini_text_parts" in converted:
        delta["_gemini_text_parts"] = converted["_gemini_text_parts"]
    if converted and isinstance(converted.get("content"), list):
        delta["content"] = converted["content"]
    elif text_parts:
        delta["content"] = "".join(text_parts)
    if thought_parts:
        delta["reasoning_content"] = "".join(thought_parts)
    if tool_calls:
        delta["tool_calls"] = tool_calls
    return delta


def _gemini_parts_to_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    text_parts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    return "\n".join(item for item in text_parts if isinstance(item, str) and item)


def _chat_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _image_url_to_gemini_part(url: Any) -> dict[str, Any] | None:
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:") and ";base64," in url:
        metadata, data = url[5:].split(",", 1)
        mime_type = metadata.split(";", 1)[0] or "image/png"
        return {"inlineData": {"mimeType": mime_type, "data": data}}
    return {"fileData": {"fileUri": url}}


def _chat_generation_config(body: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    mapping = {
        "max_tokens": "maxOutputTokens",
        "max_completion_tokens": "maxOutputTokens",
        "temperature": "temperature",
        "top_p": "topP",
        "stop": "stopSequences",
    }
    for source, target in mapping.items():
        if source in body and target not in config:
            config[target] = body[source]
    effort = _extract_chat_reasoning_effort(body)
    if effort:
        thinking_config = _gemini_thinking_config(str(body.get("model") or ""), effort)
        if thinking_config is not None:
            config["thinkingConfig"] = thinking_config
    response_format = body.get("response_format")
    if isinstance(response_format, dict):
        if response_format.get("type") == "json_object":
            config["responseMimeType"] = "application/json"
        elif response_format.get("type") == "json_schema":
            schema = response_format.get("json_schema")
            if isinstance(schema, dict):
                config["responseMimeType"] = "application/json"
                config["responseSchema"] = schema.get("schema", schema)
    return config


def _chat_tools_to_gemini_tools(raw_tools: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tools, list):
        return []
    declarations: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        item = {
            "name": function.get("name") or "",
            "parametersJsonSchema": _gemini_tool_schema(
                function.get("parameters", {"type": "object", "properties": {}})
            ),
        }
        if function.get("description") is not None:
            item["description"] = function["description"]
        declarations.append(item)
    return [{"functionDeclarations": declarations}] if declarations else []


def _gemini_tools_to_chat_tools(raw_tools: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tools, list):
        return []
    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, dict):
            continue
        declarations = tool.get("functionDeclarations") or tool.get(
            "function_declarations"
        )
        if not isinstance(declarations, list):
            continue
        for declaration in declarations:
            if not isinstance(declaration, dict):
                continue
            function: dict[str, Any] = {
                "name": declaration.get("name") or "",
                "parameters": copy.deepcopy(
                    declaration.get(
                        "parametersJsonSchema",
                        declaration.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    )
                ),
            }
            if declaration.get("description") is not None:
                function["description"] = declaration["description"]
            tools.append({"type": "function", "function": function})
    return tools


def _chat_tool_choice_to_gemini_config(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        mode = {"auto": "AUTO", "required": "ANY", "none": "NONE"}.get(tool_choice)
        return {"functionCallingConfig": {"mode": mode}} if mode else None
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if tool_choice.get("type") == "function" and isinstance(function, dict):
            return {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [function.get("name") or ""],
                }
            }
    return None


def _gemini_tool_schema(value: Any, name_map: bool = False) -> Any:
    if isinstance(value, list):
        return [_gemini_tool_schema(item) for item in value]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    name_maps = {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        "dependencies",
    }
    subschemas = {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "items",
        "prefixItems",
        "additionalItems",
        "unevaluatedItems",
        "contains",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
    }
    result = {}
    for key, item in value.items():
        if name_map or key in subschemas:
            result[key] = _gemini_tool_schema(item)
        elif key in name_maps:
            result[key] = _gemini_tool_schema(item, True)
        else:
            result[key] = copy.deepcopy(item)
    if not name_map and "const" in result and "enum" not in result:
        constant = result["const"]
        if isinstance(constant, (str, int, float)) and not isinstance(constant, bool):
            result["enum"] = [result.pop("const")]
    return result


def _json_arguments_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Gemini function call requires valid JSON object arguments"
            ) from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Gemini function call requires JSON object arguments")


def _gemini_finish_to_chat(value: Any, has_tool_calls: bool = False) -> str:
    reason = value if isinstance(value, str) else None
    if reason is not None and reason not in _GEMINI_FINISH_TO_CHAT:
        raise ValueError(f"Gemini completion failed with finish reason: {reason}")
    mapped = _GEMINI_FINISH_TO_CHAT.get(reason, "stop")
    return "tool_calls" if mapped == "stop" and has_tool_calls else mapped


def _chat_finish_to_gemini(value: Any) -> str | None:
    reason = value if isinstance(value, str) else None
    if reason is None:
        return None
    if reason not in _CHAT_FINISH_TO_GEMINI:
        raise ValueError(f"Chat completion failed with finish reason: {reason}")
    return _CHAT_FINISH_TO_GEMINI[reason]


def _chat_usage_to_gemini_usage(raw_usage: Any) -> dict[str, int]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt = _int_value(usage.get("prompt_tokens"))
    completion = _int_value(usage.get("completion_tokens"))
    result: dict[str, int] = {
        "promptTokenCount": prompt,
        "candidatesTokenCount": completion,
        "totalTokenCount": _int_value(usage.get("total_tokens")) or prompt + completion,
    }
    details = usage.get("completion_tokens_details")
    reasoning = (
        _int_value(details.get("reasoning_tokens")) if isinstance(details, dict) else 0
    )
    if reasoning:
        # Gemini reports thinking tokens in their own field, as a subset of the
        # candidate total rather than an addition to it.
        result["thoughtsTokenCount"] = reasoning
        result["candidatesTokenCount"] = max(completion - reasoning, 0)
    prompt_details = usage.get("prompt_tokens_details")
    cached = (
        _int_value(prompt_details.get("cached_tokens"))
        if isinstance(prompt_details, dict)
        else 0
    )
    if cached:
        result["cachedContentTokenCount"] = cached
    return result


def _gemini_usage_to_chat_usage(raw_usage: Any) -> dict[str, int]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt = _int_value(usage.get("promptTokenCount"))
    candidates = _int_value(usage.get("candidatesTokenCount"))
    # Gemini reports thinking tokens separately; pi counts them as output.
    thoughts = _int_value(usage.get("thoughtsTokenCount"))
    completion = candidates + thoughts
    total = _int_value(usage.get("totalTokenCount")) or prompt + completion
    result: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    cached = _int_value(usage.get("cachedContentTokenCount"))
    if cached:
        result["prompt_tokens_details"] = {"cached_tokens": cached}
    if thoughts:
        result["completion_tokens_details"] = {"reasoning_tokens": thoughts}
    return result


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


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


class _GeminiToolIdState:
    """Match explicit ids first, otherwise consume same-name calls in order."""

    def __init__(self) -> None:
        self._pending: dict[str, list[str]] = {}
        self._counts: dict[str, int] = {}
        self._indices: dict[str, int] = {}

    def call_id(self, name: str, provided: Any = None) -> str:
        count = self._counts.get(name, 0) + 1
        self._counts[name] = count
        call_id = (
            provided
            if isinstance(provided, str) and provided
            else f"call_{name}_{count}"
        )
        self._pending.setdefault(name, []).append(call_id)
        self._indices.setdefault(call_id, len(self._indices))
        return call_id

    def index(self, call_id: str) -> int:
        return self._indices[call_id]

    def response_id(self, name: str, provided: Any = None) -> str:
        pending = self._pending.get(name, [])
        if isinstance(provided, str) and provided:
            if provided in pending:
                pending.remove(provided)
            return provided
        return pending.pop(0) if pending else ""
