from __future__ import annotations

import uuid
from collections.abc import Callable

from .runtime_context import (
    Any,
    AttemptLog,
    BackgroundTask,
    HTTPException,
    Mapping,
    ProtocolKind,
    RequestLogLifecycleStatus,
    Response,
    StreamingResponse,
    UpstreamRequestError,
    _RequestDeadline,
    _attempt_logs_to_dicts,
    _lens_response_headers,
    app_state,
    relay_log_capture_flags,
    asyncio,
    convert_request,
    logger,
    needs_conversion,
    perf_counter,
    settings,
)
from ..router import (
    AllTargetsCooledError,
    RouteSelection,
    RouteTarget,
)
from .auth import _gateway_key_allows_model
from .errors import (
    _apply_router_runtime_settings,
    _protocol_error_response,
)
from .upstream_http import (
    _default_lens_user_agent,
    _is_generic_user_agent,
    _normalize_user_agent,
)
from .multimodal_relay import _maybe_relay_multimodal
from .payload_serialization import _dump_log_json
from .proxy_upstream import (
    _call_channel,
    _prepare_channel_request,
    _record_target_failure,
)
from .request_logger import _RequestLogger, _update_request_log
from .runtime_context import resolve_channel_error_policy
from .routing_plan import (
    _apply_deepseek_thinking_compat,
    _apply_model_group_param_override,
    _apply_param_override,
    _elapsed_ms,
    _extract_request_reasoning_effort,
    _final_upstream_failure,
    _is_deepseek_thinking_target,
    _prepare_upstream_body,
    _resolve_routing_plan,
)
from .stream_logging import (
    _close_stream_resources,
    _record_stream_request_log_and_release_probe,
)

_RETRY_AFTER_WAIT_CAP_SECONDS = 10.0
_CAPACITY_ERRORS = {
    "concurrency": (
        "concurrency_limit",
        "All matching channels are at concurrency limit",
    ),
    "rpm": ("rpm_limit", "All matching channels are at RPM limit"),
    "usage": ("usage_limit", "All matching channels are at usage limit"),
}


def _capacity_error(reasons: set[str]) -> tuple[str, str]:
    if len(reasons) == 1:
        key = next(iter(reasons))
        if key in _CAPACITY_ERRORS:
            return _CAPACITY_ERRORS[key]
    return "channel_limit", "All matching channels are at their limit"


async def _proxy_protocol(
    protocol: ProtocolKind,
    body: dict[str, Any],
    gateway_key: GatewayApiKey,
    inbound_user_agent: str | None = None,
    inbound_headers: Mapping[str, str] | None = None,
    path_suffix: str | None = None,
    multipart_files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    request_headers: Mapping[str, str] | None = None,
) -> Response:
    started_at = perf_counter()
    request_id = uuid.uuid4().hex
    channels, runtime = await asyncio.gather(
        app_state.channel_store.list(),
        app_state.settings_repo.get_runtime_settings(),
    )
    deadline = _RequestDeadline(
        started_at,
        float(runtime["first_token_timeout_seconds"]),
        float(runtime["stream_idle_timeout_seconds"]),
    )
    _apply_router_runtime_settings(runtime)
    log_request_headers, _, log_request_body, _ = relay_log_capture_flags(runtime)
    request_content = _dump_log_json(body) if log_request_body else None
    request_headers_content = (
        _dump_log_json(dict(request_headers))
        if log_request_headers and request_headers
        else None
    )
    inbound_ua = _normalize_user_agent(inbound_user_agent)
    upstream_user_agent = (
        inbound_ua
        if inbound_ua and not _is_generic_user_agent(inbound_ua)
        else _default_lens_user_agent()
    )
    is_stream_body = bool(body.get("stream"))
    requested_model = body.get("model")
    if not isinstance(requested_model, str) or not requested_model.strip():
        request_log = await app_state.request_log_store.create_pending_request_log(
            protocol=protocol.value,
            user_agent=upstream_user_agent,
            requested_group_name=None,
            resolved_group_name=None,
            upstream_model_name=None,
            channel_id=None,
            channel_name=None,
            gateway_key_id=gateway_key.id,
            is_stream=is_stream_body,
            request_id=request_id,
            request_content=request_content,
            request_headers=request_headers_content,
        )
        await _update_request_log(
            request_log.id,
            protocol=protocol,
            requested_group_name=None,
            resolved_group_name=None,
            upstream_model_name=None,
            channel_id=None,
            channel_name=None,
            gateway_key=gateway_key,
            user_agent=upstream_user_agent,
            lifecycle_status=RequestLogLifecycleStatus.FAILED,
            status_code=400,
            success=False,
            is_stream=is_stream_body,
            first_token_latency_ms=0,
            latency_ms=_elapsed_ms(started_at),
            request_content=request_content,
            attempts=[],
            error_message="Request model is required",
        )
        return _protocol_error_response(
            protocol=protocol,
            status_code=400,
            error_type="missing_model",
            message="Request model is required",
            headers=_lens_response_headers(request_id=request_id),
            request_id=request_id,
            attempt_count=0,
            retryable=False,
        )
    requested_model = requested_model.strip()
    if not _gateway_key_allows_model(gateway_key, requested_model):
        request_log = await app_state.request_log_store.create_pending_request_log(
            protocol=protocol.value,
            user_agent=upstream_user_agent,
            requested_group_name=requested_model,
            resolved_group_name=None,
            upstream_model_name=None,
            channel_id=None,
            channel_name=None,
            gateway_key_id=gateway_key.id,
            is_stream=is_stream_body,
            request_id=request_id,
            request_content=request_content,
            request_headers=request_headers_content,
        )
        await _update_request_log(
            request_log.id,
            protocol=protocol,
            requested_group_name=requested_model,
            resolved_group_name=None,
            upstream_model_name=None,
            channel_id=None,
            channel_name=None,
            gateway_key=gateway_key,
            user_agent=upstream_user_agent,
            lifecycle_status=RequestLogLifecycleStatus.FAILED,
            status_code=403,
            success=False,
            is_stream=is_stream_body,
            first_token_latency_ms=0,
            latency_ms=_elapsed_ms(started_at),
            request_content=request_content,
            attempts=[],
            error_message="Gateway API key is not allowed to use this model",
        )
        return _protocol_error_response(
            protocol=protocol,
            status_code=403,
            error_type="forbidden_model",
            message="Gateway API key is not allowed to use this model",
            headers=_lens_response_headers(request_id=request_id),
            request_id=request_id,
            attempt_count=0,
            retryable=False,
        )
    plan: RoutingPlan | None = None
    request_log = await app_state.request_log_store.create_pending_request_log(
        protocol=protocol.value,
        user_agent=upstream_user_agent,
        requested_group_name=requested_model,
        resolved_group_name=None,
        upstream_model_name=None,
        channel_id=None,
        channel_name=None,
        gateway_key_id=gateway_key.id,
        is_stream=is_stream_body,
        request_id=request_id,
        request_content=request_content,
        request_headers=request_headers_content,
    )
    log_ctx = _RequestLogger(
        request_log_id=request_log.id,
        request_id=request_id,
        protocol=protocol,
        gateway_key=gateway_key,
        started_at=started_at,
        body=body,
        request_content=request_content,
        attempts=[],
    )
    try:
        plan, selection, routing_error = await _resolve_proxy_route(
            channels=channels,
            protocol=protocol,
            requested_model=requested_model,
            log_ctx=log_ctx,
            upstream_user_agent=upstream_user_agent,
            is_stream_body=is_stream_body,
        )
        if routing_error is not None:
            return routing_error
        if plan is None or selection is None:
            raise RuntimeError("Routing plan was not resolved")

        body = await _maybe_relay_multimodal(
            body=body,
            protocol=protocol,
            plan=plan,
            channels=channels,
            runtime=runtime,
            deadline=deadline,
            log_ctx=log_ctx,
        )
        is_stream_body = bool(body.get("stream"))

        errors: list[str] = []
        failure_status_codes: list[int | None] = []
        candidates = [selection.primary, *selection.fallbacks]
        cooled_only = True
        capacity_reasons: set[str] = set()
        unavailable_rejected = False
        for target in candidates:
            if deadline.expired():
                timeout_message = deadline.message()
                await log_ctx.update(
                    requested_group_name=plan.requested_group_name,
                    resolved_group_name=plan.resolved_group_name,
                    upstream_model_name=None,
                    channel=None,
                    user_agent=upstream_user_agent,
                    lifecycle_status=RequestLogLifecycleStatus.FAILED,
                    status_code=504,
                    success=False,
                    is_stream=is_stream_body,
                    error_message=timeout_message,
                )
                return _protocol_error_response(
                    protocol=protocol,
                    status_code=504,
                    error_type="gateway_timeout",
                    message=timeout_message,
                    headers=_response_headers_for_log(log_ctx),
                    request_id=log_ctx.request_id,
                    attempt_count=len(log_ctx.attempts),
                    retryable=False,
                )
            target_attempts = 0
            same_target_budget = 1
            while target_attempts < same_target_budget:
                release_target, capacity_reason = app_state.router.acquire_target(
                    target
                )
                if release_target is None:
                    if capacity_reason:
                        capacity_reasons.add(capacity_reason)
                    else:
                        unavailable_rejected = True
                    break
                cooled_only = False
                try:
                    response = await _try_target(
                        target=target,
                        protocol=protocol,
                        body=body,
                        runtime=runtime,
                        upstream_user_agent=upstream_user_agent,
                        inbound_headers=inbound_headers,
                        plan=plan,
                        log_ctx=log_ctx,
                        errors=errors,
                        failure_status_codes=failure_status_codes,
                        deadline=deadline,
                        route_release=release_target,
                        path_suffix=path_suffix,
                        multipart_files=multipart_files,
                    )
                except asyncio.CancelledError:
                    release_target()
                    app_state.router.release_probe(target)
                    raise
                except Exception:
                    release_target()
                    app_state.router.release_probe(target)
                    raise
                target_attempts += 1
                if response is not None:
                    if not isinstance(response, StreamingResponse):
                        release_target()
                        app_state.router.release_probe(target)
                    return response
                release_target()
                app_state.router.release_probe(target)
                policy = _policy_from_last_attempt(log_ctx, runtime)
                if policy is not None:
                    same_target_budget = max(policy.same_target_retries, 0) + 1
                if target_attempts < same_target_budget:
                    await _sleep_before_same_target_retry(log_ctx, deadline)
                    continue
                if policy is not None and not policy.fallback:
                    # Same-target budget exhausted and fallback disabled.
                    failed_status_code, failed_error_type, failed_message = (
                        _final_upstream_failure(errors, failure_status_codes)
                    )
                    return _protocol_error_response(
                        protocol=protocol,
                        status_code=failed_status_code,
                        error_type=failed_error_type,
                        message=failed_message,
                        headers=_response_headers_for_log(log_ctx),
                        request_id=log_ctx.request_id,
                        attempt_count=len(log_ctx.attempts),
                        retryable=_attempts_retryable(log_ctx),
                    )
                break

        if cooled_only and not errors:
            headers = _response_headers_for_log(log_ctx)
            if capacity_reasons and not unavailable_rejected:
                error_type, message = _capacity_error(capacity_reasons)
            elif unavailable_rejected and not capacity_reasons:
                error_type = "routing_error"
                message = "All matching channels are in cooldown"
                recovery = app_state.router.min_recovery_seconds(candidates)
                if recovery > 0:
                    headers["retry-after"] = str(recovery)
            else:
                error_type = "routing_error"
                message = "All matching channels are unavailable"
            await log_ctx.update(
                requested_group_name=plan.requested_group_name,
                resolved_group_name=plan.resolved_group_name,
                upstream_model_name=None,
                channel=None,
                user_agent=upstream_user_agent,
                lifecycle_status=RequestLogLifecycleStatus.FAILED,
                status_code=503,
                success=False,
                is_stream=is_stream_body,
                error_message=message,
            )
            return _protocol_error_response(
                protocol=protocol,
                status_code=503,
                error_type=error_type,
                message=message,
                headers=headers,
                request_id=log_ctx.request_id,
                attempt_count=len(log_ctx.attempts),
                retryable=True,
            )

        failed_status_code, failed_error_type, failed_message = _final_upstream_failure(
            errors, failure_status_codes
        )
        return _protocol_error_response(
            protocol=protocol,
            status_code=failed_status_code,
            error_type=failed_error_type,
            message=failed_message,
            headers=_response_headers_for_log(log_ctx),
            request_id=log_ctx.request_id,
            attempt_count=len(log_ctx.attempts),
            retryable=_attempts_retryable(log_ctx),
        )
    except Exception as exc:
        logger.exception("Proxy request failed unexpectedly")
        await log_ctx.update(
            requested_group_name=plan.requested_group_name if plan else requested_model,
            resolved_group_name=plan.resolved_group_name if plan else None,
            upstream_model_name=None,
            channel=None,
            user_agent=upstream_user_agent,
            lifecycle_status=RequestLogLifecycleStatus.FAILED,
            status_code=500,
            success=False,
            is_stream=is_stream_body,
            error_message=f"Unexpected proxy error: {type(exc).__name__}: {exc}",
        )
        return _protocol_error_response(
            protocol=protocol,
            status_code=500,
            error_type="server_error",
            message="Internal server error",
            headers=_response_headers_for_log(log_ctx),
            request_id=log_ctx.request_id,
            attempt_count=len(log_ctx.attempts),
            retryable=False,
        )


async def _resolve_proxy_route(
    *,
    channels: list[ChannelConfig],
    protocol: ProtocolKind,
    requested_model: str,
    log_ctx: _RequestLogger,
    upstream_user_agent: str,
    is_stream_body: bool,
) -> tuple[RoutingPlan | None, RouteSelection | None, JSONResponse | None]:
    plan: RoutingPlan | None = None
    try:
        plan = await _resolve_routing_plan(protocol, requested_model, channels)
        selection = app_state.router.select(
            channels,
            protocol,
            plan.resolved_group_name,
            strategy=plan.strategy,
            route_targets=plan.route_targets,
            use_model_matching=plan.use_model_matching,
            cursor_key=plan.cursor_key,
        )
        await log_ctx.update(
            requested_group_name=plan.requested_group_name,
            resolved_group_name=plan.resolved_group_name,
            upstream_model_name=None,
            channel=None,
            user_agent=upstream_user_agent,
            lifecycle_status=RequestLogLifecycleStatus.CONNECTING,
            status_code=None,
            success=False,
            is_stream=is_stream_body,
        )
        return plan, selection, None
    except AllTargetsCooledError as exc:
        return (
            plan,
            None,
            await _routing_error_response(
                plan=plan,
                protocol=protocol,
                requested_model=requested_model,
                log_ctx=log_ctx,
                upstream_user_agent=upstream_user_agent,
                is_stream_body=is_stream_body,
                exc=exc,
                retryable=True,
                retry_after_seconds=exc.recovery_seconds,
            ),
        )
    except LookupError as exc:
        return (
            plan,
            None,
            await _routing_error_response(
                plan=plan,
                protocol=protocol,
                requested_model=requested_model,
                log_ctx=log_ctx,
                upstream_user_agent=upstream_user_agent,
                is_stream_body=is_stream_body,
                exc=exc,
            ),
        )


async def _routing_error_response(
    *,
    plan: RoutingPlan | None,
    protocol: ProtocolKind,
    requested_model: str,
    log_ctx: _RequestLogger,
    upstream_user_agent: str,
    is_stream_body: bool,
    exc: LookupError,
    retryable: bool = False,
    retry_after_seconds: int = 0,
) -> JSONResponse:
    await log_ctx.update(
        requested_group_name=plan.requested_group_name if plan else requested_model,
        resolved_group_name=plan.resolved_group_name if plan else None,
        upstream_model_name=None,
        channel=None,
        user_agent=upstream_user_agent,
        lifecycle_status=RequestLogLifecycleStatus.FAILED,
        status_code=503,
        success=False,
        is_stream=is_stream_body,
        error_message=str(exc),
    )
    headers = _response_headers_for_log(log_ctx)
    if retryable and retry_after_seconds > 0:
        headers["retry-after"] = str(int(retry_after_seconds))
    return _protocol_error_response(
        protocol=protocol,
        status_code=503,
        error_type="routing_error",
        message="Gateway routing failed" if not retryable else str(exc),
        headers=headers,
        request_id=log_ctx.request_id,
        attempt_count=len(log_ctx.attempts),
        retryable=retryable,
    )


def _response_headers_for_log(
    log_ctx: _RequestLogger,
    *,
    final_channel_id: str | None = None,
    final_model: str | None = None,
) -> dict[str, str]:
    last_attempt = log_ctx.attempts[-1] if log_ctx.attempts else None
    return _lens_response_headers(
        request_id=log_ctx.request_id,
        attempt_count=len(log_ctx.attempts),
        final_channel_id=final_channel_id
        or (last_attempt.channel_id if last_attempt is not None else None),
        final_model=final_model
        or (last_attempt.model_name if last_attempt is not None else None),
        fallback_used=len(log_ctx.attempts) > 1,
    )


def _apply_response_headers(response: Response, headers: Mapping[str, str]) -> None:
    for name, value in headers.items():
        response.headers[name] = value


def _attempts_retryable(log_ctx: _RequestLogger) -> bool:
    return any(attempt.retryable is True for attempt in log_ctx.attempts)


def _policy_from_last_attempt(log_ctx: _RequestLogger, runtime: dict[str, Any]):
    if not log_ctx.attempts:
        return None
    attempt = log_ctx.attempts[-1]
    _key, policy = resolve_channel_error_policy(
        runtime,
        attempt.router_error_policy_config,
        policy_key=attempt.error_policy_key,
        status_code=attempt.status_code,
    )
    return policy


async def _sleep_before_same_target_retry(
    log_ctx: _RequestLogger, deadline: _RequestDeadline
) -> None:
    if not log_ctx.attempts:
        return
    retry_after = log_ctx.attempts[-1].retry_after_seconds
    if retry_after is None or retry_after <= 0:
        return
    if retry_after > _RETRY_AFTER_WAIT_CAP_SECONDS:
        return
    remaining = deadline.remaining_seconds()
    if remaining is not None and retry_after >= remaining:
        return
    await asyncio.sleep(retry_after)


def _effective_user_agent_from_headers(
    headers: Mapping[str, str], fallback: str
) -> str:
    for name, value in headers.items():
        if name.lower() == "user-agent":
            return _normalize_user_agent(value)
    return fallback


async def _try_target(
    *,
    target: RouteTarget,
    protocol: ProtocolKind,
    body: dict[str, Any],
    runtime: dict[str, Any],
    upstream_user_agent: str,
    inbound_headers: Mapping[str, str] | None,
    plan: RoutingPlan,
    log_ctx: _RequestLogger,
    errors: list[str],
    failure_status_codes: list[int | None],
    deadline: _RequestDeadline,
    route_release: Callable[[], None],
    path_suffix: str | None = None,
    multipart_files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
) -> Response | None:
    channel = target.channel
    attempt_started_at = perf_counter()
    model_group_headers: list[Mapping[str, str]] = []
    if plan.requested_group is not None and plan.requested_group.route_group_id:
        model_group_headers.append(plan.requested_group.headers)
    if plan.resolved_group is not None:
        model_group_headers.append(plan.resolved_group.headers)

    if needs_conversion(protocol, channel.protocol):
        try:
            upstream_body = convert_request(
                protocol,
                channel.protocol,
                body,
                target.model_name,
                preserve_reasoning=_is_deepseek_thinking_target(
                    channel, target.model_name
                ),
            )
        except ValueError as exc:
            return await _record_target_failure(
                target=target,
                channel=channel,
                runtime=runtime,
                log_ctx=log_ctx,
                plan=plan,
                errors=errors,
                failure_status_codes=failure_status_codes,
                attempt_started_at=attempt_started_at,
                effective_user_agent=upstream_user_agent,
                upstream_body=body,
                exc=UpstreamRequestError(
                    status_code=400,
                    detail=str(exc),
                    router_status_code=None,
                ),
            )
    else:
        upstream_body = _prepare_upstream_body(protocol, body, target.model_name)
    try:
        if plan.requested_group is not None and plan.requested_group.route_group_id:
            upstream_body = _apply_model_group_param_override(
                upstream_body,
                plan.requested_group.param_override,
                plan.requested_group.name,
            )
        if plan.resolved_group is not None:
            upstream_body = _apply_model_group_param_override(
                upstream_body,
                plan.resolved_group.param_override,
                plan.resolved_group.name,
            )
        upstream_body = _apply_param_override(channel, upstream_body)
        upstream_body = _apply_deepseek_thinking_compat(channel, upstream_body)
    except UpstreamRequestError as exc:
        return await _record_target_failure(
            target=target,
            channel=channel,
            runtime=runtime,
            log_ctx=log_ctx,
            plan=plan,
            errors=errors,
            failure_status_codes=failure_status_codes,
            attempt_started_at=attempt_started_at,
            effective_user_agent=upstream_user_agent,
            upstream_body=upstream_body,
            exc=exc,
        )
    if protocol in {ProtocolKind.OPENAI_EMBEDDING, ProtocolKind.RERANK}:
        upstream_body.pop("stream", None)

    (
        log_request_headers,
        log_response_headers,
        log_request_body,
        log_response_body,
    ) = relay_log_capture_flags(runtime)
    log_debug_enabled = bool(runtime["relay_log_debug_mode"])
    reasoning_effort = _extract_request_reasoning_effort(body, upstream_body)
    try:
        upstream, body_bytes, upstream_request_content = _prepare_channel_request(
            channel,
            upstream_body,
            credential_id=target.credential_id,
            user_agent=upstream_user_agent,
            forwarded_headers=inbound_headers,
            model_group_headers=tuple(model_group_headers),
            log_body_enabled=log_request_body,
            path_suffix=path_suffix,
            multipart_files=multipart_files,
        )
        upstream_headers_content = (
            _dump_log_json(dict(upstream.headers)) if log_request_headers else None
        )
        effective_user_agent = _effective_user_agent_from_headers(
            upstream.headers, upstream_user_agent
        )
    except UpstreamRequestError as exc:
        return await _record_target_failure(
            target=target,
            channel=channel,
            runtime=runtime,
            log_ctx=log_ctx,
            plan=plan,
            errors=errors,
            failure_status_codes=failure_status_codes,
            attempt_started_at=attempt_started_at,
            effective_user_agent=upstream_user_agent,
            upstream_body=upstream_body,
            request_content=exc.request_content,
            exc=exc,
        )
    except HTTPException as exc:
        return await _record_target_failure(
            target=target,
            channel=channel,
            runtime=runtime,
            log_ctx=log_ctx,
            plan=plan,
            errors=errors,
            failure_status_codes=failure_status_codes,
            attempt_started_at=attempt_started_at,
            effective_user_agent=upstream_user_agent,
            upstream_body=upstream_body,
            exc=UpstreamRequestError(
                status_code=exc.status_code,
                detail=exc.detail,
                router_status_code=exc.status_code,
            ),
        )
    await log_ctx.update(
        requested_group_name=plan.requested_group_name,
        resolved_group_name=plan.resolved_group_name,
        upstream_model_name=target.model_name,
        channel=channel,
        user_agent=effective_user_agent,
        lifecycle_status=RequestLogLifecycleStatus.CONNECTING,
        status_code=None,
        success=False,
        is_stream=bool(upstream_body.get("stream")),
        request_content=upstream_request_content,
        upstream_headers=upstream_headers_content,
    )
    try:
        result = await _call_channel(
            channel,
            upstream_body,
            upstream,
            body_bytes,
            upstream_request_content,
            credential_id=target.credential_id,
            probe_owner=target.probe_owner,
            model_name=target.model_name,
            pricing_group_name=plan.resolved_group_name,
            client_protocol=protocol,
            log_body_enabled=log_response_body,
            log_response_headers_enabled=log_response_headers,
            log_debug_enabled=log_debug_enabled,
            deadline=deadline,
            global_proxy_url=str(runtime["proxy_url"]),
        )
    except UpstreamRequestError as exc:
        return await _record_target_failure(
            target=target,
            channel=channel,
            runtime=runtime,
            log_ctx=log_ctx,
            plan=plan,
            errors=errors,
            failure_status_codes=failure_status_codes,
            attempt_started_at=attempt_started_at,
            effective_user_agent=effective_user_agent,
            upstream_body=upstream_body,
            request_content=upstream_request_content,
            request_url=str(upstream.url),
            request_headers=upstream_headers_content,
            exc=exc,
        )

    log_ctx.attempts.append(
        AttemptLog(
            request_id=log_ctx.request_id,
            channel_id=channel.id,
            channel_name=channel.name,
            credential_id=target.credential_id,
            credential_name=target.credential_name or "",
            model_name=target.model_name,
            status_code=result.status_code,
            success=True,
            duration_ms=_elapsed_ms(attempt_started_at),
            reasoning_effort=reasoning_effort,
            request_url=str(upstream.url),
            request_headers=upstream_headers_content,
            request_body=upstream_request_content,
            response_headers=result.upstream_response_headers,
            response_body=result.upstream_response_content,
        )
    )

    merged_request_content = result.request_content or upstream_request_content
    if result.is_stream:
        capture = result.stream_capture
        if capture is not None:
            capture.request_log_id = log_ctx.request_log_id
            capture.stream_started_at = log_ctx.started_at
            capture.concurrency_release = route_release
            capture.probe_owner = target.probe_owner
        else:
            route_release()
        first_token_latency_ms = (
            capture.first_token_latency_ms
            if capture is not None
            else result.first_token_latency_ms
        )
        try:
            await log_ctx.update(
                requested_group_name=plan.requested_group_name,
                resolved_group_name=plan.resolved_group_name,
                upstream_model_name=result.upstream_model_name,
                channel=channel,
                user_agent=effective_user_agent,
                lifecycle_status=RequestLogLifecycleStatus.STREAMING,
                status_code=result.status_code,
                success=False,
                is_stream=True,
                first_token_latency_ms=first_token_latency_ms,
                request_content=merged_request_content,
            )
        except asyncio.CancelledError:
            if capture is not None:
                await asyncio.shield(_close_stream_resources(capture))
            raise
        except Exception:
            if capture is not None:
                await _close_stream_resources(capture)
            raise
        _apply_response_headers(
            result.response,
            _response_headers_for_log(
                log_ctx,
                final_channel_id=channel.id,
                final_model=result.upstream_model_name or target.model_name,
            ),
        )
        result.response.background = BackgroundTask(
            _record_stream_request_log_and_release_probe,
            target=target,
            request_log_id=log_ctx.request_log_id,
            protocol=protocol,
            requested_group_name=plan.requested_group_name,
            resolved_group_name=plan.resolved_group_name,
            client_request_content=log_ctx.request_content,
            channel=channel,
            gateway_key=log_ctx.gateway_key,
            user_agent=effective_user_agent,
            started_at=log_ctx.started_at,
            result=result,
            attempts=_attempt_logs_to_dicts(log_ctx.attempts),
            log_debug_enabled=log_debug_enabled,
        )
        return result.response
    _apply_response_headers(
        result.response,
        _response_headers_for_log(
            log_ctx,
            final_channel_id=channel.id,
            final_model=result.upstream_model_name or target.model_name,
        ),
    )
    await log_ctx.update(
        requested_group_name=plan.requested_group_name,
        resolved_group_name=plan.resolved_group_name,
        upstream_model_name=result.upstream_model_name,
        channel=channel,
        user_agent=effective_user_agent,
        lifecycle_status=RequestLogLifecycleStatus.SUCCEEDED,
        status_code=result.status_code,
        success=True,
        is_stream=result.is_stream,
        first_token_latency_ms=result.first_token_latency_ms,
        request_content=merged_request_content,
        response_content=result.response_content,
        result=result,
    )
    return result.response
