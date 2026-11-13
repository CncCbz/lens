from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

from ..core.url_utils import append_url_path
from ..models import ChannelConfig, ProtocolKind


def _endpoint(base: str, suffix: str) -> str:
    if urlsplit(base).path.rstrip("/").endswith("/" + suffix.strip("/")):
        return base
    return append_url_path(base, suffix)


def _credential(value: str) -> dict[str, Any]:
    if not value.lstrip().startswith("{"):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid provider credential JSON") from None
    if not isinstance(parsed, dict):
        raise HTTPException(400, "Provider credentials must be a JSON object")
    return parsed


def provider_request(
    channel: ChannelConfig,
    body: dict[str, Any],
    api_key: str,
    path_suffix: str | None,
) -> tuple[str, dict[str, str], dict[str, Any], dict[str, str] | None] | None:
    protocol = channel.protocol
    base = str(channel.base_url).rstrip("/")
    payload = dict(body)
    headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
    signing = None
    if protocol == ProtocolKind.AZURE_OPENAI_RESPONSES:
        parsed = urlsplit(base)
        path = parsed.path.rstrip("/")
        if not path or path == "/openai":
            base = urlunsplit(parsed._replace(path="/openai/v1"))
        elif path.endswith("/openai"):
            base = append_url_path(base, "v1")
        url = _endpoint(base, "responses")
        parsed = urlsplit(url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "api-version" for key, _ in query):
            query.append(("api-version", "v1"))
        url = urlunsplit(parsed._replace(query=urlencode(query)))
        headers = {"api-key": api_key, "content-type": "application/json"}
    elif protocol == ProtocolKind.OPENAI_CODEX_RESPONSES:
        from .converters.provider_variants import normalize_codex_request

        path = urlsplit(base).path.rstrip("/")
        if path.endswith(("/codex", "/codex/responses")):
            url = _endpoint(base, "responses")
        elif path.endswith("/backend-api"):
            url = _endpoint(base, "codex/responses")
        else:
            url = _endpoint(base, "backend-api/codex/responses")
        credentials = _credential(api_key)
        token = (
            credentials.get("access_token")
            if api_key.lstrip().startswith("{")
            else api_key
        )
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(400, "Codex credentials require access_token")
        payload = normalize_codex_request(body, normalize_effort=False)
        account = next(
            (
                v
                for k, v in channel.headers.items()
                if k.lower() == "chatgpt-account-id"
            ),
            "",
        )
        if not account:
            try:
                encoded = token.split(".")[1]
                claims = json.loads(
                    base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
                )
                account = claims.get("https://api.openai.com/auth", {}).get(
                    "chatgpt_account_id", ""
                )
            except (IndexError, ValueError, TypeError, AttributeError):
                raise HTTPException(
                    400,
                    "Codex credentials require a JWT account ID or chatgpt-account-id header",
                ) from None
        if not isinstance(account, str) or not account:
            raise HTTPException(400, "Codex credentials are missing chatgpt_account_id")
        headers.update(
            {
                "authorization": f"Bearer {token}",
                "chatgpt-account-id": account,
                "originator": "pi",
                "OpenAI-Beta": "responses=experimental",
                "accept": "text/event-stream",
            }
        )
    elif protocol == ProtocolKind.GOOGLE_VERTEX:
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(400, "Vertex request requires model")
        method = "streamGenerateContent" if body.get("stream") else "generateContent"
        parsed = urlsplit(base)
        path = parsed.path.rstrip("/").split("/models/", 1)[0]
        if "/projects/" in path and not re.search(
            r"/projects/[^/]+/locations/[^/]+(?:/|$)", path
        ):
            raise HTTPException(400, "Vertex endpoint requires project and location")
        if not re.search(r"/v1(?:beta\d*)?(?:/|$)", path):
            resource = re.search(r"/(?:projects|publishers)/", path)
            position = resource.start() if resource else len(path)
            path = path[:position] + "/v1" + path[position:]
        if "/publishers/" not in path:
            path += "/publishers/google"
        base = urlunsplit(parsed._replace(path=path))
        credentials = _credential(api_key)
        token = credentials.get("access_token")
        query = {"alt": "sse"} if body.get("stream") else {}
        headers = {"content-type": "application/json"}
        if "access_token" in credentials:
            if not isinstance(token, str) or not token.strip():
                raise HTTPException(
                    400, "Vertex credentials require a valid access_token"
                )
            headers["authorization"] = f"Bearer {token}"
        else:
            key = (
                credentials.get("api_key")
                if api_key.lstrip().startswith("{")
                else api_key
            )
            if not isinstance(key, str) or not key.strip():
                raise HTTPException(
                    400, "Vertex credentials require access_token or api_key"
                )
            query["key"] = key
        url = append_url_path(base, "models", f"{quote(model, safe='-._~')}:{method}")
        parsed = urlsplit(url)
        existing_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key not in {"key", "alt"}
        ]
        url = urlunsplit(
            parsed._replace(query=urlencode([*existing_query, *query.items()]))
        )
        payload = {k: v for k, v in payload.items() if k not in {"model", "stream"}}
    elif protocol == ProtocolKind.BEDROCK_CONVERSE_STREAM:
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(400, "Bedrock request requires model")
        operation = "converse-stream" if body.get("stream") else "converse"
        parsed = urlsplit(base)
        base_path = parsed.path.split("/model/", 1)[0].rstrip("/")
        base = urlunsplit(parsed._replace(path=base_path))
        url = append_url_path(base, "model", quote(model, safe="-._~"), operation)
        credentials = _credential(api_key)
        access = credentials.get("access_key_id") or credentials.get("AccessKeyId")
        if access:
            if not isinstance(access, str) or not access.strip():
                raise HTTPException(
                    400, "Bedrock AWS credentials require access_key_id"
                )
            secret = credentials.get("secret_access_key") or credentials.get(
                "SecretAccessKey"
            )
            if not isinstance(secret, str) or not secret:
                raise HTTPException(
                    400, "Bedrock AWS credentials require secret_access_key"
                )
            region = credentials.get("region")
            if not region:
                match = re.search(
                    r"bedrock-runtime[.-]([a-z0-9-]+)\.amazonaws\.com",
                    parsed.hostname or "",
                )
                region = match.group(1) if match else None
            if not isinstance(region, str) or not region:
                raise HTTPException(
                    400, "Bedrock AWS credentials require region for a custom endpoint"
                )
            signing = {
                "access_key_id": str(access),
                "secret_access_key": secret,
                "region": region,
            }
            session = credentials.get("session_token") or credentials.get(
                "SessionToken"
            )
            if session:
                if not isinstance(session, str):
                    raise HTTPException(400, "Bedrock session_token must be a string")
                signing["session_token"] = session
            headers.pop("authorization")
        elif credentials or api_key.lstrip().startswith("{"):
            token = credentials.get("bearer_token") or credentials.get("access_token")
            if not isinstance(token, str) or not token:
                raise HTTPException(
                    400, "Bedrock credentials require AWS keys or bearer_token"
                )
            headers["authorization"] = f"Bearer {token}"
        headers["accept"] = (
            "application/vnd.amazon.eventstream"
            if body.get("stream")
            else "application/json"
        )
        payload = {
            k: v for k, v in payload.items() if k not in {"model", "modelId", "stream"}
        }
    elif protocol == ProtocolKind.PI_MESSAGES:
        url = _endpoint(base, "messages")
        headers["accept"] = "text/event-stream"
        payload.pop("stream", None)
    elif protocol in {
        ProtocolKind.MISTRAL_CONVERSATIONS,
        ProtocolKind.OPENROUTER_IMAGES,
    }:
        parsed = urlsplit(base)
        if not parsed.path.rstrip("/").endswith("/v1") and not parsed.path.rstrip(
            "/"
        ).endswith("/chat/completions"):
            base = append_url_path(base, "v1")
        url = _endpoint(base, "chat/completions")
    elif protocol == ProtocolKind.TYPESAFE_SYSTEM_ONE:
        parsed = urlsplit(base)
        openrouter = parsed.hostname == "openrouter.ai" or (
            parsed.hostname or ""
        ).endswith(".openrouter.ai")
        if openrouter and parsed.path.rstrip("/") == "/api":
            base = append_url_path(base, "v1")
        url = _endpoint(base, "systemone")
        if openrouter and payload.get("assets"):
            assets = payload["assets"]
            state = payload.get("state")
            if (
                not isinstance(assets, dict)
                or not isinstance(state, list)
                or not state
                or state[:-1] != list(assets)
            ):
                raise HTTPException(
                    400,
                    "OpenRouter System One requires image_url state parts; custom asset references cannot be mapped",
                )
            images = []
            for asset in assets.values():
                if (
                    not isinstance(asset, dict)
                    or set(asset) != {"type", "format", "data_base64"}
                    or asset.get("type") != "image"
                    or asset.get("format") not in {"png", "jpeg"}
                    or not isinstance(asset.get("data_base64"), str)
                ):
                    raise HTTPException(
                        400, "Unsupported OpenRouter System One image asset"
                    )
                images.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{asset['format']};base64,{asset['data_base64']}"
                        },
                    }
                )
            payload["state"] = [*images, state[-1]]
            payload.pop("assets")
        elif payload.get("assets"):
            headers["TypeSafe-Danger-Zone-Images"] = "true"
        payload.pop("stream", None)
    elif protocol == ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        url = _endpoint(base, "run")
        payload.pop("stream", None)
    elif protocol == ProtocolKind.OPENAI_DECISIONS:
        if not urlsplit(base).path.rstrip("/").endswith(("/v1", "/decisions")):
            base = append_url_path(base, "v1")
        url = _endpoint(base, "decisions")
        payload.pop("stream", None)
    elif protocol == ProtocolKind.LLAMA_CPP_CLASSIFY:
        suffix = (
            "classify"
            if body.get("_lens_classifier") is True
            else path_suffix or "completion"
        )
        if suffix.strip("/") not in {
            "tokenize",
            "apply-template",
            "completion",
            "classify",
        }:
            raise HTTPException(400, "Unsupported llama.cpp operation")
        parsed = urlsplit(base)
        path = parsed.path.rstrip("/")
        for operation in ("tokenize", "apply-template", "completion", "classify"):
            if path.endswith("/" + operation):
                path = path[: -len(operation) - 1]
                break
        if path.endswith("/v1"):
            path = path[:-3]
        url = append_url_path(urlunsplit(parsed._replace(path=path)), suffix)
        if suffix.strip("/") in {"tokenize", "apply-template"}:
            payload = {k: v for k, v in payload.items() if k != "stream"}
    else:
        return None
    if api_key.lstrip().startswith("{") and protocol not in {
        ProtocolKind.OPENAI_CODEX_RESPONSES,
        ProtocolKind.GOOGLE_VERTEX,
        ProtocolKind.BEDROCK_CONVERSE_STREAM,
    }:
        raise HTTPException(400, "This provider requires a raw API token")
    return url, headers, payload, signing


def sign_upstream_headers(upstream: Any, body_bytes: bytes) -> dict[str, str]:
    credentials = getattr(upstream, "signing_credentials", None)
    headers = dict(upstream.headers)
    if not credentials:
        return headers
    parsed = urlsplit(upstream.url)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    date = stamp[:8]
    lowered = {k.lower(): v for k, v in headers.items()}
    lowered.update(
        {
            "host": parsed.netloc,
            "x-amz-date": stamp,
            "x-amz-content-sha256": hashlib.sha256(body_bytes).hexdigest(),
        }
    )
    if credentials.get("session_token"):
        lowered["x-amz-security-token"] = credentials["session_token"]
    selected = {
        k: " ".join(str(v).split())
        for k, v in lowered.items()
        if k in {"host", "content-type"} or k.startswith("x-amz-")
    }
    names = ";".join(sorted(selected))
    canonical_headers = "".join(f"{k}:{selected[k]}\n" for k in sorted(selected))
    query = "&".join(
        f"{key}={value}"
        for key, value in sorted(
            (quote(k, safe="-._~"), quote(v, safe="-._~"))
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        )
    )
    segments: list[str] = []
    for segment in (parsed.path or "/").split("/"):
        if segment == "..":
            if segments:
                segments.pop()
        elif segment and segment != ".":
            segments.append(segment)
    path = "/" + "/".join(segments)
    if segments and parsed.path.endswith("/"):
        path += "/"
    canonical = "\n".join(
        (
            upstream.method.upper(),
            quote(path, safe="/-._~"),
            query,
            canonical_headers,
            names,
            lowered["x-amz-content-sha256"],
        )
    )
    scope = f"{date}/{credentials['region']}/bedrock/aws4_request"
    to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            stamp,
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
    )
    signing_key = ("AWS4" + credentials["secret_access_key"]).encode()
    for value in (date, credentials["region"], "bedrock", "aws4_request"):
        signing_key = hmac.new(signing_key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    lowered["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={credentials['access_key_id']}/{scope}, SignedHeaders={names}, Signature={signature}"
    )
    return lowered
