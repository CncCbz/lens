from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from lens_api.gateway.service import multimodal_relay as relay
from lens_api.models import ModelGroupMultimodalMode, ProtocolKind

_IMAGE = {"type": "image_url", "image_url": {"url": "http://img/old.png"}}
_NEW_IMAGE = {"type": "image_url", "image_url": {"url": "http://img/new.png"}}


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        resolved_group=SimpleNamespace(multimodal=ModelGroupMultimodalMode.OFF)
    )


@pytest.mark.asyncio
async def test_relay_only_current_user_turn(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert calls == []
    assert follow_up["messages"][0]["content"] == [{"type": "text", "text": "old"}]
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
    assert calls == ["http://img/new.png"]
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

    class _FakeClient:
        async def request(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return response

        async def aclose(self) -> None:
            return None

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
    monkeypatch.setattr(
        relay, "_resolve_http_client", lambda _proxy: (_FakeClient(), True)
    )
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
            message_context="ctx",
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
