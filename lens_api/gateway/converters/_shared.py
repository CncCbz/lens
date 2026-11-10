import codecs
import json
import re
from collections.abc import Mapping
from typing import Any, AsyncIterator

FINISH_REASON_CHAT_TO_ANTHROPIC: dict[str | None, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
}

FINISH_REASON_CHAT_TO_RESPONSES: dict[str | None, str] = {
    "stop": "completed",
    "length": "incomplete",
    "tool_calls": "completed",
    "content_filter": "failed",
}

# Reasoning/thinking level used as the shared Chat-pivot value domain. It
# matches the OpenAI effort vocabulary that Chat and Responses providers use;
# Anthropic budgets and Gemini budgets are mapped to/from these levels.
_THINKING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)

# pi's DEFAULT_THINKING_BUDGETS; xhigh and max clamp to the high budget.
_ANTHROPIC_EFFORT_TO_BUDGET: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 16384,
    "max": 16384,
}


def _clean_thinking_effort(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in _THINKING_EFFORTS:
        return normalized
    return None


def _extract_chat_reasoning_effort(body: Mapping[str, Any] | None) -> str | None:
    """Read the reasoning level from a Chat-format request."""
    if not isinstance(body, Mapping):
        return None
    for key in ("reasoning_effort", "reasoningEffort"):
        effort = _clean_thinking_effort(body.get(key))
        if effort:
            return effort
    reasoning = body.get("reasoning")
    if isinstance(reasoning, Mapping):
        return _clean_thinking_effort(reasoning.get("effort"))
    return _clean_thinking_effort(reasoning)


def _anthropic_budget_to_effort(budget: Any) -> str | None:
    """Reverse an Anthropic thinking budget to the closest Chat effort level."""
    if isinstance(budget, bool):
        return None
    if not isinstance(budget, int):
        return None
    if budget <= 0:
        return None
    if budget >= 32768:
        return "max"
    if budget >= 16384:
        return "high"
    if budget >= 8192:
        return "medium"
    if budget >= 2048:
        return "low"
    return "minimal"


def _effort_to_anthropic_budget(effort: str | None) -> int | None:
    if effort is None:
        return None
    return _ANTHROPIC_EFFORT_TO_BUDGET.get(effort)


def _gemini_budget_to_effort(budget: Any) -> str | None:
    """Reverse a Gemini thinking budget to the closest Chat effort level."""
    if isinstance(budget, bool):
        return None
    if not isinstance(budget, int):
        return None
    if budget == 0:
        return "none"
    if budget < 0:
        # -1 means dynamic thinking; leave the level unspecified.
        return None
    if budget >= 32768:
        return "max"
    if budget >= 24576:
        return "xhigh"
    if budget >= 16384:
        return "high"
    if budget >= 8192:
        return "medium"
    if budget >= 2048:
        return "low"
    return "minimal"


_GEMINI_DISCRETE_LEVELS: dict[str, str] = {
    "minimal": "MINIMAL",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
    "max": "HIGH",
}


def _gemini_uses_thinking_level(model: str) -> bool:
    """Gemini 3+ and Gemma 4 take a discrete thinkingLevel, not a budget."""
    identifier = model.lower()
    return bool(
        re.search(r"gemini-3(?:\.\d+)?-(?:pro|flash)", identifier)
        or re.search(r"gemini-(?:[4-9]|[1-9]\d)", identifier)
        or identifier in ("gemini-flash-latest", "gemini-flash-lite-latest")
        or re.search(r"gemma-?4", identifier)
    )


def _gemini_thinking_config(model: str, effort: str | None) -> dict[str, Any] | None:
    """Build a Gemini thinkingConfig from a Chat effort level.

    Discrete models get an enum; other models use pi\'s budget table.
    includeThoughts is enabled when thinking is requested.
    """
    if effort is None:
        return None
    if effort == "none":
        # shortcut: without a per-model level map, off uses MINIMAL for discrete
        # models; mirror pi\'s exact off fallback when capability data is available.
        if _gemini_uses_thinking_level(model):
            return {"thinkingLevel": "MINIMAL"}
        return {"thinkingBudget": 0}
    if _gemini_uses_thinking_level(model):
        level = _GEMINI_DISCRETE_LEVELS.get(effort)
        if level:
            return {"thinkingLevel": level, "includeThoughts": True}
    budget = _gemini_budget_for_model(model, effort)
    if budget is None:
        return None
    return {"thinkingBudget": budget, "includeThoughts": True}


def _gemini_budget_for_model(model: str, effort: str) -> int | None:
    """Model-specific token budget, matching pi's getGoogleBudget table."""
    identifier = model.lower()
    clamped = "high" if effort in ("xhigh", "max") else effort
    if "2.5-pro" in identifier:
        table = {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}
    elif "2.5-flash-lite" in identifier:
        table = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576}
    elif "2.5-flash" in identifier:
        table = {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}
    else:
        # pi returns -1 (dynamic) for models outside the table above.
        return -1
    return table.get(clamped)


def _sse_block_payloads(block: str) -> list[dict[str, Any] | None]:
    """Parse one SSE event block; ``None`` marks the ``[DONE]`` sentinel."""
    data_lines = [
        line[5:].strip() for line in block.splitlines() if line.startswith("data:")
    ]
    if not data_lines:
        return []
    joined = "\n".join(data_lines)
    if joined == "[DONE]":
        return [None]
    try:
        parsed = json.loads(joined)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid stream JSON") from exc
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _raw_json_payloads(text: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON objects (``{`` mode) or one complete
    top-level JSON array (``[`` mode). Malformed input raises ``ValueError``.
    """
    payloads: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid stream JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Invalid stream JSON")
        payloads.append(parsed)
    return payloads


async def _parse_sse_stream(
    raw_iterator: AsyncIterator[bytes],
    *,
    allow_raw_json: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Incrementally parse an SSE byte stream across arbitrary chunk boundaries.

    Handles LF/CRLF/lone-CR newlines (including ``\r\n`` split across chunks),
    multiline ``data:`` lines, blank-line event separators, an unterminated
    final event at EOF, and the ``[DONE]`` transport sentinel. After ``[DONE]``
    no more payloads are yielded, but the source iterator is still consumed so
    capture/resource draining stays deterministic.

    When ``allow_raw_json`` is true and the stream does not look like SSE (the
    first non-whitespace byte is ``{`` or ``[``), newline-delimited JSON objects
    and a complete top-level JSON array are parsed instead. Malformed input
    raises ``ValueError``; it is never treated as an empty successful stream.
    """
    buffer = ""
    done_seen = False
    format_seen: str | None = None  # "sse" | "ndjson" | "array"
    trailing_cr = False

    def normalize(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    async def decoded_parts() -> AsyncIterator[str]:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        async for chunk in raw_iterator:
            if done_seen:
                continue
            # Stop decoding at event boundaries so bytes after DONE are drained
            # without validating or interpreting their encoding.
            for part in re.split(rb"(?<=[\r\n])", chunk):
                if done_seen:
                    break
                text = decoder.decode(part)
                if text:
                    yield text
        if not done_seen:
            text = decoder.decode(b"", final=True)
            if text:
                yield text

    async for text in decoded_parts():
        if trailing_cr and text.startswith("\n"):
            text = text[1:]
        trailing_cr = text.endswith("\r")
        buffer += normalize(text)

        head = buffer.lstrip()
        if format_seen is None and head:
            first = head[0]
            if allow_raw_json and first in "{[":
                format_seen = "array" if first == "[" else "ndjson"
            else:
                format_seen = "sse"

        if format_seen == "sse":
            normalized = normalize(buffer)
            buffer = ""
            while "\n\n" in normalized:
                block, normalized = normalized.split("\n\n", 1)
                for payload in _sse_block_payloads(block):
                    if payload is None:
                        done_seen = True
                        break
                    yield payload
                if done_seen:
                    break
            if not done_seen:
                buffer = normalized
        elif format_seen == "ndjson":
            normalized = normalize(buffer)
            buffer = ""
            lines = normalized.split("\n")
            if lines and lines[-1] and not normalized.endswith("\n"):
                buffer = lines.pop()
            for payload in _raw_json_payloads("\n".join(lines)):
                yield payload
        # "array": buffer everything until EOF

    if trailing_cr:
        buffer += "\n"
        trailing_cr = False

    if done_seen:
        async for _ in raw_iterator:
            pass
        return

    if format_seen == "sse":
        for payload in _sse_block_payloads(normalize(buffer)):
            if payload is not None:
                yield payload
    elif format_seen == "ndjson":
        for payload in _raw_json_payloads(normalize(buffer)):
            yield payload
    elif format_seen == "array":
        stripped = buffer.strip()
        if not stripped:
            return
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid stream JSON") from exc
        if not isinstance(parsed, list):
            raise ValueError("Invalid stream JSON")
        for item in parsed:
            if isinstance(item, dict):
                yield item


async def _parse_chat_sse_stream(
    raw_iterator: AsyncIterator[bytes],
) -> AsyncIterator[dict[str, Any]]:
    async for payload in _parse_sse_stream(raw_iterator):
        yield payload


def _build_chat_tool_call(
    call_id: str,
    name: str,
    arguments: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_call = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    if source is not None:
        _copy_cache_control(tool_call, source)
    return tool_call


def _build_chat_image_part(url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def _copy_cache_control(target: dict[str, Any], source: dict[str, Any]) -> None:
    cache_control = source.get("cache_control")
    if isinstance(cache_control, dict):
        target["cache_control"] = dict(cache_control)


def _assemble_content_parts(
    text_parts: list[dict[str, Any]], image_parts: list[dict[str, Any]]
) -> list[dict[str, Any]] | str:
    if not image_parts and not _content_blocks_have_cache_control(text_parts):
        return "\n".join(str(part.get("text") or "") for part in text_parts)
    parts: list[dict[str, Any]] = []
    if text_parts:
        if _content_blocks_have_cache_control(text_parts):
            parts.extend(text_parts)
        else:
            parts.append(
                {
                    "type": "text",
                    "text": "\n".join(
                        str(part.get("text") or "") for part in text_parts
                    ),
                }
            )
    parts.extend(image_parts)
    return parts


def _content_blocks_have_cache_control(parts: list[dict[str, Any]]) -> bool:
    return any(isinstance(part.get("cache_control"), dict) for part in parts)


def _assemble_anthropic_chat_content(
    text_parts: list[dict[str, Any]], image_parts: list[dict[str, Any]]
) -> list[dict[str, Any]] | str | None:
    if not text_parts and not image_parts:
        return None
    if not image_parts and not _content_blocks_have_cache_control(text_parts):
        return "\n".join(str(part.get("text") or "") for part in text_parts)
    if not _content_blocks_have_cache_control(text_parts):
        parts: list[dict[str, Any]] = []
        text = "\n".join(str(part.get("text") or "") for part in text_parts)
        if text_parts:
            parts.append({"type": "text", "text": text})
        parts.extend(image_parts)
        return parts
    return [*text_parts, *image_parts]


def _anthropic_blocks_to_chat_content(
    content: Any,
) -> list[dict[str, Any]] | str | None:
    if not isinstance(content, list):
        return None

    text_parts: list[dict[str, Any]] = []
    image_parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                part = {"type": "text", "text": text}
                _copy_cache_control(part, block)
                text_parts.append(part)
        elif block_type == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                part = _build_chat_image_part(f"data:{media_type};base64,{data}")
                _copy_cache_control(part, block)
                image_parts.append(part)
            elif source.get("type") == "url":
                part = _build_chat_image_part(source.get("url", ""))
                _copy_cache_control(part, block)
                image_parts.append(part)

    return _assemble_anthropic_chat_content(text_parts, image_parts)


def anthropic_content_to_chat_messages(
    messages: list[dict[str, Any]],
    *,
    preserve_thinking: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        if not isinstance(content, list):
            result.append({"role": role, "content": content})
            continue

        chat_content = _anthropic_blocks_to_chat_content(content)
        thinking_parts: list[str] = []
        thinking_signatures: list[str] = []
        reasoning_blocks: list[dict[str, Any]] = []
        redacted_blocks: list[dict[str, Any]] = []
        has_thinking = False
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "thinking":
                has_thinking = True
                reasoning_block = {
                    "type": "thinking",
                    "thinking": block.get("thinking", ""),
                }
                if isinstance(block.get("signature"), str):
                    reasoning_block["signature"] = block["signature"]
                reasoning_blocks.append(reasoning_block)
                thinking = block.get("thinking")
                thinking_parts.append(thinking if isinstance(thinking, str) else "")
                signature = block.get("signature")
                if isinstance(signature, str) and signature:
                    thinking_signatures.append(signature)
            elif bt == "redacted_thinking":
                has_thinking = True
                data = block.get("data")
                if isinstance(data, str) and data:
                    redacted_blocks.append({"type": "redacted_thinking", "data": data})
                    reasoning_blocks.append({"type": "redacted_thinking", "data": data})
            elif bt == "tool_use":
                tool_calls.append(
                    _build_chat_tool_call(
                        block.get("id", ""),
                        block.get("name", ""),
                        json.dumps(block.get("input", {}), ensure_ascii=False),
                        block,
                    )
                )
            elif bt == "tool_result":
                tool_content = _anthropic_blocks_to_chat_content(block.get("content"))
                if tool_content is None:
                    tool_content = block.get("content", "")
                cache_control = block.get("cache_control")
                if isinstance(cache_control, dict):
                    if isinstance(tool_content, list):
                        if tool_content and not _content_blocks_have_cache_control(
                            tool_content
                        ):
                            _copy_cache_control(tool_content[-1], block)
                    else:
                        tool_content = [
                            {
                                "type": "text",
                                "text": str(tool_content),
                                "cache_control": dict(cache_control),
                            }
                        ]
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": tool_content,
                        **(
                            {"is_error": bool(block["is_error"])}
                            if "is_error" in block
                            else {}
                        ),
                    }
                )

        if role == "assistant" and tool_calls:
            msg_out: dict[str, Any] = {
                "role": "assistant",
                "content": chat_content if chat_content is not None else None,
            }
            if preserve_thinking and has_thinking:
                msg_out["reasoning_content"] = "\n".join(thinking_parts)
                msg_out["reasoning_blocks"] = reasoning_blocks
                if len(thinking_parts) == 1 and thinking_signatures:
                    msg_out["reasoning_signature"] = thinking_signatures[-1]
                if redacted_blocks:
                    msg_out["reasoning_redacted"] = redacted_blocks
            msg_out["tool_calls"] = tool_calls
            result.append(msg_out)
        elif chat_content is not None:
            msg_out = {"role": role, "content": chat_content}
            if role == "assistant" and preserve_thinking and has_thinking:
                msg_out["reasoning_content"] = "\n".join(thinking_parts)
                msg_out["reasoning_blocks"] = reasoning_blocks
                if len(thinking_parts) == 1 and thinking_signatures:
                    msg_out["reasoning_signature"] = thinking_signatures[-1]
                if redacted_blocks:
                    msg_out["reasoning_redacted"] = redacted_blocks
            result.append(msg_out)
        elif role == "assistant" and preserve_thinking and has_thinking:
            msg_out = {
                "role": "assistant",
                "content": None,
                "reasoning_content": "\n".join(thinking_parts),
            }
            msg_out["reasoning_blocks"] = reasoning_blocks
            if len(thinking_parts) == 1 and thinking_signatures:
                msg_out["reasoning_signature"] = thinking_signatures[-1]
            if redacted_blocks:
                msg_out["reasoning_redacted"] = redacted_blocks
            result.append(msg_out)

        for tr in tool_results:
            result.append(tr)

    return result


def anthropic_tools_to_chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in tools:
        entry = {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        _copy_cache_control(entry, tool)
        result.append(entry)
    return result


def anthropic_tool_choice_to_chat(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return None
    ct = tool_choice.get("type", "auto")
    if ct == "auto":
        return "auto"
    if ct == "any":
        return "required"
    if ct == "tool":
        return {"type": "function", "function": {"name": tool_choice.get("name", "")}}
    if ct == "none":
        return "none"
    return "auto"


def _normalize_anthropic_tool_id(tool_id: str) -> str:
    """Anthropic requires tool ids to match [a-zA-Z0-9_-] and be at most 64 chars."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id)[:64]


def chat_tool_calls_to_anthropic_content(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for tc in tool_calls:
        func = tc.get("function", {})
        try:
            parsed_input = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Invalid tool call arguments JSON") from exc
        block = {
            "type": "tool_use",
            "id": _normalize_anthropic_tool_id(str(tc.get("id") or "")),
            "name": func.get("name", ""),
            "input": parsed_input,
        }
        _copy_cache_control(block, tc)
        if "cache_control" not in block and isinstance(func, dict):
            _copy_cache_control(block, func)
        blocks.append(block)
    return blocks


def responses_input_to_chat_messages(
    input_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages, _, _ = _responses_input_to_chat_parts(input_items)
    return messages


def _responses_input_to_chat_parts(
    input_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Split Responses input items into Chat messages, embedded tool
    definitions (additional_tools / tool_search_output items), and the
    latest configuration_update reasoning effort, if any."""
    result: list[dict[str, Any]] = []
    embedded_tools: list[dict[str, Any]] = []
    configuration_effort: str | None = None
    pending_tool_calls: list[dict[str, Any]] = []
    # pi emits one assistant turn as separate message + function_call items; Chat
    # carries both in a single assistant message, so the text stays pending until
    # the tool calls of the same turn are known.
    pending_assistant_text: str | None = None
    pending_assistant_reasoning: str | None = None
    pending_assistant_extra: list[dict[str, Any]] = []
    pending_assistant_parts: list[dict[str, Any]] = []
    pending_reasoning_items: list[dict[str, Any]] = []
    pending_assistant_cache_control: dict[str, Any] | None = None

    def flush_assistant_turn() -> None:
        """Emit the pending assistant turn as one Chat message.

        Reasoning, text and tool calls of one upstream assistant turn arrive as
        separate Responses items, while Chat carries all three on a single
        assistant message, so they are buffered and flushed together.
        """
        nonlocal pending_assistant_text, pending_assistant_reasoning
        nonlocal pending_assistant_cache_control
        has_content = (
            pending_assistant_text is not None
            or bool(pending_tool_calls)
            or bool(pending_assistant_extra)
            or bool(pending_reasoning_items)
        )
        if pending_assistant_reasoning is not None and not has_content:
            # Chat upstreams reject an assistant message with null content and no
            # tool calls, so orphan reasoning is dropped rather than emitted.
            pending_assistant_reasoning = None
        if has_content or pending_assistant_reasoning is not None:
            message: dict[str, Any] = {
                "role": "assistant",
                "content": pending_assistant_text,
            }
            if pending_assistant_reasoning is not None:
                message["reasoning_content"] = pending_assistant_reasoning
            if pending_tool_calls:
                message["tool_calls"] = list(pending_tool_calls)
            if pending_assistant_extra:
                message["content"] = list(pending_assistant_parts)
            if pending_reasoning_items:
                message["_responses_reasoning_items"] = list(pending_reasoning_items)
            if pending_assistant_cache_control is not None:
                _copy_cache_control(message, pending_assistant_cache_control)
            result.append(message)
        pending_tool_calls.clear()
        pending_assistant_text = None
        pending_assistant_reasoning = None
        pending_assistant_extra.clear()
        pending_assistant_parts.clear()
        pending_reasoning_items.clear()
        pending_assistant_cache_control = None

    for item in input_items:
        role = item.get("role", "user")
        if role == "developer":
            role = "system"
        content = item.get("content")
        item_type = item.get("type")

        # Codex/pi agents embed tool definitions in additional_tools items and
        # mid-turn thinking-level changes in configuration_update items. Neither
        # carries conversational content, so they must not become messages.
        if item_type in ("additional_tools", "tool_search_output"):
            tools = item.get("tools")
            if isinstance(tools, list):
                embedded_tools.extend(tools)
            continue
        if item_type == "tool_search_call":
            continue
        if item_type == "configuration_update":
            reasoning = item.get("reasoning")
            if isinstance(reasoning, dict):
                effort = _clean_thinking_effort(reasoning.get("effort"))
                if effort:
                    configuration_effort = effort
            continue

        if item_type == "function_call_output":
            flush_assistant_turn()
            output = item.get("output", "")
            message = {
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": output,
            }
            _copy_cache_control(message, item)
            result.append(message)
            continue

        if item_type == "function_call":
            # Consecutive function_call items are parallel tool calls; merge
            # them into one assistant message so the resulting Chat history
            # keeps each assistant tool_calls message followed by its tool
            # responses, which upstreams require.
            pending_tool_calls.append(
                _build_chat_tool_call(
                    item.get("call_id", ""),
                    item.get("name", ""),
                    item.get("arguments", "{}"),
                    item,
                )
            )
            continue

        if item_type == "reasoning":
            if (
                isinstance(item.get("id"), str)
                and item["id"]
                and isinstance(item.get("summary"), list)
            ):
                pending_reasoning_items.append(dict(item))
            summary = item.get("summary") or item.get("content") or []
            reasoning_text = "\n\n".join(
                str(part.get("text") or "")
                for part in summary
                if isinstance(part, dict)
            )
            if reasoning_text:
                pending_assistant_reasoning = (
                    pending_assistant_reasoning or ""
                ) + reasoning_text
            continue

        if role == "assistant":
            # Assistant content stays in the pending turn so reasoning, text and
            # tool calls arrive as one Chat message. Non-text parts (images) and
            # cache_control breakpoints cannot be held there, so they are kept
            # aside and appended to the emitted turn.
            text = _assistant_item_text(item, content)
            if text:
                pending_assistant_text = (
                    pending_assistant_text + text if pending_assistant_text else text
                )
            blocks = content if isinstance(content, list) else [content]
            for block in blocks:
                block_text = _assistant_item_text(
                    item, [block] if isinstance(block, dict) else block
                )
                if block_text:
                    part = {"type": "text", "text": block_text}
                    if isinstance(block, dict):
                        _copy_cache_control(part, block)
                    pending_assistant_parts.append(part)
                pending_assistant_parts.extend(_assistant_item_extra_parts([block]))
            extra = _assistant_item_extra_parts(content)
            if extra:
                pending_assistant_extra.extend(extra)
            if _has_cache_control(item):
                pending_assistant_cache_control = item
            continue

        flush_assistant_turn()

        if isinstance(content, str):
            message = {"role": role, "content": content}
            _copy_cache_control(message, item)
            result.append(message)
            continue

        if isinstance(content, list):
            text_parts: list[dict[str, Any]] = []
            image_parts: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype in ("input_text", "output_text", "text"):
                    part = {"type": "text", "text": block.get("text", "")}
                    _copy_cache_control(part, block)
                    text_parts.append(part)
                elif btype == "input_image":
                    url = block.get("image_url", "")
                    if isinstance(url, dict):
                        url = url.get("url", "")
                    part = _build_chat_image_part(url)
                    _copy_cache_control(part, block)
                    image_parts.append(part)
            if image_parts or text_parts:
                message = {
                    "role": role,
                    "content": _assemble_content_parts(text_parts, image_parts),
                }
                _copy_cache_control(message, item)
                result.append(message)
            continue

        # Typed items (reasoning replay, custom_tool_call_output, ...) and
        # unknown item types carry no Chat-representable content; skip them
        # instead of emitting placeholder messages with null content.
        if not item_type and content is not None:
            result.append({"role": role or "user", "content": content})

    flush_assistant_turn()
    return result, embedded_tools, configuration_effort


def _has_cache_control(item: dict[str, Any]) -> bool:
    return isinstance(item.get("cache_control"), dict)


def _assistant_item_extra_parts(content: Any) -> list[dict[str, Any]]:
    """Collect non-text assistant parts that must survive the turn merge."""
    if not isinstance(content, list):
        return []
    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in ("input_image", "output_image"):
            continue
        url = block.get("image_url")
        if isinstance(url, dict):
            url = url.get("url", "")
        if not isinstance(url, str) or not url:
            continue
        part = _build_chat_image_part(url)
        _copy_cache_control(part, block)
        parts.append(part)
    return parts


def _assistant_item_text(item: dict[str, Any], content: Any) -> str:
    """Extract replayed assistant text from a Responses item."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("refusal") or block.get("text") or "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") in ("output_text", "text", "refusal", "input_text")
    )


def responses_tools_to_chat_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func_def: dict[str, Any] = {"name": tool.get("name", "")}
        if "description" in tool:
            func_def["description"] = tool["description"]
        if "parameters" in tool:
            func_def["parameters"] = tool["parameters"]
        entry: dict[str, Any] = {"type": "function", "function": func_def}
        if tool.get("strict") is not None:
            entry["function"]["strict"] = tool["strict"]
        _copy_cache_control(entry, tool)
        result.append(entry)
    return result


def format_sse_event(event: str | None, data: dict[str, Any] | str) -> bytes:
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if isinstance(data, dict):
        lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    else:
        lines.append(f"data: {data}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")
