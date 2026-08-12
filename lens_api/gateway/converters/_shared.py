import json
from collections.abc import Mapping
from typing import Any, AsyncIterator

FINISH_REASON_CHAT_TO_ANTHROPIC: dict[str | None, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
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

# pi-agent default Anthropic budget per level (xhigh/max clamp to high on
# budget-based Anthropic models); max uses a larger budget so it survives
# gateways whose upstream accepts extended efforts (e.g. DeepSeek).
_ANTHROPIC_EFFORT_TO_BUDGET: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 16384,
    "max": 32768,
}

# Gemini 2.5-style thinking budgets within the documented 128..32768 range.
_GEMINI_EFFORT_TO_BUDGET: dict[str, int] = {
    "minimal": 512,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 24576,
    "max": 32768,
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


def _effort_to_gemini_budget(effort: str | None) -> int | None:
    if effort is None:
        return None
    return _GEMINI_EFFORT_TO_BUDGET.get(effort)


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

    async for chunk in raw_iterator:
        if done_seen:
            continue
        text = chunk.decode("utf-8", errors="replace")
        if trailing_cr:
            buffer += "\n"
            if text.startswith("\n"):
                text = text[1:]
            trailing_cr = False
        buffer += text
        if buffer.endswith("\r"):
            buffer = buffer[:-1]
            trailing_cr = True

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
        has_thinking = False
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "thinking":
                has_thinking = True
                thinking = block.get("thinking")
                thinking_parts.append(thinking if isinstance(thinking, str) else "")
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
                    }
                )

        if role == "assistant" and tool_calls:
            msg_out: dict[str, Any] = {
                "role": "assistant",
                "content": chat_content if chat_content is not None else None,
            }
            if preserve_thinking and has_thinking:
                msg_out["reasoning_content"] = "\n".join(thinking_parts)
            msg_out["tool_calls"] = tool_calls
            result.append(msg_out)
        elif chat_content is not None:
            msg_out = {"role": role, "content": chat_content}
            if role == "assistant" and preserve_thinking and has_thinking:
                msg_out["reasoning_content"] = "\n".join(thinking_parts)
            result.append(msg_out)
        elif role == "assistant" and preserve_thinking and has_thinking:
            result.append(
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "\n".join(thinking_parts),
                }
            )

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
            "id": tc.get("id", ""),
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
    result: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []

    def flush_pending_tool_calls() -> None:
        if pending_tool_calls:
            result.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": list(pending_tool_calls),
                }
            )
            pending_tool_calls.clear()

    for item in input_items:
        role = item.get("role", "user")
        if role == "developer":
            role = "system"
        content = item.get("content")
        item_type = item.get("type")

        if item_type == "function_call_output":
            flush_pending_tool_calls()
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

        flush_pending_tool_calls()

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

        if role or content is not None:
            result.append({"role": role or "user", "content": content})

    flush_pending_tool_calls()
    return result


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
