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
_INPUT_PAYLOAD_KEYS = frozenset(
    {
        "messages",
        "prompt",
        "input",
        "contents",
        "system",
        "systemInstruction",
        "instructions",
    }
)


def _json_body_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _dump_json(value: Any) -> str | None:
    try:
        return _json_body_bytes(value).decode("utf-8")
    except (TypeError, ValueError):
        return None


def _filter_payload_content(
    value: Any, *, log_input: bool = True, log_output: bool = True
) -> Any:
    if not isinstance(value, dict):
        return value
    filtered = dict(value)
    if not log_input:
        for k in _INPUT_PAYLOAD_KEYS:
            if k in filtered:
                filtered[k] = _OMITTED_PLACEHOLDER

    if not log_output:
        if "choices" in filtered and isinstance(filtered["choices"], list):
            new_choices = []
            for item in filtered["choices"]:
                if isinstance(item, dict):
                    c = dict(item)
                    if "message" in c and isinstance(c["message"], dict):
                        m = dict(c["message"])
                        m["content"] = _OMITTED_PLACEHOLDER
                        if "reasoning_content" in m:
                            m["reasoning_content"] = _OMITTED_PLACEHOLDER
                        if "reasoning" in m:
                            m["reasoning"] = _OMITTED_PLACEHOLDER
                        if "tool_calls" in m:
                            m["tool_calls"] = _OMITTED_PLACEHOLDER
                        if "refusal" in m and m["refusal"] is not None:
                            m["refusal"] = _OMITTED_PLACEHOLDER
                        c["message"] = m
                    elif "delta" in c and isinstance(c["delta"], dict):
                        d = dict(c["delta"])
                        if "content" in d and d["content"] is not None:
                            d["content"] = _OMITTED_PLACEHOLDER
                        if "reasoning_content" in d:
                            d["reasoning_content"] = _OMITTED_PLACEHOLDER
                        if "reasoning" in d:
                            d["reasoning"] = _OMITTED_PLACEHOLDER
                        if "tool_calls" in d:
                            d["tool_calls"] = _OMITTED_PLACEHOLDER
                        c["delta"] = d
                    elif "text" in c:
                        c["text"] = _OMITTED_PLACEHOLDER
                    new_choices.append(c)
                else:
                    new_choices.append(item)
            filtered["choices"] = new_choices

        if "content" in filtered and isinstance(filtered["content"], list):
            filtered["content"] = _OMITTED_PLACEHOLDER

        if "candidates" in filtered and isinstance(filtered["candidates"], list):
            new_candidates = []
            for item in filtered["candidates"]:
                if isinstance(item, dict):
                    cand = dict(item)
                    if "content" in cand:
                        cand["content"] = _OMITTED_PLACEHOLDER
                    new_candidates.append(cand)
                else:
                    new_candidates.append(item)
            filtered["candidates"] = new_candidates

        if "output" in filtered:
            filtered["output"] = _OMITTED_PLACEHOLDER

    return filtered


def _dump_log_json(
    value: Any, *, log_input: bool = True, log_output: bool = True
) -> str | None:
    if not log_input or not log_output:
        value = _filter_payload_content(
            value, log_input=log_input, log_output=log_output
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
    content: bytes | None, *, log_input: bool = True, log_output: bool = True
) -> str | None:
    return _sanitize_log_content_text(
        _decode_content_bytes(content), log_input=log_input, log_output=log_output
    )


def _sanitize_log_content_text(
    value: str | None, *, log_input: bool = True, log_output: bool = True
) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return _sanitize_sse_log_content(
            value, log_input=log_input, log_output=log_output
        )
    return _dump_log_json(payload, log_input=log_input, log_output=log_output)


def _sanitize_sse_log_content(
    value: str, *, log_input: bool = True, log_output: bool = True
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
        sanitized = _dump_log_json(payload, log_input=log_input, log_output=log_output)
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
