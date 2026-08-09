from __future__ import annotations

from .runtime_context import (
    Any,
    ChannelConfig,
    ChannelHealth,
    ChannelStatus,
    Depends,
    HTTPException,
    OverviewChannelAnalytics,
    OverviewChannelHealthPoint,
    OverviewDailyPoint,
    OverviewDimensionUsageAnalytics,
    OverviewModelAnalytics,
    OverviewPerformanceAnalytics,
    OverviewSummary,
    ProtocolKind,
    Query,
    RequestLogDetail,
    RequestLogPage,
    RequestLogSortMode,
    RequestLogStatusFilter,
    Response,
    RoutePreviewRequest,
    RoutePreviewResponse,
    RoutePreviewTarget,
    RouteTarget,
    SiteBatchImportRequest,
    SiteBatchImportResult,
    SiteConfig,
    SiteCreate,
    SiteModelFetchItem,
    SiteModelFetchRequest,
    SiteModelTestRequest,
    SiteModelTestResult,
    SiteRuntimeSummary,
    SiteUpdate,
    app_state,
)
from .upstream_http import (
    _fetch_upstream_models,
    _format_channel_error,
)
from .site_model_probe import (
    _apply_site_model_probe_param_override,
    _call_site_model_probe_channel,
    _site_model_probe_body,
    _site_model_probe_channel,
)
from .auth import get_current_admin
from .errors import _apply_router_runtime_settings
from .routing_plan import _resolve_routing_plan


async def list_sites(_: Any = Depends(get_current_admin)) -> list[SiteConfig]:
    return await app_state.channel_store.list_sites()


async def site_runtime_summaries(
    _: Any = Depends(get_current_admin),
) -> list[SiteRuntimeSummary]:
    return await app_state.request_log_store.list_site_runtime_summaries()


async def create_site(
    payload: SiteCreate, _: Any = Depends(get_current_admin)
) -> SiteConfig:
    return await app_state.channel_store.create_site(payload)


async def import_sites(
    payload: SiteBatchImportRequest, _: Any = Depends(get_current_admin)
) -> SiteBatchImportResult:
    return await app_state.channel_store.import_sites(payload)


async def update_site(
    site_id: str, payload: SiteUpdate, _: Any = Depends(get_current_admin)
) -> SiteConfig:
    return await app_state.channel_store.update_site(site_id, payload)


async def delete_site(site_id: str, _: Any = Depends(get_current_admin)) -> Response:
    await app_state.channel_store.delete_site(site_id)
    return Response(status_code=204)


async def fetch_site_models(
    payload: SiteModelFetchRequest, _: Any = Depends(get_current_admin)
) -> list[SiteModelFetchItem]:
    previews = await app_state.channel_store.fetch_models_preview(payload)
    items: list[SiteModelFetchItem] = []
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []

    for preview in previews:
        credential = next(
            (
                item
                for item in payload.credentials
                if (item.id or "") == preview["credential_id"]
            ),
            None,
        )
        if credential is None:
            continue

        channel = ChannelConfig(
            id="preview",
            name=preview["credential_name"] or "preview",
            protocol=ProtocolKind.OPENAI_CHAT,
            base_url=payload.base_url,
            api_key=credential.api_key,
            headers=payload.headers,
            model_patterns=[],
            keys=[
                {
                    "id": preview["credential_id"],
                    "key": credential.api_key,
                    "remark": preview["credential_name"],
                    "enabled": True,
                }
            ],
            models=[],
            proxy_mode=payload.proxy_mode,
            channel_proxy=payload.channel_proxy,
            param_override="",
            match_regex=payload.match_regex,
        )
        try:
            model_names = await _fetch_upstream_models(channel)
        except HTTPException as exc:
            errors.append(_format_channel_error(exc.detail))
            continue

        for model_name in model_names:
            key = (preview["credential_id"], model_name)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                SiteModelFetchItem(
                    credential_id=preview["credential_id"],
                    credential_name=preview["credential_name"],
                    model_name=model_name,
                )
            )
    if not items and errors:
        raise HTTPException(
            status_code=502,
            detail="Model discovery failed: " + "; ".join(errors),
        )
    return items


async def test_site_model(
    payload: SiteModelTestRequest, _: Any = Depends(get_current_admin)
) -> SiteModelTestResult:
    channel = _site_model_probe_channel(payload)
    body = _site_model_probe_body(payload)
    prepared_body = _apply_site_model_probe_param_override(channel, body, payload)
    if isinstance(prepared_body, SiteModelTestResult):
        return prepared_body
    return await _call_site_model_probe_channel(
        channel=channel,
        body=prepared_body,
        model_name=payload.model_name,
        credential_id=payload.credential.id,
    )


async def router_snapshot(_: Any = Depends(get_current_admin)) -> dict[str, Any]:
    channels = await app_state.channel_store.list()
    return app_state.router.snapshot(channels).model_dump(mode="json")


async def router_cooldowns(_: Any = Depends(get_current_admin)) -> dict[str, Any]:
    health = app_state.router.cooldown_snapshot()
    return {
        "health": [item.model_dump(mode="json") for item in health],
    }


async def route_preview(
    payload: RoutePreviewRequest, _: Any = Depends(get_current_admin)
) -> RoutePreviewResponse:
    requested_model = payload.model.strip()
    if not requested_model:
        return RoutePreviewResponse(
            success=False,
            protocol=payload.protocol,
            requested_group_name=payload.model,
            error_message="Model group is required",
        )

    channels = await app_state.channel_store.list()
    runtime = await app_state.settings_repo.get_runtime_settings()
    _apply_router_runtime_settings(runtime)
    try:
        plan = await _resolve_routing_plan(payload.protocol, requested_model, channels)
    except LookupError as exc:
        return RoutePreviewResponse(
            success=False,
            protocol=payload.protocol,
            requested_group_name=requested_model,
            error_message=str(exc),
        )

    selection_error = ""
    selected_targets: list[RouteTarget] = []
    try:
        selection = app_state.router.select(
            channels,
            payload.protocol,
            plan.resolved_group_name,
            strategy=plan.strategy,
            route_targets=plan.route_targets,
            use_model_matching=plan.use_model_matching,
            cursor_key=plan.cursor_key,
            mutate=False,
        )
        selected_targets = [selection.primary, *selection.fallbacks]
    except LookupError as exc:
        selection_error = str(exc)

    snapshot = app_state.router.snapshot(channels)
    health_by_channel = {item.channel_id: item for item in snapshot.health}
    selected_keys = {_route_preview_target_key(target) for target in selected_targets}
    targets = [
        _route_preview_target(
            target,
            role="primary" if index == 0 else "fallback",
            client_protocol=payload.protocol,
            health_by_channel=health_by_channel,
        )
        for index, target in enumerate(selected_targets)
    ]
    targets.extend(
        _route_preview_target(
            target,
            role="skipped",
            client_protocol=payload.protocol,
            health_by_channel=health_by_channel,
        )
        for target in plan.route_targets
        if _route_preview_target_key(target) not in selected_keys
    )

    return RoutePreviewResponse(
        success=bool(selected_targets),
        protocol=payload.protocol,
        requested_group_name=plan.requested_group_name,
        resolved_group_name=plan.resolved_group_name,
        strategy=plan.strategy,
        error_message="" if selected_targets else selection_error,
        targets=targets,
    )


async def clear_router_cooldown(
    channel_id: str, _: Any = Depends(get_current_admin)
) -> Response:
    channels = await app_state.channel_store.list()
    if not any(channel.id == channel_id for channel in channels):
        raise HTTPException(status_code=404, detail="Channel not found")
    app_state.router.clear_cooldown(channel_id)
    return Response(status_code=204)


def _route_preview_target_key(target: RouteTarget) -> tuple[str, str, str]:
    return (target.channel.id, target.credential_id or "", target.model_name or "")


def _route_preview_channel_key(target: RouteTarget) -> Any | None:
    if not target.credential_id:
        return None
    for key in target.channel.keys:
        if key.id == target.credential_id:
            return key
    return None


def _route_preview_credential_name(target: RouteTarget) -> str:
    if target.credential_name:
        return target.credential_name
    key = _route_preview_channel_key(target)
    if key is not None:
        return str(key.remark or key.id)
    return target.credential_id or ""


def _route_preview_target_state(
    target: RouteTarget, health_by_channel: dict[str, ChannelHealth]
) -> tuple[bool, str, int, str]:
    if target.channel.status != ChannelStatus.ENABLED:
        return False, "channel_disabled", 0, "disabled"
    if target.credential_id:
        key = _route_preview_channel_key(target)
        if key is None:
            return False, "credential_not_found", 0, "unavailable"
        if not key.enabled:
            return False, "credential_disabled", 0, "disabled"

    available = app_state.router.is_target_available(target)
    remaining = app_state.router.min_recovery_seconds([target])
    health = health_by_channel.get(target.channel.id)
    if not available:
        if target.credential_id and health is not None:
            key_health = next(
                (
                    item
                    for item in health.key_health
                    if item.credential_id == target.credential_id
                ),
                None,
            )
            if key_health is not None and key_health.cooldown_remaining_seconds > 0:
                return (
                    False,
                    "credential_cooldown",
                    key_health.cooldown_remaining_seconds,
                    "open",
                )
        reason = "target_cooldown" if target.model_name else "channel_cooldown"
        return False, reason, remaining, "open"
    if health is not None and health.state == "probe":
        return True, "probe", 0, "probe"
    return True, "", 0, health.state if health is not None else "available"


def _route_preview_target(
    target: RouteTarget,
    *,
    role: str,
    client_protocol: ProtocolKind,
    health_by_channel: dict[str, ChannelHealth],
) -> RoutePreviewTarget:
    available, reason, cooldown_remaining_seconds, state = _route_preview_target_state(
        target, health_by_channel
    )
    return RoutePreviewTarget(
        role=role,
        state=state,
        channel_id=target.channel.id,
        channel_name=target.channel.name,
        protocol=target.channel.protocol,
        credential_id=target.credential_id,
        credential_name=_route_preview_credential_name(target),
        model_name=target.model_name,
        priority=target.priority,
        weight=target.weight,
        available=available,
        reason=reason,
        cooldown_remaining_seconds=cooldown_remaining_seconds,
        native_protocol=target.channel.protocol == client_protocol,
    )


async def overview_summary(
    days: int = 7,
    _: Any = Depends(get_current_admin),
) -> OverviewSummary:
    return await app_state.request_log_store.get_overview_summary(
        days=days,
    )


async def overview_daily(
    days: int = 0,
    _: Any = Depends(get_current_admin),
) -> list[OverviewDailyPoint]:
    return await app_state.request_log_store.list_overview_daily(
        days=days,
    )


async def overview_models(
    days: int = 7,
    metric: str = Query(default="cost", pattern="^(cost|requests|tokens)$"),
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewModelAnalytics:
    return await app_state.request_log_store.get_model_analytics(
        days=days,
        metric=metric,
        gateway_key_id=gateway_key_id,
    )


async def overview_channels(
    days: int = 7,
    metric: str = Query(default="cost", pattern="^(cost|requests|tokens)$"),
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewChannelAnalytics:
    return await app_state.request_log_store.get_channel_analytics(
        days=days,
        metric=metric,
        gateway_key_id=gateway_key_id,
    )


async def overview_channel_health(
    days: int = 7,
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> list[OverviewChannelHealthPoint]:
    return await app_state.request_log_store.get_channel_health(
        days=days,
        gateway_key_id=gateway_key_id,
    )


async def overview_usage_channels(
    days: int = 7,
    metric: str = Query(default="cost", pattern="^(cost|requests|tokens)$"),
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewDimensionUsageAnalytics:
    return await app_state.request_log_store.get_dimension_usage(
        "channel",
        days=days,
        metric=metric,
        gateway_key_id=gateway_key_id,
    )


async def overview_usage_models(
    days: int = 7,
    metric: str = Query(default="cost", pattern="^(cost|requests|tokens)$"),
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewDimensionUsageAnalytics:
    return await app_state.request_log_store.get_dimension_usage(
        "model",
        days=days,
        metric=metric,
        gateway_key_id=gateway_key_id,
    )


async def overview_usage_gateway_keys(
    days: int = 7,
    metric: str = Query(default="cost", pattern="^(cost|requests|tokens)$"),
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewDimensionUsageAnalytics:
    return await app_state.request_log_store.get_dimension_usage(
        "gateway_key",
        days=days,
        metric=metric,
        gateway_key_id=gateway_key_id,
    )


async def overview_performance_channels(
    days: int = 7,
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewPerformanceAnalytics:
    return await app_state.request_log_store.get_performance_analytics(
        "channel",
        days=days,
        gateway_key_id=gateway_key_id,
    )


async def overview_performance_models(
    days: int = 7,
    gateway_key_id: str | None = None,
    _: Any = Depends(get_current_admin),
) -> OverviewPerformanceAnalytics:
    return await app_state.request_log_store.get_performance_analytics(
        "model",
        days=days,
        gateway_key_id=gateway_key_id,
    )


async def request_log_page(
    limit: int = 100,
    offset: int = 0,
    gateway_key_id: str | None = None,
    model_prefix: str | None = None,
    status_filter: RequestLogStatusFilter | None = Query(default=None, alias="status"),
    protocol: ProtocolKind | None = None,
    channel: str | None = None,
    keyword: str | None = None,
    sort: RequestLogSortMode = RequestLogSortMode.LATEST,
    _: Any = Depends(get_current_admin),
) -> RequestLogPage:
    return await app_state.request_log_store.list_request_log_page(
        limit=limit,
        offset=offset,
        gateway_key_id=gateway_key_id,
        model_prefix=model_prefix,
        status_filter=status_filter,
        protocol=protocol,
        channel=channel,
        keyword=keyword,
        sort=sort,
    )


async def clear_request_logs(_: Any = Depends(get_current_admin)) -> Response:
    await app_state.request_log_store.clear_request_logs()
    return Response(status_code=204)


async def request_log_detail(
    log_id: int, _: Any = Depends(get_current_admin)
) -> RequestLogDetail:
    return await app_state.request_log_store.get_request_log(log_id)
