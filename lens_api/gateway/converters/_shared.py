import json
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


async def _parse_chat_sse_stream(
    raw_iterator: AsyncIterator[bytes],
) -> AsyncIterator[dict[str, Any]]:
    buffer = b""
    done_seen = False
    async for chunk in raw_iterator:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str.startswith("data:"):
                continue
            data_str = line_str[5:].strip()
            if data_str == "[DONE]":
                done_seen = True
                continue
            if done_seen:
                continue
            try:
                yield json.loads(data_str)
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid stream JSON") from exc


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
    for item in input_items:
        role = item.get("role", "user")
        if role == "developer":
            role = "system"
        content = item.get("content")
        item_type = item.get("type")

        if item_type == "function_call_output":
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
            result.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        _build_chat_tool_call(
                            item.get("call_id", ""),
                            item.get("name", ""),
                            item.get("arguments", "{}"),
                            item,
                        )
                    ],
                }
            )
            continue

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
