import json
from copy import deepcopy

import pytest

from lens_api.core.match_overrides import (
    MatchOverrideError,
    apply_match_overrides,
    param_override_to_match_rule,
)
from lens_api.gateway.converters import convert_request, needs_conversion
from lens_api.gateway.service.proxy_upstream import _prepare_channel_request
from lens_api.gateway.service.routing_plan import (
    _extract_request_reasoning_effort,
    _prepare_upstream_body,
)
from lens_api.gateway.upstreams import build_upstream_headers
from lens_api.models import (
    ChannelConfig,
    ChannelKeyItem,
    MatchAction,
    MatchOverrideRule,
    ProtocolKind,
)


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


def _proxy_flow_match_rules(
    *,
    requested_route_group_id: str,
    requested_rules: list[dict],
    resolved_rules: list[dict],
) -> list[dict]:
    match_rules: list[dict] = []
    if requested_route_group_id:
        match_rules.extend(requested_rules)
    match_rules.extend(resolved_rules)
    return match_rules


def test_route_group_rules_apply_to_target_channel() -> None:
    rules = _proxy_flow_match_rules(
        requested_route_group_id="exec-id",
        requested_rules=_REWRITE_CHAT,
        resolved_rules=[],
    )
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-b",
        body=_CHAT_MAX,
        rules=rules,
    )
    assert body["reasoning_effort"] == "xhigh"


def test_route_group_rules_skip_other_target_channel() -> None:
    rules = _proxy_flow_match_rules(
        requested_route_group_id="exec-id",
        requested_rules=_REWRITE_CHAT,
        resolved_rules=[],
    )
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-a",
        body=_CHAT_MAX,
        rules=rules,
    )
    assert body["reasoning_effort"] == "max"


def test_execution_group_rules_win_after_route_group() -> None:
    rules = _proxy_flow_match_rules(
        requested_route_group_id="exec-id",
        requested_rules=[
            {
                "if": {"all": []},
                "then": [{"path": "body.reasoning_effort", "value": "from-route"}],
            }
        ],
        resolved_rules=[
            {
                "if": {"all": []},
                "then": [{"path": "body.reasoning_effort", "value": "from-exec"}],
            }
        ],
    )
    body, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_CHAT,
        channel_protocol=ProtocolKind.OPENAI_CHAT,
        channel_id="chan-b",
        body=_CHAT_MAX,
        rules=rules,
    )
    assert body["reasoning_effort"] == "from-exec"


def test_match_editor_items_use_route_target() -> None:
    form_items: list[dict] = []
    route_targets = [
        {
            "id": "exec-id",
            "items": [
                {
                    "channel_id": "pcfg_openai_chat",
                    "channel_name": "upstream-b",
                    "protocol": "openai_chat",
                }
            ],
        }
    ]
    route_group_id = "exec-id"
    items = (
        next(
            (
                group["items"]
                for group in route_targets
                if group["id"] == route_group_id
            ),
            [],
        )
        if route_group_id
        else form_items
    )
    assert items[0]["channel_id"] == "pcfg_openai_chat"
    assert items[0]["channel_name"] == "upstream-b"


def test_action_model_rejected_on_validate() -> None:
    try:
        MatchAction.model_validate({"path": "body.model", "value": "other"})
    except Exception as exc:
        assert "model cannot be overridden" in str(exc)
    else:
        raise AssertionError("model override should be rejected")


def test_extract_request_reasoning_effort_reads_configuration_update() -> None:
    # pi/Codex Responses clients pin the top-level reasoning.effort and carry
    # the real level in configuration_update input items.
    body = {
        "model": "m",
        "input": [
            {"type": "additional_tools", "tools": []},
            {"role": "user", "content": "hi"},
            {"type": "configuration_update", "reasoning": {"effort": "xhigh"}},
        ],
        "reasoning": {"effort": "medium", "summary": "auto"},
    }
    assert _extract_request_reasoning_effort(body) == "xhigh"

    # The last configuration_update item wins.
    body["input"].append(
        {"type": "configuration_update", "reasoning": {"effort": "low"}}
    )
    assert _extract_request_reasoning_effort(body) == "low"

    # Without configuration_update items the top-level effort is used.
    plain = {"reasoning": {"effort": "high"}, "input": "hi"}
    assert _extract_request_reasoning_effort(plain) == "high"


@pytest.fixture
def native_responses_body() -> dict:
    return {
        "model": "m",
        "reasoning": {"effort": "medium", "summary": "auto", "extra": {"keep": True}},
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
            {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
            {
                "type": "additional_tools",
                "tools": [
                    {
                        "type": "function",
                        "name": "embedded",
                        "parameters": {"type": "object"},
                    }
                ],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "native",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"type": "configuration_update", "reasoning": {"effort": "xhigh"}},
        ],
        "tools": [
            {
                "type": "function",
                "name": "native",
                "parameters": {"type": "object"},
                "strict": True,
            },
            {
                "type": "custom",
                "name": "grammar",
                "format": {
                    "type": "grammar",
                    "syntax": "lark",
                    "definition": 'start: "ok"',
                },
            },
        ],
        "include": ["reasoning.encrypted_content"],
        "text": {"format": {"type": "text"}},
        "metadata": {"keep": "value"},
        "store": False,
    }


@pytest.mark.parametrize("override", [None, "low"])
def test_native_responses_pipeline_serializes_configuration_effort(
    native_responses_body: dict, override: str | None
) -> None:
    original = deepcopy(native_responses_body)
    rules = (
        []
        if override is None
        else [
            {
                "if": {"path": "body.reasoning.effort", "op": "is", "value": "xhigh"},
                "then": [{"path": "body.reasoning.effort", "value": override}],
            }
        ]
    )
    payload, _ = _pipeline(
        client_protocol=ProtocolKind.OPENAI_RESPONSES,
        channel_protocol=ProtocolKind.OPENAI_RESPONSES,
        channel_id="responses",
        body=native_responses_body,
        rules=rules,
    )
    channel = ChannelConfig(
        id="responses",
        name="Responses",
        protocol=ProtocolKind.OPENAI_RESPONSES,
        base_url="https://provider.example",
        api_key="test-key",
        keys=[ChannelKeyItem(id="test-key", key="test-key")],
    )
    upstream, body_bytes, _ = _prepare_channel_request(
        channel,
        payload,
        credential_id=None,
        user_agent=None,
        forwarded_headers=None,
        model_group_headers=(),
        log_body_enabled=False,
    )
    expected = deepcopy(original)
    expected["reasoning"]["effort"] = override or "xhigh"
    assert json.loads(body_bytes) == expected
    assert upstream.url.endswith("/responses")
    assert native_responses_body == original
    payload["input"][1]["encrypted_content"] = "changed"
    payload["reasoning"]["extra"]["keep"] = False
    assert native_responses_body == original


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize(
    "updates, expected",
    [
        ([], "xhigh"),
        ([{"reasoning": {"effort": "low"}}], "low"),
        ([{"reasoning": {"effort": " HIGH "}}], "high"),
        (
            [
                {},
                {"reasoning": None},
                {"reasoning": {}},
                {"reasoning": {"effort": None}},
                {"reasoning": {"effort": "invalid"}},
                {"reasoning": {"effort": 123}},
            ],
            "xhigh",
        ),
        (
            [{"reasoning": {"effort": "low"}}, {"reasoning": {"effort": "invalid"}}],
            "low",
        ),
    ],
)
def test_native_responses_last_valid_configuration_effort(
    native_responses_body: dict, direct: bool, updates: list[dict], expected: str
) -> None:
    native_responses_body["input"].extend(
        {"type": "configuration_update", **update} for update in updates
    )
    original = deepcopy(native_responses_body)
    result = (
        convert_request(
            ProtocolKind.OPENAI_RESPONSES,
            ProtocolKind.OPENAI_RESPONSES,
            native_responses_body,
        )
        if direct
        else _prepare_upstream_body(
            ProtocolKind.OPENAI_RESPONSES, native_responses_body, None
        )
    )
    wanted = deepcopy(original)
    wanted["reasoning"]["effort"] = expected
    assert result == wanted
    assert result is not native_responses_body
    assert result["reasoning"] is not native_responses_body["reasoning"]
    assert native_responses_body == original


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("with_updates", [False, True])
@pytest.mark.parametrize(
    "reasoning", [None, {}, {"effort": "medium", "summary": "auto"}]
)
def test_native_responses_without_valid_updates_unchanged(
    direct: bool, with_updates: bool, reasoning: dict | None
) -> None:
    body = {
        "model": "m",
        "input": [
            {"type": "configuration_update", "reasoning": {"effort": "invalid"}},
            {"type": "configuration_update", "reasoning": {}},
        ],
    }
    if not with_updates:
        body["input"] = []
    if reasoning is not None:
        body["reasoning"] = reasoning
    original = deepcopy(body)
    result = (
        convert_request(
            ProtocolKind.OPENAI_RESPONSES, ProtocolKind.OPENAI_RESPONSES, body
        )
        if direct
        else _prepare_upstream_body(ProtocolKind.OPENAI_RESPONSES, body, None)
    )
    assert result == original
    assert body == original


def test_responses_configuration_effort_still_converts_to_chat(
    native_responses_body: dict,
) -> None:
    original = deepcopy(native_responses_body)
    result = convert_request(
        ProtocolKind.OPENAI_RESPONSES, ProtocolKind.OPENAI_CHAT, native_responses_body
    )
    assert result["reasoning_effort"] == "xhigh"
    assert native_responses_body == original
