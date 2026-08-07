from __future__ import annotations

from lens_api.gateway.router.gateway_router import GatewayRouter
from lens_api.models import RouterErrorPolicy


def test_cooldown_snapshot_without_channel_configs() -> None:
    router = GatewayRouter()
    policy = RouterErrorPolicy(
        same_target_retries=0,
        fallback=True,
        cooldown_scope="channel",
        failure_threshold=1,
        cooldown_seconds=30,
        max_cooldown_seconds=60,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    )
    applied = router.record_failure(
        "cfg_openai_chat",
        "upstream failed",
        status_code=503,
        policy=policy,
    )
    assert applied >= 30

    snapshot = router.cooldown_snapshot()
    assert len(snapshot) == 1
    item = snapshot[0]
    assert item.channel_id == "cfg_openai_chat"
    assert item.cooldown_remaining_seconds > 0
    assert item.state == "open"

    router.clear_cooldown("cfg_openai_chat")
    assert router.cooldown_snapshot() == []
