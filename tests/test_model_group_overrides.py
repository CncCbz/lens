from lens_api.gateway.service.routing_plan import (
    _apply_model_group_param_override,
)
from lens_api.gateway.upstreams import build_upstream_headers


def test_model_group_param_override_deep_merges_without_replacing_model() -> None:
    body = {"model": "target-model", "options": {"keep": True, "value": 1}}
    result = _apply_model_group_param_override(
        body,
        {"options": {"value": 2, "added": "yes"}},
        "execution",
    )
    assert result == {
        "model": "target-model",
        "options": {"keep": True, "value": 2, "added": "yes"},
    }


def test_route_execution_channel_param_priority_and_deep_merge() -> None:
    body = {"model": "target-model", "options": {"route": False, "keep": True}}
    body = _apply_model_group_param_override(
        body, {"options": {"value": "route"}}, "route"
    )
    body = _apply_model_group_param_override(
        body, {"options": {"value": "execution"}}, "execution"
    )
    channel_override = {"options": {"value": "channel"}}
    from lens_api.gateway.service.routing_plan import _deep_merge_json_objects

    result = _deep_merge_json_objects(body, channel_override)
    assert result == {
        "model": "target-model",
        "options": {"route": False, "keep": True, "value": "channel"},
    }


def test_model_group_param_override_rejects_model() -> None:
    try:
        _apply_model_group_param_override(
            {"model": "target-model"}, {"model": "other-model"}, "route"
        )
    except Exception as exc:
        assert "model cannot be overridden" in str(exc)
    else:
        raise AssertionError("model override should be rejected")


def test_model_group_headers_merge_case_insensitively_in_priority_order() -> None:
    result = build_upstream_headers(
        {
            "authorization": "Bearer real",
            "content-type": "application/json",
            "x-api-key": "real-key",
        },
        {"X-Shared": "channel", "X-Channel": "1"},
        model_group_headers=(
            {
                "x-shared": "route",
                "X-Route": "1",
                "Authorization": "Bearer ignored",
                "X-API-Key": "ignored",
                "Host": "ignored",
                "Content-Length": "1",
                "Content-Type": "text/plain",
            },
            {"X-SHARED": "execution", "X-Execution": "1"},
        ),
    )
    assert result["X-Shared"] == "channel"
    assert result["X-Channel"] == "1"
    assert result["X-Route"] == "1"
    assert result["X-Execution"] == "1"
    assert result["authorization"] == "Bearer real"
    assert result["x-api-key"] == "real-key"
    assert result["content-type"] == "application/json"
    assert "host" not in {key.lower() for key in result}
    assert "content-length" not in {key.lower() for key in result}
