from __future__ import annotations

from lens_api.gateway.router.gateway_router import GatewayRouter, RouteTarget
from lens_api.models import ChannelConfig, ProtocolKind, RoutingStrategy


def _channel(channel_id: str) -> ChannelConfig:
    return ChannelConfig(
        id=channel_id,
        name=channel_id,
        protocol=ProtocolKind.OPENAI_CHAT,
        base_url="https://example.com",
        api_key="sk-test",
    )


def test_round_robin_equal_weights_distribute_evenly() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b"), _channel("c")]
    targets = [
        RouteTarget(channel=channel, model_name="model-x", priority=0, weight=1)
        for channel in channels
    ]
    counts = {channel_id: 0 for channel_id in ("a", "b", "c")}
    for _ in range(300):
        selection = router.select(
            channels,
            ProtocolKind.OPENAI_CHAT,
            "model-x",
            strategy=RoutingStrategy.ROUND_ROBIN,
            route_targets=targets,
            use_model_matching=False,
        )
        counts[selection.primary.channel.id] += 1
    values = list(counts.values())
    assert max(values) - min(values) <= 1


def test_round_robin_weighted_ratio() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b")]
    targets = [
        RouteTarget(channels[0], model_name="model-x", priority=0, weight=3),
        RouteTarget(channels[1], model_name="model-x", priority=0, weight=1),
    ]
    counts = {"a": 0, "b": 0}
    for _ in range(400):
        selection = router.select(
            channels,
            ProtocolKind.OPENAI_CHAT,
            "model-x",
            strategy=RoutingStrategy.ROUND_ROBIN,
            route_targets=targets,
            use_model_matching=False,
        )
        counts[selection.primary.channel.id] += 1
    assert counts["a"] > 2.5 * counts["b"]
    assert counts["a"] < 4 * counts["b"]


def test_round_robin_weight_floor_to_one() -> None:
    router = GatewayRouter()
    channels = [_channel("a"), _channel("b")]
    targets = [
        RouteTarget(channels[0], model_name="model-x", priority=0, weight=0),
        RouteTarget(channels[1], model_name="model-x", priority=0, weight=0),
    ]
    counts = {"a": 0, "b": 0}
    for _ in range(200):
        selection = router.select(
            channels,
            ProtocolKind.OPENAI_CHAT,
            "model-x",
            strategy=RoutingStrategy.ROUND_ROBIN,
            route_targets=targets,
            use_model_matching=False,
        )
        counts[selection.primary.channel.id] += 1
    assert counts["a"] == counts["b"] == 100
