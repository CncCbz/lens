from __future__ import annotations

from ...core.protocol_reachability import CLASSIFIER_PROTOCOLS, wire_protocol

from .runtime_context import (
    Any,
    Mapping,
    ProtocolKind,
    RESPONSES_TERMINAL_EVENTS,
    StreamCapture,
    httpx,
    json,
)


def _usage_mapping(value: Any, key: str = "usage") -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"Invalid usage object: {key}")
    return value


def _usage_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key)
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"Invalid usage value: {key}")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid usage value: {key}") from None
    if parsed < 0:
        raise ValueError(f"Invalid negative usage value: {key}")
    return parsed


def _openai_cached_tokens(usage: Mapping[str, Any], detail_key: str) -> int:
    details = usage.get(detail_key)
    if details is None:
        return 0
    if not isinstance(details, Mapping):
        raise ValueError(f"Invalid usage object: {detail_key}")
    return _usage_int(details, "cached_tokens")


def _anthropic_usage(
    usage: Mapping[str, Any], *, model: str | None
) -> dict[str, int | str | None]:
    base_input_tokens = _usage_int(usage, "input_tokens")
    cache_read_input_tokens = _usage_int(usage, "cache_read_input_tokens")
    cache_write_input_tokens = _usage_int(usage, "cache_creation_input_tokens")
    input_tokens = (
        base_input_tokens + cache_read_input_tokens + cache_write_input_tokens
    )
    output_tokens = _usage_int(usage, "output_tokens")
    return {
        "resolved_model": model,
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def _gemini_usage(payload: Mapping[str, Any]) -> dict[str, int | str | None]:
    usage = _usage_mapping(payload.get("usageMetadata"), "usageMetadata")
    input_tokens = _usage_int(usage, "promptTokenCount")
    cache_read_input_tokens = _usage_int(usage, "cachedContentTokenCount")
    output_tokens = _usage_int(usage, "candidatesTokenCount")
    total_tokens = _usage_int(usage, "totalTokenCount") or (
        input_tokens + output_tokens
    )
    return {
        "resolved_model": payload.get("modelVersion") or payload.get("model"),
        "input_tokens": input_tokens,
        "cache_read_input_tokens": min(cache_read_input_tokens, input_tokens),
        "cache_write_input_tokens": 0,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _openai_chat_usage(payload: Mapping[str, Any]) -> dict[str, int | str | None]:
    usage = _usage_mapping(payload.get("usage"))
    cache_read_input_tokens = _openai_cached_tokens(usage, "prompt_tokens_details")
    input_tokens = _usage_int(usage, "prompt_tokens")
    return {
        "resolved_model": payload.get("model"),
        "input_tokens": input_tokens,
        "cache_read_input_tokens": min(cache_read_input_tokens, input_tokens),
        "cache_write_input_tokens": 0,
        "output_tokens": _usage_int(usage, "completion_tokens"),
        "total_tokens": _usage_int(usage, "total_tokens"),
    }


def _openai_responses_usage(
    payload: Mapping[str, Any], *, model: str | None
) -> dict[str, int | str | None]:
    usage = _usage_mapping(payload.get("usage"))
    cache_read_input_tokens = _openai_cached_tokens(usage, "input_tokens_details")
    input_tokens = _usage_int(usage, "input_tokens")
    return {
        "resolved_model": model,
        "input_tokens": input_tokens,
        "cache_read_input_tokens": min(cache_read_input_tokens, input_tokens),
        "cache_write_input_tokens": 0,
        "output_tokens": _usage_int(usage, "output_tokens"),
        "total_tokens": _usage_int(usage, "total_tokens"),
    }


def _openai_image_usage(
    payload: Mapping[str, Any], *, model: str | None
) -> dict[str, int | str | None]:
    usage = _usage_mapping(payload.get("usage"))
    prompt_tokens = _usage_int(usage, "prompt_tokens")
    total_tokens = _usage_int(usage, "total_tokens")
    return {
        "resolved_model": model,
        "input_tokens": prompt_tokens,
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": max(total_tokens - prompt_tokens, 0),
        "total_tokens": total_tokens,
    }


def _openai_embedding_usage(payload: Mapping[str, Any]) -> dict[str, int | str | None]:
    usage = _usage_mapping(payload.get("usage"))
    return {
        "resolved_model": payload.get("model"),
        "input_tokens": _usage_int(usage, "prompt_tokens"),
        "cache_read_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": _usage_int(usage, "total_tokens"),
    }


_EMPTY_USAGE: dict[str, int | str | None] = {
    "resolved_model": None,
    "input_tokens": 0,
    "cache_read_input_tokens": 0,
    "cache_write_input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
}


def _extract_stream_usage(
    protocol: ProtocolKind,
    raw_content: str | None,
    parse_errors: list[str] | None = None,
) -> dict[str, int | str | None]:
    protocol = wire_protocol(protocol)
    if (
        protocol == ProtocolKind.OPENAI_EMBEDDING
        or protocol == ProtocolKind.RERANK
        or not raw_content
    ):
        return dict(_EMPTY_USAGE)

    if protocol == ProtocolKind.GEMINI:
        payloads = _parse_sse_payloads(
            raw_content, errors=parse_errors
        ) or _parse_ndjson_payloads(raw_content, errors=parse_errors)
        return _extract_usage_from_payload(protocol, payloads[-1] if payloads else {})

    payloads = _parse_sse_payloads(raw_content, errors=parse_errors)
    merged: dict[str, int | str | None] = dict(_EMPTY_USAGE)
    int_keys = (
        "input_tokens",
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "total_tokens",
    )
    for payload in payloads:
        parsed = _extract_usage_from_payload(protocol, payload)
        if parsed["resolved_model"]:
            merged["resolved_model"] = parsed["resolved_model"]
        for key in int_keys:
            value = parsed[key]
            assert isinstance(value, int)
            if value:
                merged[key] = max(merged[key], value)
    merged["total_tokens"] = max(
        int(merged["total_tokens"] or 0),
        int(merged["input_tokens"] or 0) + int(merged["output_tokens"] or 0),
    )
    return merged


def _parse_sse_payloads(
    raw_content: str, *, errors: list[str] | None = None
) -> list[dict[str, Any]]:
    normalized = _normalize_event_stream_newlines(raw_content)
    payloads: list[dict[str, Any]] = []
    for block in normalized.split("\n\n"):
        data_lines = [
            line[5:].strip() for line in block.splitlines() if line.startswith("data:")
        ]
        if not data_lines:
            continue
        joined = "\n".join(line for line in data_lines if line and line != "[DONE]")
        if not joined:
            continue
        try:
            payload = json.loads(joined)
        except json.JSONDecodeError as exc:
            if errors is not None:
                errors.append(f"invalid SSE JSON: {exc.msg}")
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _parse_ndjson_payloads(
    raw_content: str, *, errors: list[str] | None = None
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in _normalize_event_stream_newlines(raw_content).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            if errors is not None:
                errors.append(f"invalid NDJSON: {exc.msg}")
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _normalize_event_stream_newlines(raw_content: str) -> str:
    return raw_content.replace("\r\n", "\n").replace("\r", "\n")


def _describe_stream_capture_issue(
    protocol: ProtocolKind,
    capture: StreamCapture | None,
    raw_content: str | None,
) -> str | None:
    protocol = wire_protocol(protocol)
    if capture is not None and capture.verification_cancelled:
        # A verification timeout is the whole story; the dropped stream is not
        # a separate upstream problem worth reporting.
        return capture.errors[-1] if capture.errors else "verification cancelled"
    issues: list[str] = []

    client_disconnect_after_chat_finish = (
        capture is not None
        and capture.client_disconnected
        and protocol == ProtocolKind.OPENAI_CHAT
        and len(capture.chat_finished_choices) >= capture.chat_expected_choices
    )

    if capture is not None:
        for error in capture.errors:
            if error:
                if (
                    client_disconnect_after_chat_finish
                    and error == "client disconnected"
                ):
                    continue
                issues.append(error)
        issues.extend(error for error in capture.parse_errors if error)

    if protocol in {ProtocolKind.PI_MESSAGES, ProtocolKind.BEDROCK_CONVERSE_STREAM}:
        has_terminal = (
            capture.saw_response_completed
            if capture is not None
            else any(
                (
                    payload.get("type") == "done"
                    if protocol == ProtocolKind.PI_MESSAGES
                    else isinstance(payload.get("messageStop"), dict)
                )
                for payload in _parse_sse_payloads(raw_content or "")
            )
        )
        if not has_terminal:
            issues.append("stream ended before a provider terminal event")
    if protocol == ProtocolKind.OPENAI_RESPONSES:
        if capture is not None:
            if not capture.saw_response_completed:
                issues.append("stream ended before a Responses terminal event")
        elif not raw_content:
            issues.append("no stream content captured")
        else:
            payloads = _parse_sse_payloads(raw_content)
            has_terminal = any(
                payload.get("type") in RESPONSES_TERMINAL_EVENTS for payload in payloads
            )
            if not has_terminal:
                issues.append("stream ended before a Responses terminal event")
    elif capture is None or capture.capture_body:
        if not raw_content:
            issues.append("no stream content captured")

    if capture is not None and not capture.completed:
        if not client_disconnect_after_chat_finish:
            issues.append("stream did not drain to completion")
    if (
        capture is not None
        and capture.completed
        and not capture.saw_first_chunk
        and protocol not in (ProtocolKind.OPENAI_EMBEDDING, ProtocolKind.RERANK)
    ):
        issues.append("stream ended before first token")

    if not issues:
        return None

    return "; ".join(dict.fromkeys(issues))


def _extract_usage_from_payload(
    protocol: ProtocolKind, payload: dict[str, Any]
) -> dict[str, int | str | None]:
    protocol = wire_protocol(protocol)
    if protocol == ProtocolKind.PI_MESSAGES:
        usage = _usage_mapping(payload.get("usage"))
        cache_read = _usage_int(usage, "cacheRead")
        cache_write = _usage_int(usage, "cacheWrite")
        incoming = _usage_int(usage, "input") + cache_read + cache_write
        output = _usage_int(usage, "output")
        return {
            "resolved_model": payload.get("responseModel") or payload.get("model"),
            "input_tokens": incoming,
            "output_tokens": output,
            "cache_read_input_tokens": cache_read,
            "cache_write_input_tokens": cache_write,
            "total_tokens": (
                _usage_int(usage, "totalTokens")
                if "totalTokens" in usage
                else incoming + output
            ),
        }
    if protocol == ProtocolKind.BEDROCK_CONVERSE_STREAM:
        source = payload.get("metadata", payload)
        usage = _usage_mapping(source.get("usage")) if isinstance(source, dict) else {}
        cache_read = _usage_int(usage, "cacheReadInputTokens")
        cache_write = _usage_int(usage, "cacheWriteInputTokens")
        incoming = _usage_int(usage, "inputTokens") + cache_read + cache_write
        output = _usage_int(usage, "outputTokens")
        return {
            "resolved_model": payload.get("model"),
            "input_tokens": incoming,
            "output_tokens": output,
            "cache_read_input_tokens": cache_read,
            "cache_write_input_tokens": cache_write,
            "total_tokens": (
                _usage_int(usage, "totalTokens")
                if "totalTokens" in usage
                else incoming + output
            ),
        }
    if protocol in CLASSIFIER_PROTOCOLS:
        source = payload
        if protocol == ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
            from ..converters.classifier_protocols import classifier_output

            source = classifier_output(protocol, payload)
        usage = _usage_mapping(source.get("usage")) if isinstance(source, dict) else {}
        incoming = _usage_int(usage, "input_tokens")
        output = _usage_int(usage, "output_tokens")
        if protocol == ProtocolKind.LLAMA_CPP_CLASSIFY and source.get("usage") is None:
            incoming = source.get("tokens_evaluated", 0)
            output = source.get("tokens_predicted", 0)
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in (incoming, output)
            ):
                raise ValueError("Invalid llama.cpp token usage")
        return {
            **_EMPTY_USAGE,
            "resolved_model": source.get("model") or payload.get("model"),
            "input_tokens": incoming,
            "output_tokens": output,
            "total_tokens": incoming + output,
        }
    if protocol == ProtocolKind.OPENROUTER_IMAGES:
        return _openai_chat_usage(payload)
    if protocol == ProtocolKind.OPENAI_CHAT:
        return _openai_chat_usage(payload)
    if protocol == ProtocolKind.OPENAI_RESPONSES:
        if payload.get("type") in RESPONSES_TERMINAL_EVENTS:
            response_payload = _usage_mapping(payload.get("response"))
            return _openai_responses_usage(
                response_payload,
                model=response_payload.get("model") or payload.get("model"),
            )
        return _openai_responses_usage(payload, model=payload.get("model"))
    if protocol == ProtocolKind.OPENAI_EMBEDDING:
        return _openai_embedding_usage(payload)
    if protocol == ProtocolKind.ANTHROPIC:
        if payload.get("type") == "message_start":
            message = _usage_mapping(payload.get("message"))
            return _anthropic_usage(
                _usage_mapping(message.get("usage")), model=message.get("model")
            )
        if payload.get("type") == "message_delta":
            return _anthropic_usage(_usage_mapping(payload.get("usage")), model=None)
        return _anthropic_usage(
            _usage_mapping(payload.get("usage")), model=payload.get("model")
        )
    return _gemini_usage(payload)


def _extract_response_usage(
    protocol: ProtocolKind, response: httpx.Response, fallback_model: Any = None
) -> dict[str, int | str | None]:
    protocol = wire_protocol(protocol)
    if protocol == ProtocolKind.RERANK:
        empty = dict(_EMPTY_USAGE)
        if isinstance(fallback_model, str) and fallback_model.strip():
            empty["resolved_model"] = fallback_model.strip()
        return empty
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Upstream response JSON must be an object")
    if protocol in CLASSIFIER_PROTOCOLS | {
        ProtocolKind.PI_MESSAGES,
        ProtocolKind.BEDROCK_CONVERSE_STREAM,
        ProtocolKind.OPENROUTER_IMAGES,
    }:
        return _extract_usage_from_payload(protocol, payload)
    if protocol == ProtocolKind.OPENAI_CHAT:
        return _openai_chat_usage(payload)
    if protocol == ProtocolKind.OPENAI_RESPONSES:
        return _openai_responses_usage(payload, model=payload.get("model"))
    if protocol == ProtocolKind.OPENAI_IMAGE:
        return _openai_image_usage(payload, model=fallback_model)
    if protocol == ProtocolKind.OPENAI_EMBEDDING:
        return _openai_embedding_usage(payload)
    if protocol == ProtocolKind.ANTHROPIC:
        return _anthropic_usage(
            _usage_mapping(payload.get("usage")), model=payload.get("model")
        )
    return _gemini_usage(payload)
