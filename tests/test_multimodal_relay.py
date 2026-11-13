from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from lens_api.gateway.service import multimodal_relay as relay
from lens_api.models import ModelGroupMultimodalMode, ProtocolKind

_IMAGE = {"type": "image_url", "image_url": {"url": "http://img/old.png"}}
_NEW_IMAGE = {"type": "image_url", "image_url": {"url": "http://img/new.png"}}


class _MemoryCache:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], str] = {}

    async def get(
        self, resource_hash: str, kind: str, helper_group_id: str
    ) -> str | None:
        return self.rows.get((resource_hash, kind, helper_group_id))

    async def put(
        self, resource_hash: str, kind: str, helper_group_id: str, text: str
    ) -> None:
        self.rows[(resource_hash, kind, helper_group_id)] = text


@pytest.fixture(autouse=True)
def relay_cache(monkeypatch: pytest.MonkeyPatch) -> _MemoryCache:
    cache = _MemoryCache()
    monkeypatch.setattr(relay.app_state, "multimodal_relay_cache_repo", cache)
    return cache


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        resolved_group=SimpleNamespace(multimodal=ModelGroupMultimodalMode.OFF)
    )


@pytest.mark.asyncio
async def test_relay_latest_media_message_after_text_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _fake_helper(**kwargs: object) -> str:
        block = kwargs["block"]
        assert isinstance(block, dict)
        url = block["image_url"]["url"]
        assert isinstance(url, str)
        calls.append(url)
        return "seen"

    monkeypatch.setattr(relay, "_call_helper_group", _fake_helper)
    runtime = {
        "multimodal_relay_enabled": True,
        "multimodal_image_group_id": "helper",
    }

    follow_up = await relay._maybe_relay_multimodal(
        body={
            "messages": [
                {"role": "user", "content": [_IMAGE, {"type": "text", "text": "old"}]},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "follow up"},
            ]
        },
        protocol=ProtocolKind.OPENAI_CHAT,
        plan=_plan(),
        channels=[],
        runtime=runtime,
        deadline=None,
    )
    assert calls == ["http://img/old.png"]
    assert follow_up["messages"][0]["content"] == [
        {"type": "text", "text": "[image: seen]"},
        {"type": "text", "text": "old"},
    ]
    assert follow_up["messages"][2]["content"] == "follow up"

    current = await relay._maybe_relay_multimodal(
        body={
            "messages": [
                {"role": "user", "content": [_IMAGE, {"type": "text", "text": "old"}]},
                {"role": "assistant", "content": "ok"},
                {
                    "role": "user",
                    "content": [_NEW_IMAGE, {"type": "text", "text": "now"}],
                },
            ]
        },
        protocol=ProtocolKind.OPENAI_CHAT,
        plan=_plan(),
        channels=[],
        runtime=runtime,
        deadline=None,
    )
    assert calls == ["http://img/old.png", "http://img/new.png"]
    assert current["messages"][0]["content"] == [{"type": "text", "text": "old"}]
    assert current["messages"][2]["content"] == [
        {"type": "text", "text": "[image: seen]"},
        {"type": "text", "text": "now"},
    ]


@pytest.mark.asyncio
async def test_relay_failure_degrades_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _failing_helper(**kwargs: object) -> str:
        raise RuntimeError("helper upstream exploded")

    monkeypatch.setattr(relay, "_call_helper_group", _failing_helper)
    runtime = {
        "multimodal_relay_enabled": True,
        "multimodal_image_group_id": "helper",
    }

    relayed = await relay._maybe_relay_multimodal(
        body={
            "messages": [
                {"role": "user", "content": [_IMAGE, {"type": "text", "text": "old"}]},
                {
                    "role": "user",
                    "content": [_NEW_IMAGE, {"type": "text", "text": "now"}],
                },
            ]
        },
        protocol=ProtocolKind.OPENAI_CHAT,
        plan=_plan(),
        channels=[],
        runtime=runtime,
        deadline=None,
    )

    # The stale image is dropped, the unrelayable current image becomes a text
    # marker carrying the reason, so the downstream model still sees the turn and
    # no empty content is left behind.
    assert relayed["messages"][0]["content"] == [{"type": "text", "text": "old"}]
    marker, text = relayed["messages"][1]["content"]
    assert text == {"type": "text", "text": "now"}
    assert marker["text"].startswith("[image: unavailable (")
    assert "helper upstream exploded" in marker["text"]


@pytest.mark.asyncio
async def test_helper_group_without_chat_protocol_still_relays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[ProtocolKind] = []

    async def _fake_plan(protocol: ProtocolKind, *_args: object) -> SimpleNamespace:
        seen.append(protocol)
        if protocol == ProtocolKind.OPENAI_CHAT:
            raise LookupError("does not support protocol=openai_chat")
        return SimpleNamespace(name="image-helper")

    monkeypatch.setattr(relay, "_resolve_routing_plan", _fake_plan)

    plan = await relay._resolve_helper_plan(
        SimpleNamespace(
            name="image-helper",
            protocols=[ProtocolKind.OPENAI_CHAT, ProtocolKind.ANTHROPIC],
        ),
        "image",
        [],
    )

    assert plan.name == "image-helper"
    assert seen == [ProtocolKind.OPENAI_CHAT, ProtocolKind.ANTHROPIC]

    # A responses-only helper group resolves directly on its own protocol.
    seen.clear()
    monkeypatch.setattr(
        relay,
        "_resolve_routing_plan",
        lambda protocol, *_args: _resolved(protocol, seen),
    )
    plan = await relay._resolve_helper_plan(
        SimpleNamespace(name="image-helper", protocols=[ProtocolKind.OPENAI_RESPONSES]),
        "image",
        [],
    )
    assert plan.name == "image-helper"
    assert seen == [ProtocolKind.OPENAI_RESPONSES]


async def _resolved(
    protocol: ProtocolKind, seen: list[ProtocolKind]
) -> SimpleNamespace:
    seen.append(protocol)
    return SimpleNamespace(name="image-helper")


@pytest.mark.asyncio
async def test_helper_failure_is_logged_as_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        500,
        headers={"content-type": "application/json"},
        content=b'{"error": "helper exploded"}',
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response))

    async def _fake_get_group(_group_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            name="image-helper", protocols=[ProtocolKind.OPENAI_CHAT]
        )

    async def _fake_plan(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            route_targets=[
                SimpleNamespace(
                    channel=SimpleNamespace(
                        id="chan",
                        name="chan",
                        protocol=ProtocolKind.OPENAI_CHAT,
                        param_override="",
                    ),
                    model_name="vision-helper",
                    credential_id="cred",
                    credential_name="cred",
                )
            ]
        )

    monkeypatch.setattr(
        relay,
        "app_state",
        SimpleNamespace(group_repo=SimpleNamespace(get_group=_fake_get_group)),
    )
    monkeypatch.setattr(relay, "_resolve_routing_plan", _fake_plan)
    monkeypatch.setattr(relay, "_resolve_http_client", lambda _proxy: (client, True))
    monkeypatch.setattr(
        relay,
        "build_upstream_request",
        lambda *_args, **_kwargs: SimpleNamespace(
            url="http://helper/v1/chat/completions",
            method="POST",
            headers={},
            json_body={},
        ),
    )
    monkeypatch.setattr(relay, "resolve_upstream_proxy_url", lambda *_args: None)

    log_ctx = SimpleNamespace(request_id="req-1", attempts=[])

    with pytest.raises(relay.UpstreamRequestError):
        await relay._call_helper_group(
            channels=[],
            helper_group_id="helper",
            kind="image",
            block=_NEW_IMAGE,
            protocol=ProtocolKind.OPENAI_CHAT,
            global_proxy_url=None,
            deadline=SimpleNamespace(remaining_seconds=lambda: 30.0),
            log_ctx=log_ctx,
        )

    assert len(log_ctx.attempts) == 1
    attempt = log_ctx.attempts[0]
    assert attempt.success is False
    assert attempt.status_code == 500
    assert attempt.relay_kind == "image"
    assert "helper exploded" in (attempt.error_message or "")
    assert attempt.request_url == "http://helper/v1/chat/completions"


_PROTOCOLS = (
    ProtocolKind.OPENAI_CHAT,
    ProtocolKind.OPENAI_RESPONSES,
    ProtocolKind.ANTHROPIC,
)


def _image_block(protocol: ProtocolKind, url: str) -> dict[str, Any]:
    if protocol == ProtocolKind.OPENAI_RESPONSES:
        return {"type": "input_image", "image_url": url}
    if protocol == ProtocolKind.ANTHROPIC:
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "image_url", "image_url": {"url": url}}


def _tool_message(
    protocol: ProtocolKind, content: list[dict[str, Any]]
) -> dict[str, Any]:
    if protocol == ProtocolKind.OPENAI_RESPONSES:
        return {"type": "function_call_output", "call_id": "call-1", "output": content}
    if protocol == ProtocolKind.ANTHROPIC:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "is_error": False,
                    "content": content,
                }
            ],
        }
    return {"role": "tool", "tool_call_id": "call-1", "content": content}


def _tool_content(protocol: ProtocolKind, message: dict[str, Any]) -> list[Any]:
    if protocol == ProtocolKind.OPENAI_RESPONSES:
        return message["output"]
    if protocol == ProtocolKind.ANTHROPIC:
        return message["content"][0]["content"]
    return message["content"]


async def _run_relay(
    body: dict[str, Any],
    protocol: ProtocolKind = ProtocolKind.OPENAI_CHAT,
    helper_group_id: str = "helper",
    deadline: Any = None,
) -> dict[str, Any]:
    return await relay._maybe_relay_multimodal(
        body=body,
        protocol=protocol,
        plan=_plan(),
        channels=[],
        runtime={
            "multimodal_relay_enabled": True,
            "multimodal_image_group_id": helper_group_id,
            "multimodal_audio_group_id": helper_group_id,
        },
        deadline=deadline,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", _PROTOCOLS)
@pytest.mark.parametrize("tail_role", ("user", "assistant", "tool"))
async def test_latest_media_message_ignores_later_text_roles(
    monkeypatch: pytest.MonkeyPatch, protocol: ProtocolKind, tail_role: str
) -> None:
    seen: list[dict[str, Any]] = []

    async def helper(**kwargs: Any) -> str:
        seen.append(kwargs["block"])
        return "C description"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    old = _image_block(protocol, "https://img/A")
    latest = _image_block(protocol, "https://img/C")
    tail = (
        _tool_message(protocol, [relay._text_block("D", protocol)])
        if tail_role == "tool"
        else {"role": tail_role, "content": "D"}
    )
    key = relay._message_container_key(protocol)
    body = {
        key: [
            {"role": "user", "content": [old, relay._text_block("A", protocol)]},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": [latest, relay._text_block("C", protocol)]},
            tail,
        ]
    }
    original = deepcopy(body)
    result = await _run_relay(body, protocol)
    assert seen == [latest]
    assert result[key][0]["content"] == [relay._text_block("A", protocol)]
    assert result[key][2]["content"][0] == relay._text_block(
        "[image: C description]", protocol
    )
    assert result[key][-1] == tail
    assert body == original


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", _PROTOCOLS)
async def test_latest_tool_images_keep_wrappers_and_replace_all_blocks(
    monkeypatch: pytest.MonkeyPatch, protocol: ProtocolKind
) -> None:
    seen: list[dict[str, Any]] = []

    async def helper(**kwargs: Any) -> str:
        seen.append(kwargs["block"])
        assert "message_context" not in kwargs
        return f"image {len(seen)}"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    old_tool = _tool_message(
        protocol,
        [
            _image_block(protocol, "https://img/old-1"),
            relay._text_block("old text", protocol),
            _image_block(protocol, "https://img/old-2"),
        ],
    )
    images = [_image_block(protocol, f"https://img/tool-{i}") for i in range(2)]
    tool = _tool_message(
        protocol, [images[0], relay._text_block("tool context", protocol), images[1]]
    )
    key = relay._message_container_key(protocol)
    body = {key: [old_tool, tool, {"role": "user", "content": "follow up"}]}
    original = deepcopy(body)
    result = await _run_relay(body, protocol)
    assert seen == images
    assert _tool_content(protocol, result[key][0]) == [
        relay._text_block("old text", protocol)
    ]
    content = _tool_content(protocol, result[key][1])
    assert content == [
        relay._text_block("[image: image 1]", protocol),
        relay._text_block("tool context", protocol),
        relay._text_block("[image: image 2]", protocol),
    ]
    expected_tool = deepcopy(tool)
    _tool_content(protocol, expected_tool)[:] = content
    assert result[key][1] == expected_tool
    assert body == original


@pytest.mark.asyncio
async def test_cache_reuses_result_and_coalesces_duplicate_resources(
    monkeypatch: pytest.MonkeyPatch, relay_cache: _MemoryCache
) -> None:
    calls: list[str] = []

    async def helper(**kwargs: Any) -> str:
        calls.append(kwargs["helper_group_id"])
        return "cached description"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    body = {"messages": [{"role": "tool", "content": [_IMAGE, _IMAGE]}]}
    first = await _run_relay(body)
    follow_up = deepcopy(body)
    follow_up["messages"].append({"role": "user", "content": "different question"})
    second = await _run_relay(follow_up)
    assert calls == ["helper"]
    assert len(relay_cache.rows) == 1
    assert first["messages"][0] == second["messages"][0]
    assert first["messages"][0]["content"] == [
        {"type": "text", "text": "[image: cached description]"},
        {"type": "text", "text": "[image: cached description]"},
    ]
    await _run_relay(body, helper_group_id="another-helper")
    assert calls == ["helper", "another-helper"]


@pytest.mark.asyncio
async def test_failed_helper_is_not_cached_and_next_request_retries(
    monkeypatch: pytest.MonkeyPatch, relay_cache: _MemoryCache
) -> None:
    count = 0

    async def helper(**_kwargs: Any) -> str:
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("temporary outage")
        return "recovered"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    body = {"messages": [{"role": "user", "content": [_IMAGE]}]}
    first = await _run_relay(body)
    assert "unavailable" in first["messages"][0]["content"][0]["text"]
    assert relay_cache.rows == {}
    second = await _run_relay(body)
    assert second["messages"][0]["content"][0]["text"] == "[image: recovered]"
    assert count == 2
    assert len(relay_cache.rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("get", "put"))
async def test_cache_database_errors_preserve_successful_relay(
    monkeypatch: pytest.MonkeyPatch,
    relay_cache: _MemoryCache,
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def unavailable(*_args: Any) -> None:
        raise SQLAlchemyError("cache offline")

    async def helper(**_kwargs: Any) -> str:
        return "still works"

    monkeypatch.setattr(relay_cache, operation, unavailable)
    monkeypatch.setattr(relay, "_call_helper_group", helper)
    result = await _run_relay({"messages": [{"role": "user", "content": [_IMAGE]}]})
    assert result["messages"][0]["content"][0]["text"] == "[image: still works]"
    assert "Multimodal relay cache" in caplog.text


@pytest.mark.parametrize("protocol", _PROTOCOLS)
def test_resource_hash_normalizes_inline_images_across_protocols(
    protocol: ProtocolKind,
) -> None:
    chat_image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aGVsbG8="},
    }
    image = _image_block(protocol, "data:image/png;base64,aGVs bG8=\n")
    if protocol == ProtocolKind.ANTHROPIC:
        image["source"] = {
            "type": "base64",
            "media_type": "image/png",
            "data": "aGVs bG8=\n",
        }
    assert relay._media_resource_hash(image, protocol) == relay._media_resource_hash(
        chat_image, ProtocolKind.OPENAI_CHAT
    )
    assert len(relay._media_resource_hash(image, protocol) or "") == 64


def test_resource_hash_separates_urls_and_audio_formats() -> None:
    audio = {
        "type": "input_audio",
        "input_audio": {"data": "aGVsbG8=", "format": "wav"},
    }
    mp3 = deepcopy(audio)
    mp3["input_audio"]["format"] = "mp3"
    assert relay._media_resource_hash(audio, ProtocolKind.OPENAI_CHAT) != (
        relay._media_resource_hash(mp3, ProtocolKind.OPENAI_CHAT)
    )
    assert relay._media_resource_hash(_IMAGE, ProtocolKind.OPENAI_CHAT) != (
        relay._media_resource_hash(_NEW_IMAGE, ProtocolKind.OPENAI_CHAT)
    )
    assert (
        relay._media_resource_hash(
            {"type": "input_image", "file_id": "file-1"}, ProtocolKind.OPENAI_RESPONSES
        )
        is None
    )


@pytest.mark.asyncio
async def test_newest_media_message_is_selected_across_media_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kinds: list[str] = []

    async def helper(**kwargs: Any) -> str:
        kinds.append(kwargs["kind"])
        return "audio transcript"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    body = {
        "messages": [
            {"role": "user", "content": [_IMAGE, {"type": "text", "text": "old"}]},
            {
                "role": "tool",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "aGVsbG8=", "format": "wav"},
                    }
                ],
            },
            {"role": "user", "content": "continue"},
        ]
    }
    result = await _run_relay(body)
    assert await _run_relay(body) == result
    assert kinds == ["audio"]
    assert result["messages"][0]["content"] == [{"type": "text", "text": "old"}]
    assert result["messages"][1]["content"] == [
        {"type": "text", "text": "[audio: audio transcript]"}
    ]


@pytest.mark.asyncio
async def test_cancelled_helper_propagates_without_caching(
    monkeypatch: pytest.MonkeyPatch, relay_cache: _MemoryCache
) -> None:
    async def cancelled(**_kwargs: Any) -> str:
        raise relay.asyncio.CancelledError

    monkeypatch.setattr(relay, "_call_helper_group", cancelled)
    with pytest.raises(relay.asyncio.CancelledError):
        await _run_relay({"messages": [{"role": "user", "content": [_IMAGE]}]})
    assert relay_cache.rows == {}


@pytest.mark.asyncio
async def test_cache_description_does_not_depend_on_original_question(
    monkeypatch: pytest.MonkeyPatch, relay_cache: _MemoryCache
) -> None:
    helper_bodies: list[dict[str, Any]] = []

    async def helper(**kwargs: Any) -> str:
        assert "message_context" not in kwargs
        helper_bodies.append(
            relay._media_request_body(
                "vision", kwargs["kind"], kwargs["block"], kwargs["protocol"]
            )
        )
        return "complete resource description"

    monkeypatch.setattr(relay, "_call_helper_group", helper)

    async def request(question: str) -> dict[str, Any]:
        return await _run_relay(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [_IMAGE, {"type": "text", "text": question}],
                    }
                ]
            }
        )

    first = await request("question one")
    second = await request("question two")
    assert len(helper_bodies) == 1
    assert first["messages"][0]["content"][0] == second["messages"][0]["content"][0]
    relay_cache.rows.clear()
    await request("question two")
    assert helper_bodies[0] == helper_bodies[1]
    assert "question one" not in str(helper_bodies)
    assert "question two" not in str(helper_bodies)


@pytest.mark.asyncio
async def test_responses_assistant_output_image_uses_output_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def helper(**kwargs: Any) -> str:
        seen.append(kwargs["block"])
        return "assistant image"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    image = {"type": "output_image", "image_url": "https://img/assistant"}
    body = {
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": "https://img/old"}],
            },
            {
                "role": "assistant",
                "content": [image, {"type": "output_text", "text": "generated"}],
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "text only",
            },
        ]
    }
    result = await _run_relay(body, ProtocolKind.OPENAI_RESPONSES)
    assert seen == [image]
    assert result["input"][1]["content"] == [
        {"type": "output_text", "text": "[image: assistant image]"},
        {"type": "output_text", "text": "generated"},
    ]
    assert relay._block_as_openai_chat(image, ProtocolKind.OPENAI_RESPONSES) == {
        "type": "image_url",
        "image_url": {"url": "https://img/assistant"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ("get", "helper", "put"))
async def test_parent_cancellation_stops_cache_or_helper_operation(
    monkeypatch: pytest.MonkeyPatch, relay_cache: _MemoryCache, phase: str
) -> None:
    started = relay.asyncio.Event()
    finished = relay.asyncio.Event()

    async def blocked(*_args: Any, **_kwargs: Any) -> Any:
        started.set()
        try:
            await relay.asyncio.Event().wait()
        finally:
            finished.set()

    async def helper(**_kwargs: Any) -> str:
        return "success"

    monkeypatch.setattr(
        relay, "_call_helper_group", blocked if phase == "helper" else helper
    )
    if phase != "helper":
        monkeypatch.setattr(relay_cache, phase, blocked)
    task = relay.asyncio.create_task(
        _run_relay({"messages": [{"role": "user", "content": [_IMAGE, _IMAGE]}]})
    )
    await relay.asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(relay.asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert relay_cache.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("get", "put"))
async def test_cache_operations_obey_remaining_request_deadline(
    monkeypatch: pytest.MonkeyPatch,
    relay_cache: _MemoryCache,
    operation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    finished = relay.asyncio.Event()

    async def blocked(*_args: Any) -> Any:
        try:
            await relay.asyncio.Event().wait()
        finally:
            finished.set()

    async def helper(**_kwargs: Any) -> str:
        return "successful description"

    monkeypatch.setattr(relay_cache, operation, blocked)
    monkeypatch.setattr(relay, "_call_helper_group", helper)
    result = await relay.asyncio.wait_for(
        _run_relay(
            {"messages": [{"role": "user", "content": [_IMAGE]}]},
            deadline=SimpleNamespace(remaining_seconds=lambda: 0.01),
        ),
        timeout=1,
    )
    assert finished.is_set()
    assert (
        result["messages"][0]["content"][0]["text"] == "[image: successful description]"
    )
    assert "TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_mixed_nested_history_keeps_empty_tool_wrappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def helper(**_kwargs: Any) -> str:
        return "new image"

    monkeypatch.setattr(relay, "_call_helper_group", helper)
    protocol = ProtocolKind.ANTHROPIC
    old = _image_block(protocol, "https://img/old")
    new = _image_block(protocol, "https://img/new")
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "empty",
                        "is_error": True,
                        "content": [old],
                    },
                    {"type": "text", "text": "keep sibling"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "text",
                        "content": [old, {"type": "text", "text": "keep nested"}, old],
                    },
                    old,
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "latest", "content": [new]},
                    new,
                ],
            },
        ]
    }
    result = await _run_relay(body, protocol)
    assert result["messages"][0]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "empty",
            "is_error": True,
            "content": "",
        },
        {"type": "text", "text": "keep sibling"},
        {
            "type": "tool_result",
            "tool_use_id": "text",
            "content": [{"type": "text", "text": "keep nested"}],
        },
    ]
    assert result["messages"][1]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "latest",
            "content": [{"type": "text", "text": "[image: new image]"}],
        },
        {"type": "text", "text": "[image: new image]"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_image", (False, True))
async def test_native_latest_audio_does_not_reactivate_older_image(
    monkeypatch: pytest.MonkeyPatch, relay_cache: _MemoryCache, supports_image: bool
) -> None:
    async def unexpected(**_kwargs: Any) -> str:
        raise AssertionError("native latest media must not call a helper")

    monkeypatch.setattr(relay, "_call_helper_group", unexpected)
    audio = {
        "type": "input_audio",
        "input_audio": {"data": "aGVsbG8=", "format": "wav"},
    }
    body = {
        "messages": [
            {"role": "user", "content": [_IMAGE, {"type": "text", "text": "old"}]},
            {"role": "tool", "content": [audio]},
        ]
    }
    group = SimpleNamespace(
        multimodal=ModelGroupMultimodalMode.MANUAL,
        multimodal_overrides={"image": supports_image, "audio": True},
    )
    result = await relay._maybe_relay_multimodal(
        body=body,
        protocol=ProtocolKind.OPENAI_CHAT,
        plan=SimpleNamespace(resolved_group=group),
        channels=[],
        runtime={
            "multimodal_relay_enabled": True,
            "multimodal_image_group_id": "helper",
        },
        deadline=None,
    )
    assert result["messages"][1] == body["messages"][1]
    assert result["messages"][0]["content"] == (
        body["messages"][0]["content"]
        if supports_image
        else [{"type": "text", "text": "old"}]
    )
    assert relay_cache.rows == {}


def test_cache_cleanup_uses_existing_hourly_scheduler() -> None:
    from lens_api.gateway.service.runtime_context import (
        CRONJOB_SPECS,
        TASK_MULTIMODAL_CACHE_PRUNE,
    )

    spec = next(
        item for item in CRONJOB_SPECS if item.id == TASK_MULTIMODAL_CACHE_PRUNE
    )
    assert spec.default_enabled is True
    assert spec.default_interval_hours == 1
