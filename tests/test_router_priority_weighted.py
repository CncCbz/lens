from __future__ import annotations

from lens_api.gateway.router.gateway_router import GatewayRouter, RouteTarget
from lens_api.models import (
    ChannelConfig,
    ProtocolKind,
    RouterErrorPolicy,
    RoutingStrategy,
)


def _channel(channel_id: str) -> ChannelConfig:
    return ChannelConfig(
        id=channel_id,
        name=channel_id,
        protocol=ProtocolKind.OPENAI_CHAT,
        base_url="https://example.com",
        api_key="sk-test",
    )


def _select(router: GatewayRouter, channels: list[ChannelConfig]):
    return router.select(
        channels,
        ProtocolKind.OPENAI_CHAT,
        "model-x",
        strategy=RoutingStrategy.PRIORITY_WEIGHTED,
        route_targets=[
            RouteTarget(channels[0], model_name="model-x", priority=0, weight=3),
            RouteTarget(channels[1], model_name="model-x", priority=0, weight=1),
            RouteTarget(channels[2], model_name="model-x", priority=1, weight=2),
            RouteTarget(channels[3], model_name="model-x", priority=1, weight=1),
        ],
        use_model_matching=False,
    )


def test_priority_weighted_only_uses_top_level() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b"), _channel("c"), _channel("d")]
    counts = {"a": 0, "b": 0, "c": 0, "d": 0}
    for _ in range(400):
        selection = _select(router, channels)
        counts[selection.primary.channel.id] += 1
    assert counts["c"] == 0
    assert counts["d"] == 0
    assert counts["a"] > 2.5 * counts["b"]
    assert counts["a"] < 4 * counts["b"]


def test_priority_weighted_keeps_top_level_after_recorded_failure() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b"), _channel("c"), _channel("d")]
    policy = RouterErrorPolicy(
        same_target_retries=0,
        fallback=True,
        cooldown_scope="channel",
        failure_threshold=1,
        cooldown_seconds=60,
        max_cooldown_seconds=60,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    )
    router.record_failure("a", "boom", status_code=503, policy=policy)
    router.record_failure("b", "boom", status_code=503, policy=policy)

    counts = {"a": 0, "b": 0, "c": 0, "d": 0}
    for _ in range(300):
        selection = _select(router, channels)
        counts[selection.primary.channel.id] += 1
    assert counts["c"] == 0
    assert counts["d"] == 0
    assert counts["a"] + counts["b"] == 300


def test_priority_weighted_fallbacks_same_level_first() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b"), _channel("c")]
    targets = [
        RouteTarget(channels[0], model_name="model-x", priority=0, weight=1),
        RouteTarget(channels[1], model_name="model-x", priority=0, weight=1),
        RouteTarget(channels[2], model_name="model-x", priority=1, weight=1),
    ]
    selection = router.select(
        channels,
        ProtocolKind.OPENAI_CHAT,
        "model-x",
        strategy=RoutingStrategy.PRIORITY_WEIGHTED,
        route_targets=targets,
        use_model_matching=False,
    )
    assert selection.primary.channel.id in ("a", "b")
    fallback_ids = [target.channel.id for target in selection.fallbacks]
    # Same-level remaining target comes before the lower level.
    assert fallback_ids[0] in ("a", "b")
    assert fallback_ids[-1] == "c"
