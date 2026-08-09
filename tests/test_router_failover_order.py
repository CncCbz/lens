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


def _targets(channels: list[ChannelConfig], priorities: list[int]) -> list[RouteTarget]:
    return [
        RouteTarget(
            channel=channels[index],
            model_name="model-x",
            priority=priority,
            weight=1,
        )
        for index, priority in enumerate(priorities)
    ]


def test_failover_picks_lowest_priority() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b"), _channel("c")]
    targets = _targets(channels, [1, 0, 2])
    selection = router.select(
        channels,
        ProtocolKind.OPENAI_CHAT,
        "model-x",
        strategy=RoutingStrategy.FAILOVER,
        route_targets=targets,
        use_model_matching=False,
    )
    assert selection.primary.channel.id == "b"
    assert [target.channel.id for target in selection.fallbacks] == ["a", "c"]


def test_failover_keeps_primary_despite_health_penalty() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b")]
    targets = _targets(channels, [0, 1])
    policy = RouterErrorPolicy(
        same_target_retries=0,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=100,
        cooldown_seconds=30,
        max_cooldown_seconds=60,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    )
    # A primary keeps a failure record but stays below cooldown threshold.
    router.record_failure(
        "a",
        "upstream error",
        status_code=503,
        model_name="model-x",
        policy=policy,
    )
    selection = router.select(
        channels,
        ProtocolKind.OPENAI_CHAT,
        "model-x",
        strategy=RoutingStrategy.FAILOVER,
        route_targets=targets,
        use_model_matching=False,
    )
    assert selection.primary.channel.id == "a"
    assert [target.channel.id for target in selection.fallbacks] == ["b"]


def test_failover_switches_when_primary_in_cooldown() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b"), _channel("c")]
    targets = _targets(channels, [0, 1, 2])
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
    selection = router.select(
        channels,
        ProtocolKind.OPENAI_CHAT,
        "model-x",
        strategy=RoutingStrategy.FAILOVER,
        route_targets=targets,
        use_model_matching=False,
    )
    assert selection.primary.channel.id == "b"
