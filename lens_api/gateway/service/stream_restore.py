from __future__ import annotations

from collections.abc import Collection, Mapping
import time
import uuid

from .runtime_context import (
    Any,
    ProtocolKind,
    RESPONSES_TERMINAL_EVENTS,
    deepcopy,
    json,
)
from ..converters._chat_stream import ChatToolCalls
from ...core.protocol_reachability import wire_protocol
from .provider_stream_restore import restore_bedrock_events, restore_pi_events
from .payload_serialization import _dump_log_json
from .usage import _parse_ndjson_payloads, _parse_sse_payloads

_CHAT_TEXT_DELTA_KEYS = frozenset(
    {"content", "reasoning_content", "reasoning", "refusal"}
)
_CHAT_LIST_DELTA_KEYS = frozenset({"content", "annotations"})
_CHAT_AUDIO_TEXT_KEYS = frozenset({"data", "transcript"})


def _merge_latest_keys(
    target: dict[str, Any],
    source: Mapping[str, Any],
    *,
    skip: Collection[str] = (),
) -> None:
    """Overlay ``source`` onto ``target`` while keeping every upstream key.

    Stream chunks resend stable metadata (``id``, ``model``, provider extras)
    and carry the newest value of fields that appear only once (``usage``), so
    the latest non-null value wins. Unknown keys are kept, never dropped.
    """
    for key, value in source.items():
        if key in skip or value is None:
            continue
        target[key] = deepcopy(value)


def _merge_chat_message_delta(
    message: dict[str, Any], delta: Mapping[str, Any]
) -> None:
    for key, value in delta.items():
        if value is None or key == "tool_calls":
            continue
        if key == "role":
            message["role"] = value
            continue
        current = message.get(key)
        if key in _CHAT_TEXT_DELTA_KEYS and isinstance(value, str):
            message[key] = f"{current}{value}" if isinstance(current, str) else value
            continue
        if key in _CHAT_LIST_DELTA_KEYS and isinstance(value, list):
            message[key] = [
                *(current if isinstance(current, list) else []),
                *deepcopy(value),
            ]
            continue
        if key == "audio" and isinstance(value, Mapping):
            audio = current if isinstance(current, dict) else {}
            message["audio"] = audio
            for audio_key, audio_value in value.items():
                if audio_value is None:
                    continue
                previous = audio.get(audio_key)
                if audio_key in _CHAT_AUDIO_TEXT_KEYS and isinstance(audio_value, str):
                    audio[audio_key] = (
                        f"{previous}{audio_value}"
                        if isinstance(previous, str)
                        else audio_value
                    )
                else:
                    audio[audio_key] = deepcopy(audio_value)
            continue
        if key == "function_call" and isinstance(value, Mapping):
            function_call = current if isinstance(current, dict) else {}
            message["function_call"] = function_call
            _merge_latest_keys(function_call, value, skip={"arguments"})
            if isinstance(value.get("arguments"), str):
                function_call["arguments"] = (
                    f"{function_call.get('arguments') or ''}{value['arguments']}"
                )
            continue
        message[key] = deepcopy(value)


def _chat_delta_text_parts(delta: Mapping[str, Any]) -> list[dict[str, Any]]:
    original = delta.get("_gemini_text_parts")
    if isinstance(original, list):
        return deepcopy(original)

    parts: list[dict[str, Any]] = []
    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        parts.append({"text": reasoning, "thought": True})
    content = delta.get("content")
    if isinstance(content, str):
        if content:
            parts.append({"text": content})
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                parts.append({"text": item})
            elif (
                isinstance(item, Mapping)
                and item.get("type") in {"text", "input_text", "output_text"}
                and item.get("thought") is not True
                and isinstance(item.get("text"), str)
            ):
                parts.append({"text": item["text"]})
    return parts


def _merge_chat_tool_call_deltas(
    target: dict[int, dict[str, Any]], tool_calls: Any
) -> None:
    if not isinstance(tool_calls, list):
        return
    for tool_call in tool_calls:
        if not isinstance(tool_call, Mapping):
            continue
        index = _coerce_openai_output_index(tool_call.get("index"), default=0) or 0
        entry = target.setdefault(index, {})
        _merge_latest_keys(entry, tool_call, skip={"index", "function"})
        function = tool_call.get("function")
        if not isinstance(function, Mapping):
            continue
        target_function = entry.get("function")
        if not isinstance(target_function, dict):
            target_function = {}
            entry["function"] = target_function
        _merge_latest_keys(target_function, function, skip={"arguments"})
        if isinstance(function.get("arguments"), str):
            target_function["arguments"] = (
                f"{target_function.get('arguments') or ''}" f"{function['arguments']}"
            )


def _merge_chat_logprobs(target: dict[str, Any], logprobs: Any) -> None:
    if not isinstance(logprobs, Mapping):
        return
    for key, value in logprobs.items():
        current = target.get(key)
        if isinstance(value, list):
            target[key] = [
                *(current if isinstance(current, list) else []),
                *deepcopy(value),
            ]
        elif value is not None:
            target[key] = deepcopy(value)


def _distill_stream_response_content(
    protocol: ProtocolKind,
    raw_content: str | None,
    *,
    log_output: bool = True,
    allow_incomplete: bool = False,
) -> str | None:
    """Restore an upstream stream to one upstream-native JSON object.

    Returns ``None`` when the stream lacks the protocol's required terminal
    evidence, and raises ``ValueError`` for malformed content.
    ``allow_incomplete`` is logging-only: it keeps a partial object when the
    terminal event is missing and does not reject truncated tool JSON.
    Client delivery must leave it false. Callers must never fall back to
    returning raw SSE as if it were JSON.
    """
    if not raw_content:
        return None
    if protocol == ProtocolKind.MISTRAL_CONVERSATIONS:
        from ..converters.provider_variants import (
            mistral_response_to_chat,
            chat_response_to_mistral,
        )

        events = _parse_sse_payloads(raw_content)
        ordered: dict[int, list[dict[str, Any]]] = {}
        array_choices: set[int] = set()
        projected = deepcopy(events)
        for event in projected:
            if "error" in event:
                raise ValueError(f"Mistral stream error: {event['error']}")
            for choice in event.get("choices") or []:
                index = choice.get("index", 0)
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, list):
                    array_choices.add(index)
                    ordered.setdefault(index, []).extend(deepcopy(content))
                elif isinstance(content, str):
                    ordered.setdefault(index, []).append(
                        {"type": "text", "text": content}
                    )
                choice["delta"] = mistral_response_to_chat(
                    {"choices": [{"message": delta}]}
                )["choices"][0]["message"]
        restored = _restore_openai_chat_stream(
            projected, allow_incomplete=allow_incomplete
        )
        if restored is None:
            return None
        native = chat_response_to_mistral(restored)
        for choice in native.get("choices") or []:
            index = choice.get("index", 0)
            if index in ordered:
                choice["message"]["content"] = (
                    ordered[index]
                    if index in array_choices
                    else "".join(part["text"] for part in ordered[index])
                )
        return _dump_log_json(native, log_output=log_output)
    protocol = wire_protocol(protocol)
    if protocol in {ProtocolKind.PI_MESSAGES, ProtocolKind.BEDROCK_CONVERSE_STREAM}:
        restore = (
            restore_pi_events
            if protocol == ProtocolKind.PI_MESSAGES
            else restore_bedrock_events
        )
        restored = restore(
            _parse_sse_payloads(raw_content), allow_incomplete=allow_incomplete
        )
        return (
            _dump_log_json(restored, log_output=log_output)
            if restored is not None
            else None
        )

    if protocol == ProtocolKind.OPENAI_CHAT:
        restored = _restore_openai_chat_stream(
            _parse_sse_payloads(raw_content), allow_incomplete=allow_incomplete
        )
        if restored is not None:
            return _dump_log_json(restored, log_output=log_output)
        return None

    if protocol == ProtocolKind.OPENAI_RESPONSES:
        payloads = _parse_sse_payloads(raw_content)
        for payload in reversed(payloads):
            if payload.get("type") not in RESPONSES_TERMINAL_EVENTS:
                continue
            response_payload = payload.get("response")
            if isinstance(response_payload, dict):
                return _dump_log_json(
                    _restore_openai_response_payload(response_payload, payloads),
                    log_output=log_output,
                )
        if allow_incomplete:
            partial = _restore_incomplete_openai_response(payloads)
            if partial is not None:
                return _dump_log_json(partial, log_output=log_output)
        return None
    if protocol == ProtocolKind.ANTHROPIC:
        payloads = _parse_sse_payloads(raw_content)
        has_start = any(p.get("type") == "message_start" for p in payloads)
        has_stop = any(p.get("type") == "message_stop" for p in payloads)
        if (has_start and has_stop) or (allow_incomplete and has_start):
            restored_message = _restore_anthropic_stream_message(
                payloads, allow_incomplete=allow_incomplete
            )
            if restored_message is not None:
                return _dump_log_json(restored_message, log_output=log_output)
        return None
    if protocol == ProtocolKind.GEMINI:
        payloads = _parse_sse_payloads(raw_content) or _parse_ndjson_payloads(
            raw_content
        )
        restored = _restore_gemini_stream(payloads, allow_incomplete=allow_incomplete)
        if restored is not None:
            return _dump_log_json(restored, log_output=log_output)
        return None

    return None


def _restore_openai_chat_stream(
    payloads: list[dict[str, Any]],
    *,
    allow_incomplete: bool = False,
) -> dict[str, Any] | None:
    if not payloads:
        return None
    top_level: dict[str, Any] = {}
    choices: dict[int, dict[str, Any]] = {}
    messages: dict[int, dict[str, Any]] = {}
    text_parts: dict[int, list[dict[str, Any]]] = {}
    choices_with_text_metadata: set[int] = set()
    tool_calls: dict[int, dict[int, dict[str, Any]]] = {}
    logprobs: dict[int, dict[str, Any]] = {}
    finished_choices: set[int] = set()
    normalizer = ChatToolCalls()

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        payload = normalizer.normalize_payload(payload)
        # "object" is pinned to "chat.completion" below: chunks announce
        # "chat.completion.chunk".
        _merge_latest_keys(top_level, payload, skip={"choices", "object"})
        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            idx = _coerce_openai_output_index(choice.get("index"), default=0) or 0
            entry = choices.setdefault(idx, {})
            _merge_latest_keys(
                entry, choice, skip={"delta", "message", "logprobs", "index"}
            )
            if isinstance(choice.get("logprobs"), Mapping):
                _merge_chat_logprobs(logprobs.setdefault(idx, {}), choice["logprobs"])
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                finished_choices.add(idx)
                entry["finish_reason"] = finish_reason
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                text_parts.setdefault(idx, []).extend(_chat_delta_text_parts(delta))
                if isinstance(delta.get("_gemini_text_parts"), list):
                    choices_with_text_metadata.add(idx)
                _merge_chat_message_delta(messages.setdefault(idx, {}), delta)
                _merge_chat_tool_call_deltas(
                    tool_calls.setdefault(idx, {}), delta.get("tool_calls")
                )

    # Every started choice has to report a finish_reason: a partial multi-choice
    # stream is not a complete response.
    complete = bool(choices) and set(choices) <= finished_choices
    if not complete and not (allow_incomplete and choices):
        return _stream_error_payload(payloads) if allow_incomplete else None

    restored_choices: list[dict[str, Any]] = []
    for idx in sorted(choices):
        entry = choices[idx]
        entry["index"] = idx
        message = messages.setdefault(idx, {})
        message.setdefault("role", "assistant")
        message.setdefault("content", "")
        if idx in choices_with_text_metadata:
            message["_gemini_text_parts"] = text_parts[idx]
        tc_map = tool_calls.get(idx) or {}
        if tc_map:
            ordered_tool_calls: list[dict[str, Any]] = []
            for tci in sorted(tc_map):
                tc = tc_map[tci]
                function = tc.get("function")
                if isinstance(function, dict):
                    function.setdefault("name", "")
                    function.setdefault("arguments", "")
                if not allow_incomplete:
                    arguments = tc.get("function", {}).get("arguments")
                    if isinstance(arguments, str) and arguments:
                        try:
                            json.loads(arguments)
                        except json.JSONDecodeError as exc:
                            raise ValueError(
                                "Invalid tool call arguments JSON"
                            ) from exc
                ordered_tool_calls.append(tc)
            message["tool_calls"] = ordered_tool_calls
        entry["message"] = message
        if logprobs.get(idx):
            entry["logprobs"] = logprobs[idx]
        entry.setdefault("finish_reason", None)
        restored_choices.append(entry)

    restored = {"object": "chat.completion", **top_level}
    restored.setdefault("id", f"chatcmpl-{uuid.uuid4().hex[:24]}")
    restored.setdefault("created", int(time.time()))
    restored.setdefault("model", "")
    restored.setdefault("usage", {})
    restored["choices"] = restored_choices
    return restored


def _stream_error_payload(payloads: list[dict[str, Any]]) -> dict[str, Any] | None:
    for payload in payloads:
        if isinstance(payload.get("error"), dict):
            return payload
    return None


def _restore_gemini_stream(
    payloads: list[dict[str, Any]],
    *,
    allow_incomplete: bool = False,
) -> dict[str, Any] | None:
    if not payloads:
        return None
    top_level: dict[str, Any] = {}
    finished_candidates: set[int] = set()
    candidates: dict[int, dict[str, Any]] = {}

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        _merge_latest_keys(top_level, payload, skip={"candidates"})
        for cand in payload.get("candidates") or []:
            if not isinstance(cand, dict):
                continue
            idx = _coerce_openai_output_index(cand.get("index"), default=0) or 0
            entry = candidates.setdefault(idx, {})
            _merge_latest_keys(entry, cand, skip={"content"})
            if cand.get("finishReason"):
                finished_candidates.add(idx)
            content = cand.get("content")
            if not isinstance(content, dict):
                continue
            target = entry.setdefault("content", {})
            for key, value in content.items():
                if key == "parts" and isinstance(value, list):
                    parts = target.setdefault("parts", [])
                    parts.extend(deepcopy(part) for part in value)
                elif value is not None:
                    target[key] = deepcopy(value)

    complete = bool(candidates) and set(candidates) <= finished_candidates
    if not complete and not (allow_incomplete and candidates):
        return None
    restored = dict(top_level)
    restored["candidates"] = [candidates[i] for i in sorted(candidates)]
    restored.setdefault("modelVersion", "")
    restored.setdefault("usageMetadata", {})
    return restored


def _restore_anthropic_stream_message(
    payloads: list[dict[str, Any]],
    *,
    allow_incomplete: bool = False,
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
            delta_type = str(delta.get("type") or "")
            content = message.setdefault("content", [])
            if not isinstance(content, list):
                content = []
                message["content"] = content
            while len(content) <= index:
                content.append(None)
            block = content[index]
            if not isinstance(block, dict):
                # Anthropic-compatible upstreams sometimes skip
                # content_block_start; materialize the block instead of
                # dropping every following delta.
                block = _anthropic_block_stub(delta_type)
                content[index] = block
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
            elif delta_type == "citations_delta":
                citation = delta.get("citation")
                if isinstance(citation, dict):
                    block.setdefault("citations", []).append(deepcopy(citation))
            else:
                # Unknown delta types keep every field: strings append, lists
                # extend, anything else takes the latest value.
                for key, value in delta.items():
                    if key == "type" or value is None:
                        continue
                    current = block.get(key)
                    if isinstance(value, str):
                        block[key] = (
                            f"{current}{value}" if isinstance(current, str) else value
                        )
                    elif isinstance(value, list) and isinstance(current, list):
                        block[key] = [*current, *deepcopy(value)]
                    else:
                        block[key] = deepcopy(value)
            continue

        if payload_type == "content_block_stop":
            index = _coerce_openai_output_index(payload.get("index"))
            if index is None:
                continue
            _finalize_anthropic_tool_use_input(
                message, index, input_buffers, allow_incomplete=allow_incomplete
            )
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
        _finalize_anthropic_tool_use_input(
            message, index, input_buffers, allow_incomplete=allow_incomplete
        )

    if message is None:
        return None

    content = message.get("content")
    if isinstance(content, list):
        message["content"] = [item for item in content if item is not None]
    return message


def _anthropic_block_stub(delta_type: str) -> dict[str, Any]:
    if delta_type in {"thinking_delta", "signature_delta"}:
        return {"type": "thinking", "thinking": "", "signature": ""}
    if delta_type == "input_json_delta":
        return {"type": "tool_use", "id": "", "name": "", "input": {}}
    if delta_type == "citations_delta":
        return {"type": "text", "text": "", "citations": []}
    return {"type": "text", "text": ""}


def _finalize_anthropic_tool_use_input(
    message: dict[str, Any] | None,
    index: int,
    input_buffers: dict[int, str],
    *,
    allow_incomplete: bool = False,
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
        if isinstance(current_input, dict) or allow_incomplete:
            return
        raise ValueError("Invalid Anthropic tool input")

    try:
        parsed_input = json.loads(buffer)
    except json.JSONDecodeError as exc:
        if allow_incomplete:
            block["input"] = buffer
            return
        raise ValueError("Invalid Anthropic tool input JSON") from exc
    if not isinstance(parsed_input, dict):
        if allow_incomplete:
            block["input"] = buffer
            return
        raise ValueError("Invalid Anthropic tool input")
    block["input"] = parsed_input


def _restore_openai_response_payload(
    response_payload: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep every upstream response field; only fill in a missing ``output``."""
    restored = deepcopy(response_payload)
    existing_output = restored.get("output")
    if isinstance(existing_output, list) and existing_output:
        return restored

    rebuilt_output = _rebuild_openai_response_output(payloads)
    if rebuilt_output:
        restored["output"] = rebuilt_output
    elif not isinstance(existing_output, list):
        restored["output"] = []
    return restored


def _restore_incomplete_openai_response(
    payloads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    response_payload: dict[str, Any] | None = None
    for payload in payloads:
        candidate = payload.get("response")
        if isinstance(candidate, dict):
            response_payload = candidate
    rebuilt_output = _rebuild_openai_response_output(payloads)
    if response_payload is None and not rebuilt_output:
        return _stream_error_payload(payloads)
    base = deepcopy(response_payload) if response_payload is not None else {}
    existing_output = base.get("output")
    if rebuilt_output and (
        not isinstance(existing_output, list) or not existing_output
    ):
        base["output"] = rebuilt_output
    elif "output" not in base:
        base["output"] = []
    base.setdefault("status", "incomplete")
    return base


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
