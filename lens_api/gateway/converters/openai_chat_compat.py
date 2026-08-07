from __future__ import annotations

from typing import Any

_OPENAI_CHAT_ALLOWED_ROLES = {
    "system",
    "user",
    "assistant",
    "tool",
    "latest_reminder",
}


def normalize_openai_chat_request(body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    normalized_messages: list[Any] = []
    pending_tool_calls: list[dict[str, str]] = []
    provided_tool_result_ids: set[str] = set()

    def flush_missing_tool_results() -> None:
        nonlocal pending_tool_calls, provided_tool_result_ids
        if not pending_tool_calls:
            return
        for tool_call in pending_tool_calls:
            call_id = tool_call["id"]
            if call_id in provided_tool_result_ids:
                continue
            normalized_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "No result provided",
                }
            )
        pending_tool_calls = []
        provided_tool_result_ids = set()

    for item in messages:
        if not isinstance(item, dict):
            continue
        message = dict(item)
        role = str(message.get("role") or "user")
        if role == "developer":
            role = "system"
            message["role"] = role
        elif role not in _OPENAI_CHAT_ALLOWED_ROLES:
            role = "user"
            message["role"] = role

        if role == "tool":
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                provided_tool_result_ids.add(call_id)
            normalized_messages.append(message)
            continue

        flush_missing_tool_results()
        normalized_messages.append(message)

        if role == "assistant":
            pending_tool_calls = _assistant_tool_calls(message)
            provided_tool_result_ids = set()

    flush_missing_tool_results()
    payload["messages"] = normalized_messages
    return payload


def _assistant_tool_calls(message: dict[str, Any]) -> list[dict[str, str]]:
    raw_tool_calls = message.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    tool_calls: list[dict[str, str]] = []
    for index, item in enumerate(raw_tool_calls):
        if not isinstance(item, dict):
            continue
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{index}"
            item["id"] = call_id
        tool_calls.append({"id": call_id})
    return tool_calls
