from lens_api.core.match_overrides import (
    MatchOverrideError,
    apply_match_overrides,
    param_override_to_match_rule,
)
from lens_api.gateway.converters import convert_request, needs_conversion
from lens_api.gateway.service.routing_plan import (
    _prepare_upstream_body,
)
from lens_api.gateway.upstreams import build_upstream_headers
from lens_api.models import MatchAction, MatchOverrideRule, ProtocolKind


def _pipeline(
    *,
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    channel_id: str,
    body: dict,
    rules: list[dict],
    inbound_headers: dict[str, str] | None = None,
    channel_headers: dict[str, str] | None = None,
) -> tuple[dict, dict[str, str]]:
    if needs_conversion(client_protocol, channel_protocol):
        upstream = convert_request(
            client_protocol, channel_protocol, body, str(body.get("model") or "")
        )
    else:
        upstream = _prepare_upstream_body(
            client_protocol, body, str(body.get("model") or "")
        )
    upstream, extra = apply_match_overrides(
        rules,
        channel_id=channel_id,
        inbound_headers=inbound_headers,
        body=upstream,
    )
    headers = build_upstream_headers(
        {"authorization": "Bearer real"},
        channel_headers or {},
        extra_headers=extra,
        inbound_headers=inbound_headers,
    )
    return upstream, headers


def test_match_header_and_set_header() -> None:
    rules = [
        {
            "if": {
                "all": [
                    {"path": "channel", "op": "is", "value": "chan-a"},
                    {"path": "header.User-Agent", "op": "is", "value": "xxxx"},
                ]
            },
            "then": [{"path": "header.User-Agent", "value": "xxxxxxxx"}],
        }
    ]
    body, headers = apply_match_overrides(
        rules,
        channel_id="chan-a",
        inbound_headers={"User-Agent": "xxxx"},
        body={"model": "keep"},
    )
    assert body == {"model": "keep"}
    assert headers["User-Agent"] == "xxxxxxxx"


def test_match_body_effort_and_rewrite() -> None:
    rules = [
        {
            "if": {
                "all": [
                    {"path": "channel", "op": "is", "value": "chan-a"},
                    {"path": "body.reasoning.effort", "op": "is", "value": "max"},
                ]
            },
            "then": [{"path": "body.reasoning.effort", "value": "xhigh"}],
        }
    ]
    body, headers = apply_match_overrides(
        rules,
        channel_id="chan-a",
        inbound_headers={},
        body={"reasoning": {"effort": "max", "keep": True}},
    )
    assert body == {"reasoning": {"effort": "xhigh", "keep": True}}
    assert headers == {}


def test_is_not_matches_missing_field() -> None:
    rules = [
        {
            "if": {"path": "body.reasoning.effort", "op": "is_not", "value": "max"},
            "then": [{"path": "body.flag", "value": True}],
        }
    ]
    body, _ = apply_match_overrides(
        rules, channel_id="c", inbound_headers=None, body={}
    )
    assert body == {"flag": True}


def test_is_does_not_match_missing_field() -> None:
    rules = [
        {
            "if": {"path": "body.reasoning.effort", "op": "is", "value": "max"},
            "then": [{"path": "body.flag", "value": True}],
        }
    ]
    body, _ = apply_match_overrides(
        rules, channel_id="c", inbound_headers=None, body={}
    )
    assert body == {}


def test_any_and_later_rule_wins() -> None:
    rules = [
        {
            "if": {
                "any": [
                    {"path": "channel", "op": "is", "value": "a"},
                    {"path": "channel", "op": "is", "value": "b"},
                ]
            },
            "then": [{"path": "body.effort", "value": "one"}],
        },
        {
            "if": {"path": "channel", "op": "is", "value": "b"},
            "then": [{"path": "body.effort", "value": "two"}],
        },
    ]
    body, _ = apply_match_overrides(
        rules, channel_id="b", inbound_headers=None, body={}
    )
    assert body == {"effort": "two"}


def test_empty_all_always_matches() -> None:
    rules = [{"if": {"all": []}, "then": [{"path": "body.x", "value": 1}]}]
    body, _ = apply_match_overrides(
        rules, channel_id="c", inbound_headers=None, body={}
    )
    assert body == {"x": 1}


def test_cannot_override_model() -> None:
    rules = [{"if": {"all": []}, "then": [{"path": "body.model", "value": "other"}]}]
    try:
        apply_match_overrides(rules, channel_id="c", inbound_headers=None, body={})
    except MatchOverrideError as exc:
        assert "model cannot be overridden" in str(exc)
    else:
        raise AssertionError("model override should be rejected")


def test_creates_intermediate_body_objects() -> None:
    rules = [
        {
            "if": {"all": []},
            "then": [{"path": "body.reasoning.effort", "value": "xhigh"}],
        }
    ]
    body, _ = apply_match_overrides(
        rules, channel_id="c", inbound_headers=None, body={}
    )
    assert body == {"reasoning": {"effort": "xhigh"}}


def test_rule_model_roundtrip() -> None:
    rule = MatchOverrideRule.model_validate(
        {
            "if": {
                "all": [
                    {"path": "channel", "op": "is", "value": "chan-a"},
                    {"path": "body.reasoning.effort", "op": "is", "value": "max"},
                ]
            },
            "then": [{"path": "body.reasoning.effort", "value": "xhigh"}],
        }
    )
    dumped = rule.model_dump()
    assert dumped["if"]["all"][0]["path"] == "channel"
    assert dumped["then"][0]["value"] == "xhigh"


def test_match_headers_win_over_channel() -> None:
    result = build_upstream_headers(
        {"authorization": "Bearer real"},
        {"User-Agent": "channel"},
        extra_headers={"User-Agent": "matched"},
    )
    assert result["User-Agent"] == "matched"
    assert result["authorization"] == "Bearer real"


_CHAT_MAX = {
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": "hi"}],
    "reasoning_effort": "max",
}
_RESPONSES_MAX = {
    "model": "deepseek-v4-flash",
    "input": "hi",
    "reasoning": {"effort": "max"},
}
_REWRITE_CHAT = [
    {
        "if": {
            "all": [
                {"path": "channel", "op": "is", "value": "chan-b"},
                {"path": "body.reasoning_effort", "op": "is", "value": "max"},
            ]
        },
        "then": [{"path": "body.reasoning_effort", "value": "xhigh"}],
    }
]
_REWRITE_RESPONSES = [
    {
        "if": {
            "all": [
                {"path": "channel", "op": "is", "value": "chan-b"},
                {"path": "body.reasoning.effort", "op": "is", "value": "max"},
            ]
        },
        "then": [{"path": "body.reasoning.effort", "value": "xhigh"}],
    }
]


def test_mock_chat_channel_rewrites_max_to_xhigh() -> None:
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-b",
        body=_CHAT_MAX,
        rules=_REWRITE_CHAT,
    )
    assert body["reasoning_effort"] == "xhigh"
    assert body["model"] == "deepseek-v4-flash"


def test_mock_chat_channel_a_keeps_max() -> None:
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-a",
        body=_CHAT_MAX,
        rules=_REWRITE_CHAT,
    )
    assert body["reasoning_effort"] == "max"


def test_mock_chat_client_to_responses_channel() -> None:
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_RESPONSES,
        channel_id="chan-b",
        body=_CHAT_MAX,
        rules=_REWRITE_RESPONSES,
    )
    assert body["reasoning"]["effort"] == "xhigh"
    assert "reasoning_effort" not in body


def test_mock_responses_client_to_chat_channel() -> None:
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_RESPONSES,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-b",
        body=_RESPONSES_MAX,
        rules=_REWRITE_CHAT,
    )
    assert body["reasoning_effort"] == "xhigh"


def test_mock_force_highest_without_thinking_condition() -> None:
    rules = [
        {
            "if": {"path": "channel", "op": "is", "value": "chan-a"},
            "then": [{"path": "body.reasoning_effort", "value": "max"}],
        },
        {
            "if": {"path": "channel", "op": "is", "value": "chan-b"},
            "then": [{"path": "body.reasoning_effort", "value": "xhigh"}],
        },
    ]
    low = {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "low",
    }
    a, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-a",
        body=low,
        rules=rules,
    )
    b, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-b",
        body=low,
        rules=rules,
    )
    assert a["reasoning_effort"] == "max"
    assert b["reasoning_effort"] == "xhigh"


def test_mock_user_agent_rewrite() -> None:
    rules = [
        {
            "if": {
                "all": [
                    {"path": "channel", "op": "is", "value": "chan-a"},
                    {"path": "header.User-Agent", "op": "is", "value": "xxxx"},
                ]
            },
            "then": [{"path": "header.User-Agent", "value": "xxxxxxxx"}],
        }
    ]
    _, headers = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-a",
        body={
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
        },
        rules=rules,
        inbound_headers={"user-agent": "xxxx"},
        channel_headers={"User-Agent": "channel"},
    )
    assert headers["User-Agent"] == "xxxxxxxx"


def test_mock_authorization_not_overridden() -> None:
    rules = [
        {
            "if": {"all": []},
            "then": [{"path": "header.Authorization", "value": "Bearer stolen"}],
        }
    ]
    _, headers = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-a",
        body={
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
        },
        rules=rules,
    )
    assert headers["authorization"] == "Bearer real"


def test_param_override_flattens_to_unconditional_rule() -> None:
    rule = param_override_to_match_rule(
        {"temperature": 0.2, "options": {"keep": True, "value": 2}, "model": "nope"}
    )
    assert rule == {
        "if": {"all": []},
        "then": [
            {"path": "body.temperature", "value": 0.2},
            {"path": "body.options.keep", "value": True},
            {"path": "body.options.value", "value": 2},
        ],
    }


def test_legacy_param_override_absorbed_on_create() -> None:
    from lens_api.models import ModelGroupCreate

    created = ModelGroupCreate.model_validate(
        {
            "name": "flash",
            "protocols": ["openai_chat"],
            "param_override": {"temperature": 0.2},
            "match_overrides": _REWRITE_CHAT,
        }
    )
    dumped = [rule.model_dump() for rule in created.match_overrides]
    assert dumped[0]["if"] == {"all": []}
    assert dumped[0]["then"] == [{"path": "body.temperature", "value": 0.2}]
    assert dumped[1]["then"][0]["value"] == "xhigh"


def test_legacy_headers_absorbed_on_create() -> None:
    from lens_api.models import ModelGroupCreate

    created = ModelGroupCreate.model_validate(
        {
            "name": "flash",
            "protocols": ["openai_chat"],
            "headers": {"X-Foo": "bar"},
            "match_overrides": _REWRITE_CHAT,
        }
    )
    dumped = [rule.model_dump() for rule in created.match_overrides]
    assert dumped[0]["then"] == [{"path": "header.X-Foo", "value": "bar"}]
    assert dumped[1]["then"][0]["value"] == "xhigh"


def test_action_model_rejected_on_validate() -> None:
    try:
        MatchAction.model_validate({"path": "body.model", "value": "other"})
    except Exception as exc:
        assert "model cannot be overridden" in str(exc)
    else:
        raise AssertionError("model override should be rejected")
