from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from ._shared import _extract_chat_reasoning_effort, _parse_sse_stream
from .chat_to_responses import normalize_openai_responses_request

__all__ = [
    "mistral_request_to_chat",
    "chat_request_to_mistral",
    "mistral_response_to_chat",
    "chat_response_to_mistral",
    "mistral_stream_to_chat_stream",
    "chat_stream_to_mistral_stream",
    "normalize_codex_request",
    "normalize_azure_responses_request",
    "codex_stream_to_responses_stream",
]


class _MistralToolIds:
    def __init__(self) -> None:
        self.ids: dict[str, str] = {}
        self.owners: dict[str, str] = {}

    def normalize(self, value: str) -> str:
        if value in self.ids:
            return self.ids[value]
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", value)
        attempt = 0
        while True:
            seed = cleaned or value
            if attempt:
                seed = f"{seed}:{attempt}"
            candidate = (
                cleaned
                if not attempt and len(cleaned) == 9
                else hashlib.sha256(seed.encode()).hexdigest()[:9]
            )
            if candidate not in self.owners:
                self.ids[value] = candidate
                self.owners[candidate] = value
                return candidate
            attempt += 1


def _check_error(body: dict[str, Any], provider: str) -> None:
    if "error" in body or body.get("type") == "error":
        raise ValueError(f"{provider} error: {body.get('error', body.get('message'))}")


def _finish_reason(reason: Any) -> Any:
    if reason is None or reason == "":
        return reason
    if reason not in {"stop", "length", "model_length", "tool_calls", "content_filter"}:
        raise ValueError(f"Mistral stream/response finish reason: {reason}")
    return "length" if reason == "model_length" else reason


def _thinking_text(chunk: dict[str, Any]) -> str:
    thinking = chunk.get("thinking")
    if isinstance(thinking, str):
        return thinking
    if isinstance(thinking, list):
        return "".join(
            part["text"]
            for part in thinking
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _mistral_message_to_chat(message: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(message)
    content = message.get("content")
    if isinstance(content, list):
        visible: list[Any] = []
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                visible.append(copy.deepcopy(part))
                continue
            if part.get("type") == "thinking":
                blocks.append({**copy.deepcopy(part), "thinking": _thinking_text(part)})
                continue
            part = copy.deepcopy(part)
            if part.get("type") == "image_url" and isinstance(
                part.get("image_url"), str
            ):
                part["image_url"] = {"url": part["image_url"]}
            visible.append(part)
        result["content"] = (
            "".join(part.get("text", "") for part in visible)
            if all(
                isinstance(part, dict)
                and part.get("type") == "text"
                and set(part) <= {"type", "text"}
                for part in visible
            )
            else visible
        )
        if blocks:
            result["reasoning_content"] = "".join(block["thinking"] for block in blocks)
            result["reasoning_blocks"] = blocks
            if len(blocks) == 1 and "signature" in blocks[0]:
                result["reasoning_signature"] = blocks[0]["signature"]
        # Keep ordered native chunks for exact replay, including empty signed blocks.
        result["_mistral_content"] = copy.deepcopy(content)
    for call in result.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function")
            if isinstance(function, dict) and "arguments" in function:
                function["arguments"] = _arguments(function["arguments"])
    return result


def _arguments(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def _chat_message_to_mistral(
    message: dict[str, Any], ids: _MistralToolIds
) -> dict[str, Any]:
    result = copy.deepcopy(message)
    native = result.pop("_mistral_content", None)
    reasoning_keys = ("reasoning_content", "reasoning_blocks", "reasoning_signature")
    replay = False
    if isinstance(native, list):
        snapshot = _mistral_message_to_chat({"content": native})
        replay = all(
            message.get(key) == snapshot.get(key)
            for key in ("content", *reasoning_keys)
        )
    if replay:
        result["content"] = native
    else:
        blocks = message.get("reasoning_blocks")
        thinking: list[dict[str, Any]] = []
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    text = block.get("thinking", "")
                    thinking.append(
                        {
                            **copy.deepcopy(block),
                            "thinking": (
                                [{"type": "text", "text": text}]
                                if isinstance(text, str)
                                else copy.deepcopy(text)
                            ),
                        }
                    )
        if not thinking and any(
            key in message
            for key in ("reasoning_content", "reasoning_signature", "reasoning")
        ):
            text = message.get("reasoning_content", message.get("reasoning", ""))
            if text is None and "reasoning_signature" in message:
                text = ""
            if isinstance(text, str):
                block = {
                    "type": "thinking",
                    "thinking": [{"type": "text", "text": text}],
                }
                if "reasoning_signature" in message:
                    block["signature"] = message["reasoning_signature"]
                thinking.append(block)
        if len(thinking) == 1 and "reasoning_signature" in message:
            thinking[0].setdefault("signature", message["reasoning_signature"])
        content = copy.deepcopy(message.get("content"))
        if isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "image_url"
                    and isinstance(part.get("image_url"), dict)
                ):
                    part["image_url"] = part["image_url"].get("url", "")
        if thinking:
            visible = (
                content
                if isinstance(content, list)
                else (
                    [{"type": "text", "text": content}]
                    if isinstance(content, str)
                    else []
                )
            )
            result["content"] = [*thinking, *visible]
        elif "content" in message:
            result["content"] = content
    for key in (*reasoning_keys, "reasoning"):
        result.pop(key, None)
    if result.get("role") == "developer":
        result["role"] = "system"
    if "tool_call_id" in result:
        result["tool_call_id"] = ids.normalize(str(result["tool_call_id"]))
    for position, call in enumerate(result.get("tool_calls") or []):
        if not isinstance(call, dict):
            continue
        call["id"] = ids.normalize(str(call.get("id") or f"toolcall:{position}"))
        function = call.get("function")
        if isinstance(function, dict) and "arguments" in function:
            function["arguments"] = _arguments(function["arguments"])
    return result


def mistral_request_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(body)
    result["messages"] = [
        _mistral_message_to_chat(message) for message in body.get("messages", [])
    ]
    return result


def chat_request_to_mistral(body: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(body)
    ids = _MistralToolIds()
    result["messages"] = [
        _chat_message_to_mistral(message, ids) for message in body.get("messages", [])
    ]
    effort = _extract_chat_reasoning_effort(body)
    if effort is not None:
        result["reasoning_effort"] = effort
    result.pop("reasoning", None)
    result.pop("reasoningEffort", None)
    if "max_completion_tokens" in result:
        result["max_tokens"] = result.pop("max_completion_tokens")
    return result


def _mistral_response(
    body: dict[str, Any], original_model: str, *, to_chat: bool
) -> dict[str, Any]:
    _check_error(body, "Mistral")
    result = copy.deepcopy(body)
    ids = _MistralToolIds()
    if original_model:
        result["model"] = original_model
    for choice in result.get("choices") or []:
        if "finish_reason" in choice:
            choice["finish_reason"] = _finish_reason(choice["finish_reason"])
        message = choice.get("message")
        if isinstance(message, dict):
            choice["message"] = (
                _mistral_message_to_chat(message)
                if to_chat
                else _chat_message_to_mistral(message, ids)
            )
    return result


def mistral_response_to_chat(
    body: dict[str, Any], original_model: str = ""
) -> dict[str, Any]:
    return _mistral_response(body, original_model, to_chat=True)


def chat_response_to_mistral(
    body: dict[str, Any], original_model: str = ""
) -> dict[str, Any]:
    return _mistral_response(body, original_model, to_chat=False)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


async def _mistral_stream(
    iterator: AsyncIterator[bytes], original_model: str, *, to_chat: bool
) -> AsyncIterator[bytes]:
    ids = _MistralToolIds()
    tool_ids: dict[tuple[int, int], str] = {}
    indices_by_id: dict[tuple[int, str], int] = {}
    used_indices: dict[int, set[int]] = {}
    observed: set[int] = set()
    finished: set[int] = set()
    async for payload in _parse_sse_stream(iterator):
        _check_error(payload, "Mistral")
        if not isinstance(payload.get("choices"), list):
            raise ValueError("Invalid Mistral stream event: expected choices")
        payload = copy.deepcopy(payload)
        if original_model:
            payload["model"] = original_model
        for position, choice in enumerate(payload.get("choices") or []):
            index = choice.get("index", position)
            observed.add(index)
            if "finish_reason" in choice:
                choice["finish_reason"] = _finish_reason(choice["finish_reason"])
                if choice["finish_reason"]:
                    finished.add(index)
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            # A continuation must not acquire a fresh ID merely because its header is absent.
            calls = copy.deepcopy(delta.get("tool_calls"))
            if isinstance(calls, list):
                for call_index, call in enumerate(calls):
                    if not isinstance(call, dict):
                        continue
                    incoming = call.get("id")
                    incoming = str(incoming) if incoming and incoming != "null" else ""
                    call_position = call.get("index")
                    used = used_indices.setdefault(index, set())
                    if call_position is None:
                        call_position = (
                            indices_by_id.get((index, incoming))
                            if incoming
                            else call_index
                        )
                        if call_position is None:
                            call_position = 0
                            while call_position in used:
                                call_position += 1
                    if not isinstance(call_position, int) or isinstance(
                        call_position, bool
                    ):
                        raise ValueError("Mistral tool call index must be an integer")
                    used.add(call_position)
                    call["index"] = call_position
                    key = (index, call_position)
                    if incoming:
                        indices_by_id[(index, incoming)] = call_position
                        tool_ids[key] = (
                            ids.normalize(incoming) if not to_chat else incoming
                        )
                        call["id"] = tool_ids[key]
                    elif key not in tool_ids:
                        tool_ids[key] = ids.normalize(f"toolcall:{key[0]}:{key[1]}")
                        call["id"] = tool_ids[key]
                    else:
                        call.pop("id", None)
                    function = call.get("function")
                    if isinstance(function, dict) and "arguments" in function:
                        function["arguments"] = _arguments(function["arguments"])
            if to_chat:
                delta = _mistral_message_to_chat(delta)
            else:
                delta = _chat_message_to_mistral(
                    {key: value for key, value in delta.items() if key != "tool_calls"},
                    ids,
                )
            if isinstance(calls, list):
                delta["tool_calls"] = calls
            choice["delta"] = delta
        yield _sse(payload)
    if not observed or observed - finished:
        raise ValueError(
            "Mistral stream ended without a finish reason for every choice"
        )
    yield b"data: [DONE]\n\n"


async def mistral_stream_to_chat_stream(
    iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    async for chunk in _mistral_stream(iterator, original_model, to_chat=True):
        yield chunk


async def chat_stream_to_mistral_stream(
    iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    async for chunk in _mistral_stream(iterator, original_model, to_chat=False):
        yield chunk


def normalize_azure_responses_request(body: dict[str, Any]) -> dict[str, Any]:
    return normalize_openai_responses_request(body)


def _instruction_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(part, dict)
        and part.get("type") in {"text", "input_text"}
        and isinstance(part.get("text"), str)
        for part in content
    ):
        return "\n".join(part["text"] for part in content)
    return None


def normalize_codex_request(
    body: dict[str, Any], *, normalize_effort: bool = True
) -> dict[str, Any]:
    payload = copy.deepcopy(
        normalize_openai_responses_request(body) if normalize_effort else body
    )
    payload["store"] = False
    payload["stream"] = True
    instructions = payload.get("instructions")
    parts = [instructions] if isinstance(instructions, str) and instructions else []
    items = payload.get("input", [])
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]
    if isinstance(items, list):
        leading = 0
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("type", "message") != "message"
                or item.get("role") not in {"system", "developer"}
            ):
                break
            text = _instruction_text(item.get("content"))
            if text is None:
                break
            if text:
                parts.append(text)
            leading += 1
        payload["input"] = items[leading:]
    payload["instructions"] = "\n\n".join(parts) or "You are a helpful assistant."
    include = payload.get("include")
    include = list(include) if isinstance(include, list) else []
    if "reasoning.encrypted_content" not in include:
        include.append("reasoning.encrypted_content")
    payload["include"] = include
    # Codex does not accept the Chat/Responses output token limit fields.
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        payload.pop(key, None)
    return payload


async def codex_stream_to_responses_stream(
    iterator: AsyncIterator[bytes], original_model: str = ""
) -> AsyncIterator[bytes]:
    terminal = False
    async for payload in _parse_sse_stream(iterator):
        if terminal:
            continue
        _check_error(payload, "Codex")
        event_type = payload.get("type")
        if event_type in {"response.failed", "response.cancelled"}:
            raise ValueError(f"Codex stream error: {payload}")
        if event_type in {"response.done", "response.completed", "response.incomplete"}:
            response = payload.get("response")
            if not isinstance(response, dict):
                raise ValueError("Codex terminal event has no response")
            status = response.get("status")
            if status is None or status not in {
                "completed",
                "incomplete",
                "failed",
                "cancelled",
                "queued",
                "in_progress",
            }:
                status = (
                    "incomplete" if event_type == "response.incomplete" else "completed"
                )
            if status not in {"completed", "incomplete"}:
                raise ValueError(f"Codex terminal response error: {status}")
            terminal = True
            payload = {
                **payload,
                "type": f"response.{status}",
                "response": {**response, "status": status},
            }
        yield _sse(payload)
    if not terminal:
        raise ValueError("Codex stream ended before a terminal event")
    yield b"data: [DONE]\n\n"
