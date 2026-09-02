from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

_MISSING = object()
_MAX_DEPTH = 32


class MatchOverrideError(ValueError):
    pass


def parse_match_path(path: str) -> tuple[str, str | tuple[str, ...]]:
    raw = (path or "").strip()
    if raw == "channel":
        return "channel", ""
    if raw.startswith("header."):
        name = raw[7:].strip()
        if not name:
            raise MatchOverrideError("header path requires a name")
        return "header", name
    if raw.startswith("body."):
        rest = raw[5:]
        keys = tuple(rest.split("."))
        if not rest or any(not key for key in keys):
            raise MatchOverrideError(f"invalid body path: {path}")
        return "body", keys
    raise MatchOverrideError(f"unsupported path: {path}")


def validate_condition_path(path: str) -> str:
    parse_match_path(path)
    return path.strip()


def flatten_param_override_actions(
    override: Mapping[str, Any], *, prefix: tuple[str, ...] = ("body",)
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for key, value in override.items():
        if prefix == ("body",) and key == "model":
            continue
        path_keys = prefix + (str(key),)
        if isinstance(value, dict):
            if not value:
                continue
            actions.extend(flatten_param_override_actions(value, prefix=path_keys))
            continue
        actions.append({"path": ".".join(path_keys), "value": value})
    return actions


def param_override_to_match_rule(
    override: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not override:
        return None
    then = flatten_param_override_actions(override)
    if not then:
        return None
    return {"if": {"all": []}, "then": then}


def headers_to_match_actions(headers: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for key, value in headers.items():
        name = str(key).strip()
        if not name:
            continue
        actions.append(
            {"path": f"header.{name}", "value": "" if value is None else str(value)}
        )
    return actions


def absorb_legacy_group_overrides(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    then: list[dict[str, Any]] = []
    if "headers" in data:
        headers = data.pop("headers")
        if isinstance(headers, Mapping):
            then.extend(headers_to_match_actions(headers))
    if "param_override" in data:
        override = data.pop("param_override")
        if isinstance(override, Mapping):
            then.extend(flatten_param_override_actions(override))
    if not then:
        return data
    rule = {"if": {"all": []}, "then": then}
    existing = data.get("match_overrides")
    if existing is None:
        data["match_overrides"] = [rule]
    else:
        data["match_overrides"] = [rule, *list(existing)]
    return data


def validate_action_path(path: str) -> str:
    kind, rest = parse_match_path(path)
    if kind == "channel":
        raise MatchOverrideError("channel cannot be set")
    if kind == "body" and rest and rest[0] == "model":
        raise MatchOverrideError("model cannot be overridden")
    return path.strip()


def apply_match_overrides(
    rules: Sequence[Mapping[str, Any]],
    *,
    channel_id: str,
    inbound_headers: Mapping[str, str] | None,
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    working = deepcopy(body)
    headers: dict[str, str] = {}
    for rule in rules:
        if not isinstance(rule, Mapping):
            raise MatchOverrideError("match override rule must be an object")
        if_node = rule.get("if")
        if not _eval_node(
            if_node,
            channel_id=channel_id,
            inbound_headers=inbound_headers,
            body=working,
            depth=0,
        ):
            continue
        then = rule.get("then") or []
        if not isinstance(then, list):
            raise MatchOverrideError("then must be a list")
        for action in then:
            if not isinstance(action, Mapping):
                raise MatchOverrideError("then action must be an object")
            _apply_action(
                str(action.get("path") or ""),
                action.get("value"),
                body=working,
                headers=headers,
            )
    return working, headers


def _eval_node(
    node: Any,
    *,
    channel_id: str,
    inbound_headers: Mapping[str, str] | None,
    body: Mapping[str, Any],
    depth: int,
) -> bool:
    if depth > _MAX_DEPTH:
        raise MatchOverrideError("match condition nested too deeply")
    if not isinstance(node, Mapping):
        raise MatchOverrideError("match condition must be an object")
    if "all" in node:
        children = node.get("all")
        if not isinstance(children, list):
            raise MatchOverrideError("all must be a list")
        return all(
            _eval_node(
                child,
                channel_id=channel_id,
                inbound_headers=inbound_headers,
                body=body,
                depth=depth + 1,
            )
            for child in children
        )
    if "any" in node:
        children = node.get("any")
        if not isinstance(children, list):
            raise MatchOverrideError("any must be a list")
        return any(
            _eval_node(
                child,
                channel_id=channel_id,
                inbound_headers=inbound_headers,
                body=body,
                depth=depth + 1,
            )
            for child in children
        )
    path = node.get("path")
    op = node.get("op")
    if not isinstance(path, str) or op not in {"is", "is_not"}:
        raise MatchOverrideError("leaf condition requires path and op")
    actual = _read_path(
        path,
        channel_id=channel_id,
        inbound_headers=inbound_headers,
        body=body,
    )
    expected = node.get("value")
    matched = actual is not _MISSING and actual == expected
    if op == "is":
        return matched
    return not matched


def _read_path(
    path: str,
    *,
    channel_id: str,
    inbound_headers: Mapping[str, str] | None,
    body: Mapping[str, Any],
) -> Any:
    kind, rest = parse_match_path(path)
    if kind == "channel":
        return channel_id
    if kind == "header":
        return _header_value(inbound_headers, str(rest))
    current: Any = body
    for key in rest:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _header_value(headers: Mapping[str, str] | None, name: str) -> Any:
    if not headers:
        return _MISSING
    want = name.lower()
    for key, value in headers.items():
        if key.lower() == want:
            return value
    return _MISSING


def _apply_action(
    path: str,
    value: Any,
    *,
    body: dict[str, Any],
    headers: dict[str, str],
) -> None:
    kind, rest = parse_match_path(validate_action_path(path))
    if kind == "header":
        name = str(rest)
        _set_header(headers, name, value)
        return
    keys = rest
    current = body
    for key in keys[:-1]:
        existing = current.get(key)
        if not isinstance(existing, dict):
            existing = {}
            current[key] = existing
        current = existing
    current[keys[-1]] = deepcopy(value)


def _set_header(headers: dict[str, str], key: str, value: Any) -> None:
    normalized = key.strip()
    if not normalized:
        return
    lower = normalized.lower()
    for existing in list(headers):
        if existing.lower() == lower:
            headers.pop(existing)
            break
    headers[normalized] = "" if value is None else str(value)
