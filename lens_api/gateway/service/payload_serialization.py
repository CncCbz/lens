from __future__ import annotations

import json
from typing import Any

from ...core.auth import REDACTED_CREDENTIAL_VALUE, is_sensitive_credential_name

_INLINE_BASE64_MIN_LENGTH = 256
_DATA_URL_METADATA_SCAN_LIMIT = 256
_BASE64_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
_BASE64_PAYLOAD_KEYS = frozenset({"b64_json", "image_base64"})
_MIME_KEYS = frozenset({"media_type", "mime_type", "mimetype"})
_OMITTED_PLACEHOLDER = "<omitted>"
# Generated text lives in these keys; everything else in a message/block is
# structural (role, id, type, name, status, annotations) and stays visible.
_OMIT_TEXT_KEYS = frozenset(
    {
        "content",
        "reasoning",
        "reasoning_content",
        "refusal",
        "thinking",
        "thought",
        "text",
        "arguments",
        "partial_json",
        "input",
        "args",
        "summary",
        "transcript",
    }
)
_OMIT_AUDIO_KEYS = frozenset({"data", "transcript"})
_OMIT_OUTPUT_TEXT_KEYS = frozenset({"arguments", "summary", "text", "input", "result"})
_INPUT_PAYLOAD_KEYS = frozenset(
    {
        "messages",
        "prompt",
        "input",
        "contents",
        "system",
        "systemInstruction",
        "instructions",
        "context",
        "state",
        "assets",
    }
)
_TOOLS_PAYLOAD_KEYS = frozenset(
    {"tools", "tool_choice", "toolConfig", "functions", "function_call", "questions"}
)


def _json_body_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _dump_json(value: Any) -> str | None:
    try:
        return _json_body_bytes(value).decode("utf-8")
    except (TypeError, ValueError):
        return None


def _omit_generated_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Mask generated text in a message/delta while keeping structural fields.

    Only keys that carry model output are replaced; ids, roles, tool names,
    statuses and unknown provider metadata keep their values so the log stays
    readable when output logging is off.
    """
    masked: dict[str, Any] = {}
    for key, item in value.items():
        if key in _OMIT_TEXT_KEYS:
            masked[key] = _OMITTED_PLACEHOLDER
        elif key == "audio" and isinstance(item, dict):
            masked[key] = {
                audio_key: (
                    _OMITTED_PLACEHOLDER
                    if audio_key in _OMIT_AUDIO_KEYS
                    else audio_value
                )
                for audio_key, audio_value in item.items()
            }
        elif key in {"function", "function_call"} and isinstance(item, dict):
            masked[key] = _omit_generated_fields(item)
        elif key == "tool_calls" and isinstance(item, list):
            masked[key] = [
                _omit_generated_fields(entry) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            masked[key] = item
    return masked


def _omit_output_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep a Responses output item's shape, mask the model output inside it."""
    masked: dict[str, Any] = {}
    for key, item_value in item.items():
        if key == "content" or key in _OMIT_OUTPUT_TEXT_KEYS:
            masked[key] = _OMITTED_PLACEHOLDER
        else:
            masked[key] = item_value
    return masked


def _omit_candidate_content(content: dict[str, Any]) -> dict[str, Any]:
    """Keep Gemini parts as blocks, mask the generated text/args in each part."""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return _omit_generated_fields(content)
    masked = dict(content)
    masked["parts"] = [
        _omit_part(part) if isinstance(part, dict) else part for part in parts
    ]
    return masked


def _omit_part(part: dict[str, Any]) -> dict[str, Any]:
    masked = dict(part)
    if "text" in masked:
        masked["text"] = _OMITTED_PLACEHOLDER
    function_call = masked.get("functionCall")
    if isinstance(function_call, dict) and "args" in function_call:
        masked["functionCall"] = {**function_call, "args": _OMITTED_PLACEHOLDER}
    return masked


def _omit_output_value(output: Any) -> Any:
    if not isinstance(output, list):
        return _OMITTED_PLACEHOLDER
    return [
        _omit_output_item(item) if isinstance(item, dict) else item for item in output
    ]


def _omit_response_output(payload: dict[str, Any]) -> dict[str, Any]:
    """Mask generated output inside an upstream/client response payload."""
    filtered = dict(payload)

    choices = filtered.get("choices")
    if isinstance(choices, list):
        new_choices = []
        for item in choices:
            if not isinstance(item, dict):
                new_choices.append(item)
                continue
            choice = dict(item)
            for key in ("message", "delta"):
                if isinstance(choice.get(key), dict):
                    choice[key] = _omit_generated_fields(choice[key])
            if "text" in choice:
                choice["text"] = _OMITTED_PLACEHOLDER
            new_choices.append(choice)
        filtered["choices"] = new_choices

    if isinstance(filtered.get("message"), dict):
        filtered["message"] = _omit_generated_fields(filtered["message"])

    if isinstance(filtered.get("content"), list):
        filtered["content"] = [
            _omit_generated_fields(block) if isinstance(block, dict) else block
            for block in filtered["content"]
        ]
    elif "content" in filtered:
        filtered["content"] = _OMITTED_PLACEHOLDER

    candidates = filtered.get("candidates")
    if isinstance(candidates, list):
        new_candidates = []
        for item in candidates:
            if not isinstance(item, dict):
                new_candidates.append(item)
                continue
            cand = dict(item)
            if isinstance(cand.get("content"), dict):
                cand["content"] = _omit_candidate_content(cand["content"])
            elif "content" in cand:
                cand["content"] = _OMITTED_PLACEHOLDER
            new_candidates.append(cand)
        filtered["candidates"] = new_candidates

    if "output" in filtered:
        filtered["output"] = _omit_output_value(filtered["output"])

    response = filtered.get("response")
    if isinstance(response, dict):
        inner = dict(response)
        if "output" in inner:
            inner["output"] = _omit_output_value(inner["output"])
        filtered["response"] = inner

    if isinstance(filtered.get("item"), dict):
        filtered["item"] = _omit_output_item(filtered["item"])

    if isinstance(filtered.get("content_block"), dict):
        filtered["content_block"] = _omit_generated_fields(filtered["content_block"])

    delta = filtered.get("delta")
    if isinstance(delta, dict):
        filtered["delta"] = _omit_generated_fields(delta)
    elif "delta" in filtered:
        filtered["delta"] = _OMITTED_PLACEHOLDER

    for key in ("text", "arguments", "partial_json", "summary"):
        if key in filtered:
            filtered[key] = _OMITTED_PLACEHOLDER

    for key in ("contentBlockStart", "contentBlockDelta"):
        block_event = filtered.get(key)
        if isinstance(block_event, dict):
            event = dict(block_event)
            for field in ("start", "delta"):
                block = event.get(field)
                if isinstance(block, dict):
                    block = _omit_generated_fields(block)
                    for nested in ("reasoningContent", "toolUse"):
                        if isinstance(block.get(nested), dict):
                            block[nested] = _omit_generated_fields(block[nested])
                    event[field] = block
            filtered[key] = event
    if isinstance(filtered.get("toolCall"), dict):
        filtered["toolCall"] = _omit_generated_fields(filtered["toolCall"])
    if filtered.get("type") in {"text_end", "thinking_end"} and "content" in filtered:
        filtered["content"] = _OMITTED_PLACEHOLDER
    for key in ("answers", "decisions", "predictions"):
        if key in filtered:
            filtered[key] = _OMITTED_PLACEHOLDER
    return filtered


def _filter_payload_content(
    value: Any,
    *,
    log_input: bool = True,
    log_output: bool = True,
    log_tools: bool = True,
) -> Any:
    if not isinstance(value, dict):
        return value
    filtered = dict(value)
    if not log_input:
        for k in _INPUT_PAYLOAD_KEYS:
            if k in filtered:
                filtered[k] = _OMITTED_PLACEHOLDER

    if not log_tools:
        for k in _TOOLS_PAYLOAD_KEYS:
            if k in filtered:
                filtered[k] = _OMITTED_PLACEHOLDER
        context = filtered.get("context")
        if isinstance(context, dict):
            context = dict(context)
            if "tools" in context:
                context["tools"] = _OMITTED_PLACEHOLDER
            if isinstance(context.get("messages"), list):
                context["messages"] = [
                    (
                        {
                            key: (
                                _OMITTED_PLACEHOLDER
                                if key in {"toolsAdded", "toolsRemoved"}
                                else value
                            )
                            for key, value in message.items()
                        }
                        if isinstance(message, dict)
                        else message
                    )
                    for message in context["messages"]
                ]
            filtered["context"] = context
        options = filtered.get("options")
        if isinstance(options, dict) and "toolChoice" in options:
            filtered["options"] = {**options, "toolChoice": _OMITTED_PLACEHOLDER}

    if not log_output:
        filtered = _omit_response_output(filtered)

    return filtered


def _dump_log_json(
    value: Any,
    *,
    log_input: bool = True,
    log_output: bool = True,
    log_tools: bool = True,
) -> str | None:
    if not log_input or not log_output or not log_tools:
        value = _filter_payload_content(
            value, log_input=log_input, log_output=log_output, log_tools=log_tools
        )
    sanitized, changed = _sanitize_log_payload(value)
    return _dump_json(sanitized if changed else value)


def _decode_content_bytes(content: bytes | None) -> str | None:
    if not content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")


def _decode_log_content_bytes(
    content: bytes | None,
    *,
    log_input: bool = True,
    log_output: bool = True,
    log_tools: bool = True,
) -> str | None:
    return _sanitize_log_content_text(
        _decode_content_bytes(content),
        log_input=log_input,
        log_output=log_output,
        log_tools=log_tools,
    )


def _sanitize_log_content_text(
    value: str | None,
    *,
    log_input: bool = True,
    log_output: bool = True,
    log_tools: bool = True,
) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return _sanitize_sse_log_content(
            value,
            log_input=log_input,
            log_output=log_output,
            log_tools=log_tools,
        )
    return _dump_log_json(
        payload, log_input=log_input, log_output=log_output, log_tools=log_tools
    )


def _sanitize_sse_log_content(
    value: str,
    *,
    log_input: bool = True,
    log_output: bool = True,
    log_tools: bool = True,
) -> str:
    lines = value.splitlines(keepends=True)
    data_lines: list[tuple[int, str]] = []

    def sanitize_event() -> None:
        if not data_lines:
            return
        try:
            payload = json.loads("\n".join(item[1] for item in data_lines))
        except (TypeError, ValueError):
            data_lines.clear()
            return
        sanitized = _dump_log_json(
            payload, log_input=log_input, log_output=log_output, log_tools=log_tools
        )
        if sanitized is None:
            data_lines.clear()
            return
        first_index = data_lines[0][0]
        first_line = lines[first_index]
        content = first_line.rstrip("\r\n")
        newline = first_line[len(content) :]
        raw_payload = content.split(":", 1)[1] if ":" in content else ""
        whitespace = raw_payload[: len(raw_payload) - len(raw_payload.lstrip())]
        lines[first_index] = f"data:{whitespace}{sanitized}{newline}"
        for index, _ in data_lines[1:]:
            lines[index] = ""
        data_lines.clear()

    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        if not content:
            sanitize_event()
            continue
        if content == "data" or content.startswith("data:"):
            raw_payload = content.split(":", 1)[1] if ":" in content else ""
            data_lines.append((index, raw_payload.removeprefix(" ")))
    sanitize_event()
    return "".join(lines)


def _stringify_text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _sanitize_log_payload(
    value: Any,
    *,
    key: str | None = None,
    parent_type: str = "",
    parent_has_mime_key: bool = False,
) -> tuple[Any, bool]:
    if isinstance(value, dict):
        next_parent_type = str(value.get("type") or "")
        next_parent_has_mime_key = any(
            str(item).lower() in _MIME_KEYS for item in value
        )
        result: dict[Any, Any] | None = None
        for item_key, item_value in value.items():
            sanitized_value, changed = _sanitize_log_payload(
                item_value,
                key=str(item_key),
                parent_type=next_parent_type,
                parent_has_mime_key=next_parent_has_mime_key,
            )
            if result is None and changed:
                result = dict(value)
            if result is not None:
                result[item_key] = sanitized_value
        if result is not None:
            return result, True
        return value, False
    if isinstance(value, list):
        result: list[Any] | None = None
        for index, item in enumerate(value):
            sanitized_item, changed = _sanitize_log_payload(
                item,
                key=key,
                parent_type=parent_type,
                parent_has_mime_key=parent_has_mime_key,
            )
            if result is None and changed:
                result = list(value)
            if result is not None:
                result[index] = sanitized_item
        if result is not None:
            return result, True
        return value, False
    if isinstance(value, str):
        sanitized = _sanitize_log_string(
            value,
            key=(key or "").lower(),
            parent_type=parent_type.lower(),
            parent_has_mime_key=parent_has_mime_key,
        )
        return sanitized, sanitized != value
    return value, False


def _sanitize_log_string(
    value: str,
    *,
    key: str,
    parent_type: str,
    parent_has_mime_key: bool,
) -> str:
    if is_sensitive_credential_name(key):
        return REDACTED_CREDENTIAL_VALUE
    redacted_data_url = _redact_data_url(value)
    if redacted_data_url is not None:
        return redacted_data_url
    if _should_redact_base64_string(
        value,
        key=key,
        parent_type=parent_type,
        parent_has_mime_key=parent_has_mime_key,
    ):
        return _base64_placeholder(value)
    return value


def _redact_data_url(value: str) -> str | None:
    if not value.startswith("data:"):
        return None
    comma_index = value.find(",", 5, _DATA_URL_METADATA_SCAN_LIMIT)
    if comma_index < 0:
        return None
    metadata = value[5:comma_index]
    if not any(part.lower() == "base64" for part in metadata.split(";")):
        return None
    return (
        f"data:{metadata},"
        f"{_base64_placeholder_length(len(value) - comma_index - 1)}"
    )


def _should_redact_base64_string(
    value: str,
    *,
    key: str,
    parent_type: str,
    parent_has_mime_key: bool,
) -> bool:
    if not _looks_like_base64(value):
        return False
    if key in _BASE64_PAYLOAD_KEYS:
        return True
    if key == "result" and parent_type == "image_generation_call":
        return True
    if key == "data" and (parent_type == "base64" or parent_has_mime_key):
        return True
    return False


def _looks_like_base64(value: str) -> bool:
    if len(value) < _INLINE_BASE64_MIN_LENGTH:
        return False
    payload_chars = 0
    for char in value:
        if char.isspace():
            continue
        if char not in _BASE64_CHARS:
            return False
        payload_chars += 1
    return payload_chars >= _INLINE_BASE64_MIN_LENGTH


def _base64_placeholder(value: str) -> str:
    return _base64_placeholder_length(len(value))


def _base64_placeholder_length(length: int) -> str:
    return f"<base64 omitted length={length}>"
