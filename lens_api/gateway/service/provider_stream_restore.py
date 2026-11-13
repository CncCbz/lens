from __future__ import annotations

from copy import deepcopy
import base64
import json
from typing import Any


def restore_pi_events(
    events: list[dict[str, Any]], *, allow_incomplete: bool = False
) -> dict[str, Any] | None:
    blocks: dict[int, dict[str, Any]] = {}
    ended_tools: set[int] = set()
    ended_blocks: set[int] = set()
    terminal = None
    for event in events:
        kind = event.get("type")
        index = event.get("contentIndex", 0)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError("Invalid pi-messages content index")
        if kind == "error":
            raise ValueError(
                str(
                    event.get("errorMessage")
                    or event.get("message")
                    or event.get("error")
                    or "pi-messages upstream error"
                )
            )
        if kind in {"text_start", "thinking_start", "toolcall_start"}:
            if index != len(blocks):
                raise ValueError("Invalid pi-messages content block start")
        elif isinstance(kind, str) and kind.endswith(("_delta", "_end")):
            expected = {
                "text": "text",
                "thinking": "thinking",
                "toolcall": "toolCall",
            }.get(kind.split("_", 1)[0])
            if expected is not None and (
                index not in blocks
                or blocks[index].get("type") != expected
                or index in ended_blocks
            ):
                raise ValueError("Invalid pi-messages content block state")
        if kind in {"text_start", "thinking_start"}:
            thinking = kind == "thinking_start"
            blocks[index] = {
                "type": "thinking" if thinking else "text",
                "thinking" if thinking else "text": "",
            }
        elif kind in {"text_delta", "thinking_delta"}:
            key = "thinking" if kind == "thinking_delta" else "text"
            value = event.get("delta")
            if not isinstance(value, str):
                raise ValueError("pi-messages delta must be text")
            block = blocks[index]
            block[key] = str(block.get(key) or "") + value
        elif kind in {"text_end", "thinking_end"}:
            key = "thinking" if kind == "thinking_end" else "text"
            block = blocks[index]
            value = event.get("content")
            if not isinstance(value, str) or not value.startswith(block.get(key, "")):
                raise ValueError("pi-messages block end disagrees with deltas")
            block[key] = value
            ended_blocks.add(index)
            if event.get("contentSignature") is not None:
                block["thinkingSignature" if key == "thinking" else "textSignature"] = (
                    event["contentSignature"]
                )
            if event.get("redacted"):
                block["redacted"] = True
        elif kind == "toolcall_start":
            blocks[index] = {
                "type": "toolCall",
                "id": event.get("id", ""),
                "name": event.get("toolName", ""),
                "arguments": {},
            }
        elif kind == "toolcall_delta":
            value = event.get("delta")
            if not isinstance(value, str):
                raise ValueError("pi-messages tool delta must be text")
            block = blocks[index]
            block["_arguments"] = str(block.get("_arguments") or "") + value
        elif kind == "toolcall_end":
            call = event.get("toolCall")
            if not isinstance(call, dict) or not isinstance(
                call.get("arguments"), dict
            ):
                raise ValueError("Invalid pi-messages tool call")
            if "argumentsError" in call:
                raise ValueError(str(call["argumentsError"]))
            previous = blocks[index]
            if call.get("id") != previous.get("id") or call.get("name") != previous.get(
                "name"
            ):
                raise ValueError("pi-messages tool call identity changed")
            if previous.get("_arguments"):
                try:
                    from ..converters.chat_to_pi import (
                        _parse_pi_arguments,
                        _same_json_value,
                    )

                    arguments = _parse_pi_arguments(previous["_arguments"])
                except ValueError:
                    raise ValueError("Invalid pi-messages tool arguments") from None
                if not _same_json_value(arguments, call["arguments"]):
                    raise ValueError("pi-messages tool call end disagrees with deltas")
            blocks[index] = {**deepcopy(call), "type": "toolCall"}
            ended_tools.add(index)
            ended_blocks.add(index)
        elif kind == "done":
            if event.get("reason") not in {"stop", "length", "toolUse"}:
                raise ValueError(
                    f"pi-messages stream terminated: {event.get('reason')}"
                )
            terminal = event
    if terminal is None and not allow_incomplete:
        return None
    if (
        terminal is not None
        and terminal.get("reason") != "length"
        and not allow_incomplete
        and any(
            block.get("type") == "toolCall" and index not in ended_tools
            for index, block in blocks.items()
        )
    ):
        raise ValueError("pi-messages tool call ended before toolcall_end")
    for block in blocks.values():
        arguments = block.pop("_arguments", None)
        if arguments:
            try:
                block["arguments"] = json.loads(arguments)
            except json.JSONDecodeError:
                if not allow_incomplete and terminal.get("reason") != "length":
                    raise ValueError("Invalid pi-messages tool arguments") from None
                block["arguments"] = arguments
    terminal = terminal or {}
    return {
        "role": "assistant",
        "content": [blocks[i] for i in sorted(blocks)],
        "stopReason": terminal.get("reason", "pending"),
        "usage": deepcopy(terminal.get("usage") or {}),
        "responseId": terminal.get("responseId"),
        "model": terminal.get("responseModel", ""),
        "api": "pi-messages",
        **{
            key: deepcopy(terminal[key])
            for key in ("providerThinkingLevel", "rewrite", "responseModel")
            if key in terminal
        },
    }


def restore_bedrock_events(
    events: list[dict[str, Any]], *, allow_incomplete: bool = False
) -> dict[str, Any] | None:
    blocks: dict[int, dict[str, Any]] = {}
    stop = None
    stopped_blocks: set[int] = set()
    usage: dict[str, Any] = {}
    for event in events:
        if any(k.endswith("Exception") for k in event):
            raise ValueError(f"Bedrock stream error: {event}")
        if isinstance(event.get("messageStop"), dict):
            stop = event["messageStop"].get("stopReason")
        if isinstance(event.get("metadata"), dict):
            usage.update(event["metadata"].get("usage") or {})
        stopped = event.get("contentBlockStop")
        if isinstance(stopped, dict):
            stopped_blocks.add(int(stopped.get("contentBlockIndex", 0)))
        start = event.get("contentBlockStart")
        if isinstance(start, dict):
            index = int(start.get("contentBlockIndex", 0))
            value = deepcopy(start.get("start") or {})
            blocks[index] = value
        delta = event.get("contentBlockDelta")
        if isinstance(delta, dict):
            index = int(delta.get("contentBlockIndex", 0))
            block = blocks.setdefault(index, {})
            value = delta.get("delta") or {}
            if "text" in value:
                block["text"] = str(block.get("text") or "") + str(value["text"])
            if isinstance(value.get("toolUse"), dict):
                tool = block.setdefault("toolUse", {})
                tool["input"] = str(tool.get("input") or "") + str(
                    value["toolUse"].get("input") or ""
                )
            if isinstance(value.get("reasoningContent"), dict):
                reasoning = block.setdefault("reasoningContent", {})
                source = value["reasoningContent"]
                for key in ("text", "signature"):
                    if source.get(key) is not None:
                        target = reasoning.setdefault("reasoningText", {})
                        target[key] = str(target.get(key) or "") + str(source[key])
                if source.get("redactedContent") is not None:
                    encoded = source["redactedContent"]
                    if not isinstance(encoded, str):
                        raise ValueError("Bedrock redactedContent must be base64 text")
                    try:
                        previous = base64.b64decode(
                            reasoning.get("redactedContent", ""), validate=True
                        )
                        current = base64.b64decode(encoded, validate=True)
                    except ValueError:
                        raise ValueError(
                            "Invalid Bedrock redactedContent base64"
                        ) from None
                    reasoning["redactedContent"] = base64.b64encode(
                        previous + current
                    ).decode("ascii")
    if not stop and not allow_incomplete:
        return None
    if stop and stop not in {
        "end_turn",
        "stop_sequence",
        "tool_use",
        "max_tokens",
        "guardrail_intervened",
        "content_filtered",
        "malformed_model_output",
        "malformed_tool_use",
        "model_context_window_exceeded",
    }:
        raise ValueError(f"Bedrock stream terminated: {stop}")
    if stop in {"malformed_model_output", "malformed_tool_use"}:
        raise ValueError(f"Bedrock stream terminated: {stop}")
    if (
        stop in {"end_turn", "stop_sequence", "tool_use"}
        and not allow_incomplete
        and any(
            "toolUse" in block and index not in stopped_blocks
            for index, block in blocks.items()
        )
    ):
        raise ValueError("Bedrock tool call ended before contentBlockStop")
    for block in blocks.values():
        tool = block.get("toolUse")
        if isinstance(tool, dict) and isinstance(tool.get("input"), str):
            try:
                tool["input"] = json.loads(tool["input"] or "{}")
            except json.JSONDecodeError:
                if not allow_incomplete and stop not in {
                    "max_tokens",
                    "guardrail_intervened",
                    "content_filtered",
                }:
                    raise ValueError("Invalid Bedrock tool arguments") from None
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [blocks[i] for i in sorted(blocks)],
            }
        },
        "stopReason": stop,
        "usage": usage,
    }
