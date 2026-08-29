from __future__ import annotations

import time
import uuid

from .runtime_context import (
    Any,
    ProtocolKind,
    deepcopy,
    json,
)
from ..converters._chat_stream import ChatToolCalls
from .payload_serialization import _dump_log_json
from .usage import _parse_ndjson_payloads, _parse_sse_payloads


def _distill_stream_response_content(
    protocol: ProtocolKind, raw_content: str | None
) -> str | None:
    """Restore a complete upstream stream to one upstream-native JSON object.

    Returns ``None`` when the stream lacks the protocol's required terminal
    evidence, and raises ``ValueError`` for malformed content. Callers must
    never fall back to returning raw SSE as if it were JSON.
    """
    if not raw_content:
        return None

    if protocol == ProtocolKind.OPENAI_CHAT:
        restored = _restore_openai_chat_stream(_parse_sse_payloads(raw_content))
        if restored is not None:
            return _dump_log_json(restored)
        return None

    if protocol == ProtocolKind.OPENAI_RESPONSES:
        payloads = _parse_sse_payloads(raw_content)
        for payload in reversed(payloads):
            if payload.get("type") != "response.completed":
                continue
            response_payload = payload.get("response")
            if isinstance(response_payload, dict):
                compact_payload = _compact_openai_response_payload(
                    _restore_openai_response_output(response_payload, payloads)
                )
                return _dump_log_json(compact_payload)
        return None
    if protocol == ProtocolKind.ANTHROPIC:
        payloads = _parse_sse_payloads(raw_content)
        has_start = any(p.get("type") == "message_start" for p in payloads)
        has_stop = any(p.get("type") == "message_stop" for p in payloads)
        if has_start and has_stop:
            restored_message = _restore_anthropic_stream_message(payloads)
            if restored_message is not None:
                return _dump_log_json(restored_message)
        return None
    if protocol == ProtocolKind.GEMINI:
        payloads = _parse_sse_payloads(raw_content) or _parse_ndjson_payloads(
            raw_content
        )
        restored = _restore_gemini_stream(payloads)
        if restored is not None:
            return _dump_log_json(restored)
        return None

    return None


def _restore_openai_chat_stream(
    payloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not payloads:
        return None
    model: Any = None
    resp_id: Any = None
    created: Any = None
    usage: Any = None
    finish_reason_seen = False
    choices: dict[int, dict[str, Any]] = {}
    tool_calls: dict[int, dict[int, dict[str, Any]]] = {}
    normalizer = ChatToolCalls()

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        payload = normalizer.normalize_payload(payload)
        model = payload.get("model") or model
        resp_id = payload.get("id") or resp_id
        created = payload.get("created") or created
        if isinstance(payload.get("usage"), dict):
            usage = payload["usage"]
        for choice in payload.get("choices", []):
            if not isinstance(choice, dict):
                continue
            idx = _coerce_openai_output_index(choice.get("index"), default=0) or 0
            entry = choices.setdefault(
                idx,
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                },
            )
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                finish_reason_seen = True
                entry["finish_reason"] = finish_reason
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str):
                entry["message"]["content"] += content
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                entry["message"]["reasoning_content"] = (
                    entry["message"].get("reasoning_content", "") + reasoning
                )
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                tci = _coerce_openai_output_index(tc.get("index"), default=0) or 0
                tc_entry = tool_calls.setdefault(idx, {}).setdefault(
                    tci,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if tc.get("id"):
                    tc_entry["id"] = tc["id"]
                func = tc.get("function")
                if isinstance(func, dict):
                    if func.get("name"):
                        tc_entry["function"]["name"] = func["name"]
                    if isinstance(func.get("arguments"), str):
                        tc_entry["function"]["arguments"] += func["arguments"]

    if not finish_reason_seen:
        return None

    restored_choices: list[dict[str, Any]] = []
    for idx in sorted(choices):
        entry = choices[idx]
        tc_map = tool_calls.get(idx, {})
        if tc_map:
            ordered_tool_calls: list[dict[str, Any]] = []
            for tci in sorted(tc_map):
                tc = tc_map[tci]
                try:
                    json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError as exc:
                    raise ValueError("Invalid tool call arguments JSON") from exc
                ordered_tool_calls.append(tc)
            entry["message"]["tool_calls"] = ordered_tool_calls
        restored_choices.append(
            {
                "index": idx,
                "message": entry["message"],
                "finish_reason": entry["finish_reason"],
            }
        )

    return {
        "id": resp_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model or "",
        "choices": restored_choices,
        "usage": usage or {},
    }


def _restore_gemini_stream(
    payloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not payloads:
        return None
    model: Any = None
    usage: Any = None
    finish_seen = False
    candidates: dict[int, dict[str, Any]] = {}

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        model = payload.get("modelVersion") or payload.get("model") or model
        if isinstance(payload.get("usageMetadata"), dict):
            usage = payload["usageMetadata"]
        for cand in payload.get("candidates", []):
            if not isinstance(cand, dict):
                continue
            idx = _coerce_openai_output_index(cand.get("index"), default=0) or 0
            entry = candidates.setdefault(
                idx, {"content": {"role": "model", "parts": []}, "index": idx}
            )
            content = cand.get("content")
            if isinstance(content, dict):
                for part in content.get("parts", []) or []:
                    if isinstance(part, dict):
                        entry["content"]["parts"].append(deepcopy(part))
            if cand.get("finishReason"):
                finish_seen = True
                entry["finishReason"] = cand["finishReason"]

    if not finish_seen:
        return None
    return {
        "candidates": [candidates[i] for i in sorted(candidates)],
        "usageMetadata": usage or {},
        "modelVersion": model or "",
    }


def _restore_anthropic_stream_message(
    payloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    message: dict[str, Any] | None = None
    input_buffers: dict[int, str] = {}

    for payload in payloads:
        payload_type = str(payload.get("type") or "")

        if payload_type == "message_start":
            start_message = payload.get("message")
            if not isinstance(start_message, dict):
                continue
            message = deepcopy(start_message)
            content = message.get("content")
            message["content"] = deepcopy(content) if isinstance(content, list) else []
            continue

        if message is None:
            continue

        if payload_type == "content_block_start":
            index = _coerce_openai_output_index(payload.get("index"))
            block = payload.get("content_block")
            if index is None or not isinstance(block, dict):
                continue
            content = message.setdefault("content", [])
            if not isinstance(content, list):
                content = []
                message["content"] = content
            while len(content) <= index:
                content.append(None)
            content[index] = deepcopy(block)
            continue

        if payload_type == "content_block_delta":
            index = _coerce_openai_output_index(payload.get("index"))
            delta = payload.get("delta")
            if index is None or not isinstance(delta, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list) or index >= len(content):
                continue
            block = content[index]
            if not isinstance(block, dict):
                continue
            delta_type = str(delta.get("type") or "")
            if delta_type == "text_delta":
                block["text"] = f"{block.get('text') or ''}{delta.get('text') or ''}"
            elif delta_type == "thinking_delta":
                block["thinking"] = (
                    f"{block.get('thinking') or ''}{delta.get('thinking') or ''}"
                )
            elif delta_type == "signature_delta":
                block["signature"] = (
                    f"{block.get('signature') or ''}{delta.get('signature') or ''}"
                )
            elif delta_type == "input_json_delta":
                input_buffers[index] = (
                    f"{input_buffers.get(index, '')}{delta.get('partial_json') or ''}"
                )
            continue

        if payload_type == "content_block_stop":
            index = _coerce_openai_output_index(payload.get("index"))
            if index is None:
                continue
            _finalize_anthropic_tool_use_input(message, index, input_buffers)
            continue

        if payload_type == "message_delta":
            delta = payload.get("delta")
            if isinstance(delta, dict):
                for key, value in delta.items():
                    message[key] = value
            usage = payload.get("usage")
            if isinstance(usage, dict):
                merged_usage = dict(message.get("usage") or {})
                merged_usage.update(usage)
                message["usage"] = merged_usage

    for index in list(input_buffers):
        _finalize_anthropic_tool_use_input(message, index, input_buffers)

    if message is None:
        return None

    content = message.get("content")
    if isinstance(content, list):
        message["content"] = [item for item in content if item is not None]
    return message


def _finalize_anthropic_tool_use_input(
    message: dict[str, Any] | None,
    index: int,
    input_buffers: dict[int, str],
) -> None:
    if message is None:
        return
    content = message.get("content")
    if not isinstance(content, list) or index >= len(content):
        input_buffers.pop(index, None)
        return
    block = content[index]
    if not isinstance(block, dict) or block.get("type") != "tool_use":
        input_buffers.pop(index, None)
        return

    buffer = input_buffers.pop(index, "")
    if not buffer:
        current_input = block.get("input")
        if isinstance(current_input, dict):
            return
        raise ValueError("Invalid Anthropic tool input")

    try:
        parsed_input = json.loads(buffer)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Anthropic tool input JSON") from exc
    if not isinstance(parsed_input, dict):
        raise ValueError("Invalid Anthropic tool input")
    block["input"] = parsed_input


def _compact_openai_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "id",
        "object",
        "model",
        "status",
        "created_at",
        "completed_at",
        "error",
        "incomplete_details",
        "output",
        "usage",
    ):
        value = payload.get(key)
        if value is not None:
            compact[key] = value
    return compact


def _restore_openai_response_output(
    response_payload: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    existing_output = response_payload.get("output")
    if isinstance(existing_output, list) and existing_output:
        return response_payload

    rebuilt_output = _rebuild_openai_response_output(payloads)
    if not rebuilt_output:
        return response_payload

    restored_payload = dict(response_payload)
    restored_payload["output"] = rebuilt_output
    return restored_payload


def _rebuild_openai_response_output(
    payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items_by_index: dict[int, dict[str, Any]] = {}
    for payload in payloads:
        payload_type = str(payload.get("type") or "")
        if payload_type in {"response.output_item.added", "response.output_item.done"}:
            output_index = _coerce_openai_output_index(payload.get("output_index"))
            item = payload.get("item")
            if output_index is None or not isinstance(item, dict):
                continue
            items_by_index[output_index] = _merge_openai_output_item(
                items_by_index.get(output_index), item
            )
            continue

        if payload_type in {
            "response.content_part.added",
            "response.content_part.done",
        }:
            output_index = _coerce_openai_output_index(payload.get("output_index"))
            content_index = _coerce_openai_output_index(payload.get("content_index"))
            part = payload.get("part")
            if (
                output_index is None
                or content_index is None
                or not isinstance(part, dict)
            ):
                continue
            item = _ensure_openai_output_message(
                items_by_index, output_index, payload.get("item_id")
            )
            _upsert_openai_content_part(item, content_index, part)
            continue

        if payload_type == "response.output_text.delta":
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            output_index = _coerce_openai_output_index(
                payload.get("output_index"), default=0
            )
            content_index = _coerce_openai_output_index(
                payload.get("content_index"), default=0
            )
            item = _ensure_openai_output_message(
                items_by_index, output_index, payload.get("item_id")
            )
            _append_openai_output_text(item, content_index, delta)
            continue

        if payload_type == "response.output_text.done":
            text = payload.get("text")
            if not isinstance(text, str):
                continue
            output_index = _coerce_openai_output_index(
                payload.get("output_index"), default=0
            )
            content_index = _coerce_openai_output_index(
                payload.get("content_index"), default=0
            )
            item = _ensure_openai_output_message(
                items_by_index, output_index, payload.get("item_id")
            )
            _set_openai_output_text(item, content_index, text)

    return [items_by_index[index] for index in sorted(items_by_index)]


def _merge_openai_output_item(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(existing) if existing is not None else {}
    for key, value in incoming.items():
        if key == "content" and isinstance(value, list):
            merged[key] = deepcopy(value)
            continue
        merged[key] = value
    if merged.get("type") == "message" and not isinstance(merged.get("content"), list):
        merged["content"] = []
    return merged


def _ensure_openai_output_message(
    items_by_index: dict[int, dict[str, Any]],
    output_index: int,
    item_id: Any,
) -> dict[str, Any]:
    item = items_by_index.get(output_index)
    if item is None:
        item = {"type": "message", "role": "assistant", "content": []}
        items_by_index[output_index] = item
    if item_id and item.get("id") is None:
        item["id"] = str(item_id)
    if item.get("type") == "message" and not isinstance(item.get("content"), list):
        item["content"] = []
    return item


def _upsert_openai_content_part(
    item: dict[str, Any], content_index: int, part: dict[str, Any]
) -> None:
    content = item.setdefault("content", [])
    if not isinstance(content, list):
        content = []
        item["content"] = content
    while len(content) <= content_index:
        content.append(None)
    content[content_index] = deepcopy(part)


def _append_openai_output_text(
    item: dict[str, Any], content_index: int, delta: str
) -> None:
    content = item.setdefault("content", [])
    if not isinstance(content, list):
        content = []
        item["content"] = content
    while len(content) <= content_index:
        content.append(None)
    part = content[content_index]
    if not isinstance(part, dict):
        part = {"type": "output_text", "text": "", "annotations": []}
        content[content_index] = part
    elif part.get("type") != "output_text":
        return
    part["text"] = f"{part.get('text') or ''}{delta}"
    part.setdefault("annotations", [])


def _set_openai_output_text(
    item: dict[str, Any], content_index: int, text: str
) -> None:
    content = item.setdefault("content", [])
    if not isinstance(content, list):
        content = []
        item["content"] = content
    while len(content) <= content_index:
        content.append(None)
    part = content[content_index]
    if not isinstance(part, dict):
        part = {"type": "output_text", "annotations": []}
        content[content_index] = part
    if part.get("type") != "output_text":
        return
    part["text"] = text
    part.setdefault("annotations", [])


def _coerce_openai_output_index(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
