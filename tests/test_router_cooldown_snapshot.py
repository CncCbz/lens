from __future__ import annotations

from lens_api.gateway.router.gateway_router import GatewayRouter
from lens_api.models import RouterErrorPolicy


def test_recorded_failure_does_not_open_cooldown() -> None:
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
    assert applied == 0

    snapshot = router.cooldown_snapshot()
    assert snapshot == [] or snapshot[0].cooldown_remaining_seconds == 0
