from __future__ import annotations

from types import SimpleNamespace

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
