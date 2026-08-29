import json
import time
import uuid
from typing import Any, AsyncIterator

from ._chat_stream import ChatToolCalls, chat_choice_index
from ._shared import (
    _effort_to_gemini_budget,
    _extract_chat_reasoning_effort,
    _gemini_budget_to_effort,
    _parse_sse_stream,
    format_sse_event,
)

_GEMINI_FINISH_TO_CHAT: dict[str | None, str] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
    "OTHER": "content_filter",
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
            _append_gemini_content(
                contents,
                "user",
                [
                    {
                        "functionResponse": {
                            "name": tool_name,
                            "response": {
                                "name": tool_name,
                                "content": _chat_content_to_text(
                                    message.get("content")
                                ),
                            },
                        }
                    }
                ],
            )
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _chat_content_to_gemini_parts(message.get("content"))
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
            parts.extend(_chat_tool_calls_to_gemini_parts(tool_calls))
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
            message = _gemini_parts_to_chat_message(role, parts, tool_ids)
            if message is not None:
                messages.append(message)

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
    if tools:
        chat["tools"] = tools
    return chat


def chat_response_to_gemini(
    chat_body: dict[str, Any], original_model: str
) -> dict[str, Any]:
    choice = (chat_body.get("choices") or [{}])[0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    parts = _chat_content_to_gemini_parts(message.get("content"))
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        parts.extend(_chat_tool_calls_to_gemini_parts(tool_calls))

    usage = chat_body.get("usage") if isinstance(chat_body.get("usage"), dict) else {}
    prompt_tokens = _int_value(usage.get("prompt_tokens"))
    completion_tokens = _int_value(usage.get("completion_tokens"))
    total_tokens = (
        _int_value(usage.get("total_tokens")) or prompt_tokens + completion_tokens
    )
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": ""}]},
                "finishReason": _chat_finish_to_gemini(choice.get("finish_reason")),
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens,
            "totalTokenCount": total_tokens,
        },
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
                "finish_reason": _gemini_finish_to_chat(candidate.get("finishReason")),
            }
        ],
        "usage": usage,
    }


async def chat_stream_to_gemini_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str
) -> AsyncIterator[bytes]:
    finished = False
    tool_calls_state = ChatToolCalls()
    async for payload in _parse_sse_stream(raw_iterator):
        for choice in payload.get("choices", []):
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            parts: list[dict[str, Any]] = []
            text = delta.get("content")
            if isinstance(text, str) and text:
                parts.append({"text": text})
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                normalized: list[dict[str, Any]] = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        normalized.extend(
                            tool_calls_state.normalize_tool_call(
                                tc, choice_index=chat_choice_index(choice)
                            )
                        )
                parts.extend(_chat_tool_calls_to_gemini_parts(normalized))
            finish_reason = choice.get("finish_reason")
            usage = (
                payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            )
            if parts or finish_reason or usage:
                candidate: dict[str, Any] = {
                    "content": {"role": "model", "parts": parts},
                    "index": 0,
                }
                # Only a real Chat finish reason maps to a Gemini finish
                # reason; intermediate chunks must not look terminated.
                mapped_finish = _chat_finish_to_gemini(finish_reason)
                if mapped_finish is not None:
                    candidate["finishReason"] = mapped_finish
                    finished = True
                item: dict[str, Any] = {
                    "candidates": [candidate],
                    "modelVersion": payload.get("model") or original_model,
                }
                if usage:
                    prompt = _int_value(usage.get("prompt_tokens"))
                    completion = _int_value(usage.get("completion_tokens"))
                    item["usageMetadata"] = {
                        "promptTokenCount": prompt,
                        "candidatesTokenCount": completion,
                        "totalTokenCount": _int_value(usage.get("total_tokens"))
                        or prompt + completion,
                    }
                yield format_sse_event(None, item)

    # A cleanly ended Chat stream ([DONE]/finish) always terminates the Gemini
    # candidate stream; emit a terminal candidate if the source never carried a
    # finish reason.
    if not finished:
        yield format_sse_event(
            None,
            {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": []},
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ],
                "modelVersion": original_model,
            },
        )


async def gemini_stream_to_chat_stream(
    raw_iterator: AsyncIterator[bytes], original_model: str
) -> AsyncIterator[bytes]:
    message_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    model = original_model
    created = int(time.time())
    role_sent = False
    done_sent = False
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    tool_ids = _GeminiToolIdState()

    async for payload in _parse_sse_stream(raw_iterator, allow_raw_json=True):
        if done_sent:
            continue
        model = str(payload.get("modelVersion") or payload.get("model") or model)
        if not role_sent:
            role_sent = True
            yield _chat_stream_event(message_id, model, created, {"role": "assistant"})
        candidate = (payload.get("candidates") or [{}])[0]
        content = (
            candidate.get("content")
            if isinstance(candidate.get("content"), dict)
            else {}
        )
        delta = _gemini_parts_to_chat_delta(content.get("parts"), tool_ids)
        candidate_finish = candidate.get("finishReason")
        if isinstance(candidate_finish, str) and candidate_finish:
            finish_reason = _gemini_finish_to_chat(candidate_finish)
        raw_usage = payload.get("usageMetadata")
        if isinstance(raw_usage, dict):
            usage = raw_usage
        if delta:
            yield _chat_stream_event(message_id, model, created, delta)
        if isinstance(candidate_finish, str) and candidate_finish:
            yield _chat_stream_event(
                message_id,
                model,
                created,
                {},
                finish_reason=finish_reason,
                usage=_gemini_usage_to_chat_usage(usage),
            )
            yield b"data: [DONE]\n\n"
            done_sent = True
            continue

    if not done_sent:
        raise ValueError("Gemini stream ended without a finish reason")


def _append_gemini_content(
    contents: list[dict[str, Any]], role: str, parts: list[dict[str, Any]]
) -> None:
    normalized_parts = parts or [{"text": ""}]
    if contents and contents[-1].get("role") == role:
        previous = contents[-1].setdefault("parts", [])
        if isinstance(previous, list):
            previous.extend(normalized_parts)
            return
    contents.append({"role": role, "parts": normalized_parts})


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
                parts.append({"text": text})
        elif item_type == "image_url":
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            image_part = _image_url_to_gemini_part(url)
            if image_part is not None:
                parts.append(image_part)
    return parts


def _chat_tool_calls_to_gemini_parts(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        parts.append(
            {
                "functionCall": {
                    "name": function.get("name") or "",
                    "args": _json_arguments_object(function.get("arguments")),
                }
            }
        )
    return parts


def _gemini_parts_to_chat_message(
    role: str, parts: Any, tool_ids: "_GeminiToolIdState | None" = None
) -> dict[str, Any] | None:
    text_parts: list[str] = []
    image_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    if tool_ids is None:
        tool_ids = _GeminiToolIdState()
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
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
                    }
                )
        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or "")
            call_id = tool_ids.call_id(name, function_call.get("id"))
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            function_call.get("args") or {}, ensure_ascii=False
                        ),
                    },
                }
            )
        function_response = part.get("functionResponse") or part.get(
            "function_response"
        )
        if isinstance(function_response, dict):
            name = str(function_response.get("name") or "")
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_ids.response_id(name),
                    "content": json.dumps(
                        function_response.get("response") or {}, ensure_ascii=False
                    ),
                }
            )
    if tool_results:
        return tool_results[0]
    message: dict[str, Any] = {"role": role}
    if image_parts:
        content: list[dict[str, Any]] = []
        if text_parts:
            content.append({"type": "text", "text": "\n".join(text_parts)})
        content.extend(image_parts)
        message["content"] = content
    else:
        message["content"] = "\n".join(text_parts)
    if tool_calls:
        message["content"] = message.get("content") or None
        message["tool_calls"] = tool_calls
    return message


def _gemini_parts_to_chat_delta(
    parts: Any, tool_ids: "_GeminiToolIdState | None" = None
) -> dict[str, Any]:
    if tool_ids is None:
        tool_ids = _GeminiToolIdState()
    if not isinstance(parts, list):
        return {}
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
        function_call = part.get("functionCall") or part.get("function_call")
        if isinstance(function_call, dict):
            name = str(function_call.get("name") or "")
            call_id = tool_ids.call_id(name, function_call.get("id"))
            tool_calls.append(
                {
                    "index": len(tool_calls),
                    "id": call_id,
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
    if text_parts:
        delta["content"] = "".join(text_parts)
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
        if effort == "none":
            config["thinkingConfig"] = {"thinkingBudget": 0}
        else:
            budget = _effort_to_gemini_budget(effort)
            if budget:
                config["thinkingConfig"] = {"thinkingBudget": budget}
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
            "parameters": function.get("parameters")
            or {"type": "object", "properties": {}},
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
                "parameters": declaration.get("parameters")
                or {"type": "object", "properties": {}},
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


def _json_arguments_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _gemini_finish_to_chat(value: Any) -> str:
    reason = value if isinstance(value, str) else None
    return _GEMINI_FINISH_TO_CHAT.get(reason, "stop")


def _chat_finish_to_gemini(value: Any) -> str | None:
    reason = value if isinstance(value, str) else None
    if reason is None:
        return None
    return _CHAT_FINISH_TO_GEMINI.get(reason, "STOP")


def _gemini_usage_to_chat_usage(raw_usage: Any) -> dict[str, int]:
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt = _int_value(usage.get("promptTokenCount"))
    completion = _int_value(usage.get("candidatesTokenCount"))
    total = _int_value(usage.get("totalTokenCount")) or prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


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
    """Deterministic Gemini functionCall/functionResponse id association.

    Gemini associates function responses by name, so we map a logical call name
    to a stable Chat tool_call id: preserve an upstream id when one exists,
    otherwise derive ``call_{name}_{n}``. The id for a given name is reused
    across chunks so a fragmented call stays one Chat tool call.
    """

    def __init__(self) -> None:
        self._name_to_id: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def call_id(self, name: str, provided: Any = None) -> str:
        if isinstance(provided, str) and provided:
            self._name_to_id[name] = provided
            return provided
        count = self._counts.get(name, 0) + 1
        self._counts[name] = count
        call_id = f"call_{name}_{count}"
        self._name_to_id[name] = call_id
        return call_id

    def response_id(self, name: str) -> str:
        return self._name_to_id.get(name, "")
