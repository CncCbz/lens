from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..core.auth import is_sensitive_credential_name
from ..core.config import Settings
from ..core.url_utils import normalize_base_url, append_url_path
from ..models import ChannelConfig, ChannelProxyMode, ProtocolKind


@dataclass(frozen=True, slots=True)
class UpstreamRequest:
    method: str
    url: str
    headers: dict[str, str]
    json_body: dict[str, Any]


_OPENAI_LIKE_PATH = {
    ProtocolKind.OPENAI_CHAT: "chat/completions",
    ProtocolKind.OPENAI_RESPONSES: "responses",
    ProtocolKind.OPENAI_EMBEDDING: "embeddings",
    ProtocolKind.OPENAI_IMAGE: "images/generations",
    ProtocolKind.RERANK: "rerank",
    ProtocolKind.ANTHROPIC: "messages",
}

_OPENAI_COMPATIBLE_PROTOCOLS = frozenset(
    {
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.OPENAI_EMBEDDING,
        ProtocolKind.OPENAI_IMAGE,
        ProtocolKind.RERANK,
    }
)
_GLM_HOSTS = frozenset({"open.bigmodel.cn", "api.z.ai"})
_GLM_OPENAI_VERSIONED_PATHS = frozenset({"/api/paas/v4", "/api/coding/paas/v4"})

_INBOUND_HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "anthropic-beta",
        "anthropic-version",
        "traceparent",
        "tracestate",
        "x-app",
        "x-app-name",
        "x-app-ver",
        "x-client-app",
        "x-environment-runner-version",
        "x-goog-api-client",
        "x-request-id",
    }
)
_INBOUND_HEADER_ALLOWLIST_PREFIXES = (
    "anthropic-",
    "x-anthropic-",
    "x-claude-code-",
    "x-claude-remote-",
    "x-stainless-",
)
_SYSTEM_HEADER_NAMES = frozenset(
    {
        "authorization",
        "x-api-key",
        "x-goog-api-key",
        "host",
        "content-length",
        "content-type",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def build_upstream_request(
    channel: ChannelConfig,
    body: dict[str, Any],
    settings: Settings,
    credential_id: str | None = None,
    user_agent: str | None = None,
    forwarded_headers: Mapping[str, str] | None = None,
    model_group_headers: Iterable[Mapping[str, str]] = (),
    path_suffix: str | None = None,
) -> UpstreamRequest:
    api_key = resolve_channel_api_key(channel, credential_id=credential_id)

    if channel.protocol == ProtocolKind.GEMINI:
        model_name = str(body.get("model") or "")
        if not model_name:
            raise HTTPException(status_code=400, detail="Gemini request requires model")

        path = "streamGenerateContent" if body.get("stream") else "generateContent"
        payload = {
            key: value for key, value in body.items() if key not in {"model", "stream"}
        }
        return UpstreamRequest(
            method="POST",
            url=append_url_path(
                _protocol_base_url(channel),
                "models",
                f"{model_name}:{path}",
                query_params={"key": api_key},
            ),
            headers=build_upstream_headers(
                {"content-type": "application/json"},
                channel.headers,
                user_agent=user_agent,
                model_group_headers=model_group_headers,
                inbound_headers=forwarded_headers,
            ),
            json_body=payload,
        )

    suffix = path_suffix or _OPENAI_LIKE_PATH.get(channel.protocol)
    if suffix is None:
        raise HTTPException(
            status_code=500, detail=f"Unsupported protocol={channel.protocol.value}"
        )

    if channel.protocol == ProtocolKind.ANTHROPIC:
        default_headers = {
            "x-api-key": api_key,
            "anthropic-version": settings.anthropic_version,
            "content-type": "application/json",
        }
    else:
        default_headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }

    return UpstreamRequest(
        method="POST",
        url=append_url_path(_protocol_base_url(channel), suffix),
        headers=build_upstream_headers(
            default_headers,
            channel.headers,
            user_agent=user_agent,
            model_group_headers=model_group_headers,
            inbound_headers=forwarded_headers,
        ),
        json_body=dict(body),
    )


def build_upstream_headers(
    default_headers: dict[str, str],
    channel_headers: dict[str, str],
    user_agent: str | None = None,
    model_group_headers: Iterable[Mapping[str, str]] = (),
    inbound_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    # Merge order: inbound base -> Lens defaults -> UA -> route group -> execution group -> channel.
    headers: dict[str, str] = {}
    _merge_headers(headers, _inbound_headers_for_upstream(inbound_headers))
    _merge_headers(headers, default_headers)
    if user_agent and not any(key.lower() == "user-agent" for key in channel_headers):
        _set_header(headers, "user-agent", user_agent)
    for group_headers in model_group_headers:
        _merge_headers(headers, group_headers, protected=_SYSTEM_HEADER_NAMES)
    _merge_headers(headers, channel_headers)
    return headers


def _inbound_headers_for_upstream(
    inbound_headers: Mapping[str, str] | None,
) -> dict[str, str]:
    if not inbound_headers:
        return {}
    headers: dict[str, str] = {}
    for key, value in inbound_headers.items():
        normalized_key = key.strip().lower()
        if is_sensitive_credential_name(normalized_key) or not (
            normalized_key in _INBOUND_HEADER_ALLOWLIST
            or normalized_key.startswith(_INBOUND_HEADER_ALLOWLIST_PREFIXES)
        ):
            continue
        normalized = str(value).strip()
        if not normalized:
            continue
        headers[key] = normalized
    return headers


def _set_header(headers: dict[str, str], key: str, value: str) -> None:
    normalized_key = key.strip()
    if not normalized_key:
        return
    lower_key = normalized_key.lower()
    for existing_key in list(headers):
        if existing_key.lower() == lower_key:
            headers.pop(existing_key)
            break
    headers[normalized_key] = str(value)


def _merge_headers(
    headers: dict[str, str],
    updates: Mapping[str, str] | None,
    *,
    protected: frozenset[str] = frozenset(),
) -> None:
    if not updates:
        return
    for key, value in updates.items():
        if key.strip().lower() in protected:
            continue
        _set_header(headers, key, value)


def _protocol_base_url(channel: ChannelConfig) -> str:
    root = normalize_base_url(str(channel.base_url))
    parsed = urlsplit(root)

    if (
        channel.protocol in _OPENAI_COMPATIBLE_PROTOCOLS
        and (parsed.hostname or "") in _GLM_HOSTS
        and parsed.path.rstrip("/") in _GLM_OPENAI_VERSIONED_PATHS
    ):
        return root

    if (
        channel.protocol in _OPENAI_COMPATIBLE_PROTOCOLS
        or channel.protocol == ProtocolKind.ANTHROPIC
    ):
        return append_url_path(root, "v1")
    if channel.protocol == ProtocolKind.GEMINI:
        return append_url_path(root, "v1beta")
    return root


def resolve_channel_api_key(
    channel: ChannelConfig, credential_id: str | None = None
) -> str:
    if credential_id:
        for item in channel.keys:
            if item.id == credential_id and item.enabled and item.key.strip():
                return item.key.strip()
        raise HTTPException(
            status_code=503,
            detail=f"Credential {credential_id} is not available for channel {channel.name}",
        )

    for item in channel.keys:
        if item.enabled and item.key.strip():
            return item.key.strip()
    raise HTTPException(
        status_code=503,
        detail=f"No enabled credentials available for channel {channel.name}",
    )


def resolve_upstream_proxy_url(
    channel: ChannelConfig, global_proxy_url: str | None = None
) -> str | None:
    if channel.proxy_mode == ChannelProxyMode.DIRECT:
        return None
    if channel.proxy_mode == ChannelProxyMode.CUSTOM:
        return channel.channel_proxy.strip() or None
    global_proxy = (global_proxy_url or "").strip()
    return global_proxy or None


def resolve_channel_model_list_url(channel: ChannelConfig) -> str:
    return append_url_path(_protocol_base_url(channel), "models")
