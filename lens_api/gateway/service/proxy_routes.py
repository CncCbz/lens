from __future__ import annotations

from .runtime_context import (
    Any,
    Depends,
    GatewayApiKey,
    HTTPException,
    ModelGroup,
    PiConfigExportResponse,
    ProtocolKind,
    Request,
    Response,
    UploadFile,
    app_state,
    can_reach_protocol,
    json,
)
from .auth import _gateway_key_allows_group
from .proxy_flow import _proxy_protocol
from .auth import get_current_gateway_key


def _inbound_request_headers(request: Request) -> dict[str, str]:
    return {key: value for key, value in request.headers.items()}


async def proxy_openai_chat(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    body = await request.json()
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.OPENAI_CHAT,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )


async def proxy_openai_responses(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    body = await request.json()
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.OPENAI_RESPONSES,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )


async def proxy_anthropic_messages(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    body = await request.json()
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.ANTHROPIC,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )


async def proxy_openai_embeddings(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="Embeddings request body must be a JSON object",
        )
    body.pop("stream", None)
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.OPENAI_EMBEDDING,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )


async def proxy_rerank(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="Rerank request body must be a JSON object",
        )
    body.pop("stream", None)
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.RERANK,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )


async def proxy_openai_image_generations(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    body = await request.json()
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.OPENAI_IMAGE,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        path_suffix="images/generations",
        request_headers=headers,
    )


async def proxy_openai_image_edits(
    request: Request, gateway_key: GatewayApiKey = Depends(get_current_gateway_key)
) -> Response:
    form = await request.form()
    fields: dict[str, str] = {}
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for field_name, value in form.multi_items():
        if isinstance(value, UploadFile):
            files.append(
                (
                    field_name,
                    (
                        value.filename or field_name,
                        await value.read(),
                        value.content_type or "application/octet-stream",
                    ),
                )
            )
        else:
            fields[field_name] = value
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.OPENAI_IMAGE,
        dict(fields),
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        path_suffix="images/edits",
        multipart_files=files,
        request_headers=headers,
    )


_OPENAI_LIST_PROTOCOLS: frozenset[ProtocolKind] = frozenset(
    {
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.OPENAI_EMBEDDING,
        ProtocolKind.OPENAI_IMAGE,
        ProtocolKind.RERANK,
    }
)

_ALL_MODEL_LIST_PROTOCOLS: frozenset[ProtocolKind] = frozenset(ProtocolKind)


def _filtered_group_names(
    groups: list[ModelGroup],
    gateway_key: GatewayApiKey,
    protocols: frozenset[ProtocolKind] | set[ProtocolKind],
) -> list[str]:
    group_by_id = {group.id: group for group in groups}
    requested_protocols = frozenset(protocols)

    def has_enabled_item(group: ModelGroup) -> bool:
        target = (
            group_by_id.get(group.route_group_id) if group.route_group_id else group
        )
        return bool(
            target
            and any(
                item.enabled
                and item.protocol is not None
                and any(
                    can_reach_protocol(item.protocol, protocol)
                    for protocol in requested_protocols
                )
                for item in target.items
            )
        )

    return sorted(
        {
            group.name.strip()
            for group in groups
            if group.name.strip()
            and set(group.protocols) & requested_protocols
            and has_enabled_item(group)
            and _gateway_key_allows_group(gateway_key, group)
        }
    )


def _build_openai_models_payload(
    groups: list[ModelGroup],
    gateway_key: GatewayApiKey,
    protocols: frozenset[ProtocolKind] | set[ProtocolKind] = _OPENAI_LIST_PROTOCOLS,
) -> dict[str, Any]:
    names = _filtered_group_names(groups, gateway_key, protocols)
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": 0,
                "owned_by": "lens",
            }
            for name in names
        ],
    }


def _build_anthropic_models_payload(
    groups: list[ModelGroup], gateway_key: GatewayApiKey
) -> dict[str, Any]:
    names = _filtered_group_names(
        groups, gateway_key, frozenset({ProtocolKind.ANTHROPIC})
    )
    return {
        "data": [
            {
                "id": name,
                "type": "model",
                "display_name": name,
                "created_at": "1970-01-01T00:00:00Z",
            }
            for name in names
        ],
        "first_id": names[0] if names else None,
        "last_id": names[-1] if names else None,
        "has_more": False,
    }


def _build_gemini_models_payload(
    groups: list[ModelGroup], gateway_key: GatewayApiKey
) -> dict[str, Any]:
    names = _filtered_group_names(groups, gateway_key, frozenset({ProtocolKind.GEMINI}))
    return {
        "models": [
            {
                "name": f"models/{name}",
                "baseModelId": name,
                "version": "001",
                "displayName": name,
                "supportedGenerationMethods": [
                    "generateContent",
                    "streamGenerateContent",
                ],
            }
            for name in names
        ]
    }


async def list_gateway_models(
    request: Request,
    gateway_key: GatewayApiKey = Depends(get_current_gateway_key),
) -> dict[str, Any]:
    groups = await app_state.group_repo.list_groups()
    runtime = await app_state.settings_repo.get_runtime_settings()
    if runtime["model_list_compat_mode_enabled"]:
        return _build_openai_models_payload(
            groups, gateway_key, _ALL_MODEL_LIST_PROTOCOLS
        )
    if request.headers.get("anthropic-version"):
        return _build_anthropic_models_payload(groups, gateway_key)
    return _build_openai_models_payload(groups, gateway_key)


async def export_gateway_models_config(
    type: str = "pi",
    gateway_key: GatewayApiKey = Depends(get_current_gateway_key),
) -> PiConfigExportResponse:
    from ...core.pi_catalog import collect_group_models

    if type != "pi":
        raise ValueError(f"Unsupported config type: {type}")
    groups = await app_state.group_repo.list_groups()
    entries = await app_state.pi_catalog_repo.list_all()
    runtime = await app_state.settings_repo.get_runtime_settings()
    relay_image_group_id = (
        str(runtime.get("multimodal_image_group_id") or "").strip()
        if runtime.get("multimodal_relay_enabled")
        else ""
    )
    return PiConfigExportResponse(
        type=type,
        models=collect_group_models(
            groups,
            entries,
            allow_group=lambda group: _gateway_key_allows_group(gateway_key, group),
            relay_image_group_id=relay_image_group_id,
        ),
    )


async def list_gemini_models(
    gateway_key: GatewayApiKey = Depends(get_current_gateway_key),
) -> dict[str, Any]:
    groups = await app_state.group_repo.list_groups()
    return _build_gemini_models_payload(groups, gateway_key)


async def proxy_gemini_generate_content(
    model_name: str,
    request: Request,
    gateway_key: GatewayApiKey = Depends(get_current_gateway_key),
) -> Response:
    body = await request.json()
    body = {**body, "model": model_name, "stream": False}
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.GEMINI,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )


async def proxy_gemini_stream_generate_content(
    model_name: str,
    request: Request,
    gateway_key: GatewayApiKey = Depends(get_current_gateway_key),
) -> Response:
    body = await request.json()
    body = {**body, "model": model_name, "stream": True}
    headers = _inbound_request_headers(request)
    return await _proxy_protocol(
        ProtocolKind.GEMINI,
        body,
        gateway_key,
        request.headers.get("user-agent"),
        headers,
        request_headers=headers,
    )
