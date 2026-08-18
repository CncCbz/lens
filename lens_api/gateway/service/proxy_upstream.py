from __future__ import annotations

from starlette.background import BackgroundTask
from starlette.responses import ContentStream
from starlette.types import Receive, Scope, Send

from .runtime_context import (
    AttemptLog,
    Any,
    AsyncIterator,
    ChannelConfig,
    Mapping,
    ProtocolKind,
    RequestLogLifecycleStatus,
    Response,
    RouteTarget,
    RoutingPlan,
    StreamCapture,
    StreamingResponse,
    UpstreamRequestError,
    UpstreamResult,
    _RequestDeadline,
    _lens_response_headers,
    app_state,
    asyncio,
    build_upstream_request,
    convert_response,
    convert_stream_iterator,
    resolve_channel_error_policy,
    httpx,
    logger,
    needs_conversion,
    perf_counter,
    resolve_upstream_proxy_url,
    settings,
)
from ..router import (
    classify_error,
    decide_route_error,
    decision_from_policy,
    parse_retry_after_seconds,
    policy_key_for_status,
)
from .errors import _protocol_error_response
from .upstream_http import (
    _format_channel_error,
    _format_http_response_error,
    _format_transport_error,
    _passthrough_headers,
    _resolve_http_client,
)
from .payload_serialization import (
    _decode_content_bytes,
    _decode_log_content_bytes,
    _dump_log_json,
    _sanitize_log_content_text,
    _json_body_bytes,
)
from .request_logger import _RequestLogger
from .stream_logging import (
    _cancel_stream_capture,
    _capture_converted_stream_iterator,
    _close_stream_resources,
    _release_stream_concurrency,
    _safe_estimate_cost,
    _stream_upstream_iterator,
)
from .stream_restore import _distill_stream_response_content
from .usage import _extract_response_usage, _extract_stream_usage
from .routing_plan import (
    _deadline_scope,
    _elapsed_ms,
    _extract_request_reasoning_effort,
    _is_request_too_large_error,
    _request_body_too_large_message,
)


async def _build_sse_to_json_result(
    response: httpx.Response,
    channel: ChannelConfig,
    client_protocol: ProtocolKind | None,
    original_model: str,
    pricing_group_name: str | None,
    request_content: str | None,
    log_body_enabled: bool,
) -> UpstreamResult:
    content = await response.aread()
    raw_content = _decode_content_bytes(content)
    try:
        parsed = _extract_stream_usage(channel.protocol, raw_content)
    except ValueError as exc:
        raise UpstreamRequestError(
            status_code=502,
            detail=f"Invalid upstream usage: {exc}",
            router_status_code=502,
        ) from exc
    try:
        distilled_content = _distill_stream_response_content(
            channel.protocol, raw_content
        )
    except ValueError as exc:
        raise UpstreamRequestError(
            status_code=502,
            detail=f"Invalid upstream response: {exc}",
            router_status_code=502,
        ) from exc
    if not distilled_content:
        raise UpstreamRequestError(
            status_code=502,
            detail=(
                "Upstream returned an incomplete stream for a non-streaming " "request"
            ),
            router_status_code=502,
        )
    content = distilled_content.encode("utf-8")
    if client_protocol is not None and needs_conversion(
        client_protocol, channel.protocol
    ):
        content = convert_response(
            client_protocol, channel.protocol, content, original_model
        )

    upstream_response_headers = _passthrough_headers(response.headers)
    response_headers = dict(upstream_response_headers)
    response_headers.pop("content-type", None)
    cost = await _safe_estimate_cost(
        pricing_group_name,
        parsed["input_tokens"],
        parsed["output_tokens"],
        parsed["cache_read_input_tokens"],
        parsed["cache_write_input_tokens"],
    )
    response_content = _sanitize_log_content_text(_decode_log_content_bytes(content))
    return UpstreamResult(
        response=Response(
            content=content,
            status_code=response.status_code,
            media_type="application/json",
            headers=response_headers,
        ),
        status_code=response.status_code,
        is_stream=False,
        upstream_model_name=parsed["resolved_model"],
        input_tokens=parsed["input_tokens"],
        cache_read_input_tokens=parsed["cache_read_input_tokens"],
        cache_write_input_tokens=parsed["cache_write_input_tokens"],
        output_tokens=parsed["output_tokens"],
        total_tokens=parsed["total_tokens"],
        input_cost_usd=cost[0],
        output_cost_usd=cost[1],
        total_cost_usd=cost[2],
        request_content=request_content,
        response_content=response_content if log_body_enabled else None,
        upstream_response_headers=_dump_log_json(upstream_response_headers),
        upstream_response_content=(
            _sanitize_log_content_text(raw_content) if log_body_enabled else None
        ),
        client_response_headers=_dump_log_json(response_headers),
    )


class _GatewayStreamingResponse(StreamingResponse):
    def __init__(
        self,
        content: ContentStream,
        *,
        capture: StreamCapture,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        super().__init__(
            content,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )
        self._capture = capture

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        background = self.background
        self.background = None
        try:
            await super().__call__(scope, receive, send)
        except BaseException:
            try:
                await self._finalize_stream(background)
            except Exception:
                logger.exception("Failed to finalize disconnected upstream stream")
            raise
        await self._finalize_stream(background)

    async def _finalize_stream(self, background: BackgroundTask | None) -> None:
        body_iterator = self.body_iterator
        close_iterator = getattr(body_iterator, "aclose", None)
        if close_iterator is not None:
            try:
                await close_iterator()
            except Exception:
                logger.warning("Failed to close stream iterator", exc_info=True)
        _release_stream_concurrency(self._capture)
        if not self._capture.completed:
            await _cancel_stream_capture(self._capture, "client disconnected")
        try:
            await _close_stream_resources(self._capture)
        except Exception:
            logger.warning("Failed to close upstream stream resources", exc_info=True)
        if background is not None:
            await background()


async def _build_stream_result(
    response: httpx.Response,
    channel: ChannelConfig,
    client_protocol: ProtocolKind | None,
    body: dict[str, Any],
    request_content: str | None,
    stream_started_at: float,
    log_body_enabled: bool,
    *,
    deadline: _RequestDeadline,
    client_to_close: httpx.AsyncClient | None = None,
) -> UpstreamResult:
    chat_expected_choices = body.get("n", 1)
    if (
        isinstance(chat_expected_choices, bool)
        or not isinstance(chat_expected_choices, int)
        or chat_expected_choices < 1
    ):
        chat_expected_choices = 1
    capture = StreamCapture(
        capture_body=log_body_enabled,
        chat_expected_choices=chat_expected_choices,
        client_to_close=client_to_close,
        upstream_response=response,
        deadline=deadline,
    )
    raw_iter = _stream_upstream_iterator(
        response,
        channel.protocol,
        capture,
        stream_started_at,
    )

    if client_protocol is not None and needs_conversion(
        client_protocol, channel.protocol
    ):
        converted_iter = convert_stream_iterator(
            client_protocol, channel.protocol, raw_iter, body.get("model", "")
        )
        converted_iter = _capture_converted_stream_iterator(converted_iter, capture)
        stream_media = "text/event-stream"
        converted = True
    else:
        converted_iter = raw_iter
        stream_media = response.headers.get("content-type")
        converted = False

    converted_iter = _stream_client_iterator(converted_iter, capture)

    upstream_response_headers = _passthrough_headers(response.headers)
    response_headers = dict(upstream_response_headers)
    if converted:
        # Never let the upstream media type override the converted target type.
        response_headers.pop("content-type", None)

    return UpstreamResult(
        response=_GatewayStreamingResponse(
            converted_iter,
            capture=capture,
            status_code=response.status_code,
            media_type=stream_media,
            headers=response_headers,
        ),
        is_stream=True,
        status_code=response.status_code,
        first_token_latency_ms=capture.first_token_latency_ms,
        upstream_model_name=body.get("model"),
        request_content=request_content,
        upstream_response_headers=_dump_log_json(upstream_response_headers),
        client_response_headers=_dump_log_json(response_headers),
        stream_capture=capture,
    )


async def _stream_client_iterator(
    stream: AsyncIterator[bytes],
    capture: StreamCapture,
) -> AsyncIterator[bytes]:
    finished = False
    try:
        async for chunk in stream:
            yield chunk
        finished = True
    except asyncio.CancelledError:
        await _cancel_stream_capture(capture, "client disconnected")
        raise
    finally:
        _release_stream_concurrency(capture)
        if not finished and not capture.client_disconnected:
            await _cancel_stream_capture(capture, "client disconnected")


async def _build_json_result(
    response: httpx.Response,
    channel: ChannelConfig,
    client_protocol: ProtocolKind | None,
    body: dict[str, Any],
    pricing_group_name: str | None,
    request_content: str | None,
    log_body_enabled: bool,
) -> UpstreamResult:
    content = await response.aread()
    raw_content = _decode_content_bytes(content)
    try:
        parsed = _extract_response_usage(
            channel.protocol, response, fallback_model=body.get("model")
        )
    except ValueError as exc:
        raise UpstreamRequestError(
            status_code=502,
            detail=f"Invalid upstream usage: {exc}",
            router_status_code=502,
        ) from exc
    if client_protocol is not None and needs_conversion(
        client_protocol, channel.protocol
    ):
        content = convert_response(
            client_protocol, channel.protocol, content, body.get("model", "")
        )

    cost = await _safe_estimate_cost(
        pricing_group_name,
        parsed["input_tokens"],
        parsed["output_tokens"],
        parsed["cache_read_input_tokens"],
        parsed["cache_write_input_tokens"],
    )
    upstream_response_headers = _passthrough_headers(response.headers)
    response_headers = dict(upstream_response_headers)
    return UpstreamResult(
        response=Response(
            content=content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
            headers=response_headers,
        ),
        status_code=response.status_code,
        is_stream=False,
        upstream_model_name=parsed["resolved_model"],
        input_tokens=parsed["input_tokens"],
        cache_read_input_tokens=parsed["cache_read_input_tokens"],
        cache_write_input_tokens=parsed["cache_write_input_tokens"],
        output_tokens=parsed["output_tokens"],
        total_tokens=parsed["total_tokens"],
        input_cost_usd=cost[0],
        output_cost_usd=cost[1],
        total_cost_usd=cost[2],
        request_content=request_content,
        response_content=(
            _decode_log_content_bytes(content) if log_body_enabled else None
        ),
        upstream_response_headers=_dump_log_json(upstream_response_headers),
        upstream_response_content=(
            _sanitize_log_content_text(raw_content) if log_body_enabled else None
        ),
        client_response_headers=_dump_log_json(response_headers),
    )


def _provider_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type") or error.get("status")
        return str(code) if code else None
    code = payload.get("code") or payload.get("type") or payload.get("status")
    return str(code) if code else None


async def _record_target_failure(
    *,
    target: RouteTarget,
    channel: ChannelConfig,
    runtime: dict[str, Any],
    log_ctx: _RequestLogger,
    plan: RoutingPlan,
    errors: list[str],
    failure_status_codes: list[int | None],
    attempt_started_at: float,
    effective_user_agent: str,
    upstream_body: dict[str, Any],
    request_content: str | None = None,
    request_url: str | None = None,
    request_headers: str | None = None,
    exc: UpstreamRequestError,
) -> Response | None:
    message = _format_channel_error(exc.detail)
    log_body_enabled = bool(runtime["relay_log_body_enabled"])
    policy_key, policy = resolve_channel_error_policy(
        runtime,
        channel.router_error_policy_config,
        policy_key=exc.policy_key,
        status_code=(
            exc.router_status_code
            if exc.router_status_code is not None
            else exc.status_code
        ),
    )
    decision = exc.decision
    if policy is not None:
        category = classify_error(exc.router_status_code)
        decision = (
            decision_from_policy(
                policy,
                category=category,
                user_actionable=decision.user_actionable,
            )
            or decision
        )
        # Same-target retries / fallback are owned by the proxy loop.
        # Only keep hard-stop for non-fallback policies after the loop exhausts budget.
        # Returning a response here would skip same-target retries.
        exc.stop_fallback = False
        if (
            not policy.fallback
            and policy.same_target_retries <= 0
            and decision.skip_retry
        ):
            exc.stop_fallback = True

    cooldown_seconds_applied = 0.0
    if (
        not exc.skip_route_failure
        and decision.cooldown_candidate
        and not _is_request_too_large_error(exc.status_code, message)
    ):
        cooldown_seconds_applied = app_state.router.record_failure(
            channel.id,
            message,
            status_code=exc.router_status_code,
            credential_id=target.credential_id,
            model_name=target.model_name,
            channel_keys=channel.keys,
            policy=policy,
            retry_after_seconds=exc.retry_after_seconds,
            threshold=int(runtime["circuit_breaker_threshold"]),
            cooldown_seconds=int(runtime["circuit_breaker_cooldown"]),
            max_cooldown_seconds=int(runtime["circuit_breaker_max_cooldown"]),
            probe_owner=target.probe_owner,
        )
    errors.append(message)
    failure_status_codes.append(exc.status_code)
    log_ctx.attempts.append(
        AttemptLog(
            request_id=log_ctx.request_id,
            channel_id=channel.id,
            channel_name=channel.name,
            credential_id=target.credential_id,
            credential_name=target.credential_name or "",
            model_name=target.model_name,
            status_code=exc.status_code,
            success=False,
            duration_ms=_elapsed_ms(attempt_started_at),
            error_message=message,
            error_category=decision.category.value,
            retryable=decision.retryable,
            cooldown_candidate=decision.cooldown_candidate,
            user_actionable=decision.user_actionable,
            skip_retry=decision.skip_retry,
            provider_status_code=exc.provider_status_code,
            provider_error_code=exc.provider_error_code,
            retry_after_seconds=exc.retry_after_seconds,
            error_policy_key=policy_key,
            cooldown_scope=policy.cooldown_scope if policy is not None else None,
            cooldown_seconds_applied=cooldown_seconds_applied or None,
            router_error_policy_config=channel.router_error_policy_config,
            reasoning_effort=_extract_request_reasoning_effort(
                log_ctx.body, upstream_body
            ),
            request_url=request_url,
            request_headers=request_headers,
            request_body=(_dump_log_json(upstream_body) if log_body_enabled else None),
        )
    )
    await log_ctx.update(
        requested_group_name=plan.requested_group_name,
        resolved_group_name=plan.resolved_group_name,
        upstream_model_name=None,
        channel=channel,
        user_agent=effective_user_agent,
        lifecycle_status=RequestLogLifecycleStatus.FAILED,
        status_code=exc.status_code,
        success=False,
        is_stream=bool(upstream_body.get("stream")),
        request_content=(
            exc.request_content
            if exc.request_content is not None
            else (
                request_content
                if request_content is not None
                else (_dump_log_json(upstream_body) if log_body_enabled else None)
            )
        ),
        error_message=message,
    )
    if exc.stop_fallback:
        return _protocol_error_response(
            protocol=log_ctx.protocol,
            status_code=exc.status_code,
            error_type=exc.error_type,
            message=message,
            headers=_lens_response_headers(
                request_id=log_ctx.request_id,
                attempt_count=len(log_ctx.attempts),
                final_channel_id=channel.id,
                final_model=target.model_name,
                fallback_used=len(log_ctx.attempts) > 1,
            ),
            request_id=log_ctx.request_id,
            attempt_count=len(log_ctx.attempts),
            retryable=exc.decision.retryable,
        )
    return None


def _prepare_channel_request(
    channel: ChannelConfig,
    body: dict[str, Any],
    *,
    credential_id: str | None,
    user_agent: str | None,
    forwarded_headers: Mapping[str, str] | None,
    model_group_headers: tuple[Mapping[str, str], ...],
    log_body_enabled: bool,
    path_suffix: str | None = None,
    multipart_files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
) -> tuple[Any, bytes, str | None]:
    upstream = build_upstream_request(
        channel,
        body,
        settings,
        credential_id=credential_id,
        user_agent=user_agent,
        forwarded_headers=forwarded_headers,
        model_group_headers=model_group_headers,
        path_suffix=path_suffix,
    )
    if multipart_files is not None:
        multipart_request = httpx.Request(
            "POST",
            upstream.url,
            data=upstream.json_body,
            files=multipart_files,
        )
        body_bytes = multipart_request.read()
        upstream.headers["content-type"] = multipart_request.headers["content-type"]
    else:
        body_bytes = _json_body_bytes(upstream.json_body)
    request_content = _dump_log_json(upstream.json_body) if log_body_enabled else None
    too_large_message = _request_body_too_large_message(
        len(body_bytes), settings.max_request_body_bytes
    )
    if too_large_message is not None:
        raise UpstreamRequestError(
            status_code=413,
            detail=too_large_message,
            router_status_code=None,
            error_type="request_too_large",
            decision=decide_route_error(413, body_too_large=True),
            request_content=request_content,
        )
    return upstream, body_bytes, request_content


async def _call_channel(
    channel: ChannelConfig,
    body: dict[str, Any],
    upstream: Any,
    body_bytes: bytes,
    request_content: str | None,
    deadline: _RequestDeadline,
    *,
    credential_id: str | None,
    probe_owner: object | None,
    model_name: str | None = None,
    pricing_group_name: str | None = None,
    client_protocol: ProtocolKind | None = None,
    log_body_enabled: bool = False,
    global_proxy_url: str | None = None,
) -> UpstreamResult:
    proxy_url = resolve_upstream_proxy_url(channel, global_proxy_url)
    client, close_client = _resolve_http_client(proxy_url)
    is_stream_request = bool(body.get("stream"))

    try:
        stream_started_at = perf_counter()
        async with _deadline_scope(deadline):
            response = await _send_upstream(
                client, upstream, stream=is_stream_request, body_bytes=body_bytes
            )
        response.raise_for_status()

        is_event_stream = (
            "text/event-stream" in (response.headers.get("content-type") or "").lower()
        )
        chat_protocols = frozenset(
            {
                ProtocolKind.OPENAI_CHAT,
                ProtocolKind.OPENAI_RESPONSES,
                ProtocolKind.ANTHROPIC,
                ProtocolKind.GEMINI,
            }
        )
        if (
            is_event_stream
            and not is_stream_request
            and channel.protocol in chat_protocols
        ):
            result = await _build_sse_to_json_result(
                response,
                channel,
                client_protocol,
                body.get("model", ""),
                pricing_group_name,
                request_content,
                log_body_enabled,
            )
        elif is_event_stream:
            result = await _build_stream_result(
                response,
                channel,
                client_protocol,
                body,
                request_content,
                stream_started_at,
                log_body_enabled,
                deadline=deadline,
                client_to_close=client if close_client else None,
            )
            if close_client:
                close_client = False
        elif is_stream_request and channel.protocol in chat_protocols:
            # A streaming request must receive a stream. Return an explicit
            # protocol error rather than ordinary JSON under a stream contract.
            await response.aread()
            raise UpstreamRequestError(
                status_code=502,
                detail=(
                    "Upstream returned a non-streaming response to a streaming "
                    "request"
                ),
                router_status_code=502,
                decision=decide_route_error(502),
            )
        else:
            result = await _build_json_result(
                response,
                channel,
                client_protocol,
                body,
                pricing_group_name,
                request_content,
                log_body_enabled,
            )
        if not result.is_stream:
            app_state.router.record_success(
                channel.id,
                credential_id=credential_id,
                model_name=model_name,
                probe_owner=probe_owner,
            )
        return result
    except httpx.HTTPStatusError as exc:
        await exc.response.aread()
        detail = _format_http_response_error(exc.response)
        status_code = exc.response.status_code
        raise UpstreamRequestError(
            status_code=status_code,
            detail=detail,
            router_status_code=status_code,
            decision=decide_route_error(status_code),
            provider_status_code=status_code,
            provider_error_code=_provider_error_code(exc.response),
            retry_after_seconds=parse_retry_after_seconds(exc.response.headers),
            policy_key=policy_key_for_status(status_code),
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamRequestError(
            status_code=502,
            detail=_format_transport_error(exc, upstream.url),
            router_status_code=None,
            decision=decide_route_error(None, transport_error=True),
            policy_key=policy_key_for_status(None, transport_error=True),
        ) from exc
    except TimeoutError as exc:
        raise UpstreamRequestError(
            status_code=504,
            detail=deadline.message(),
            router_status_code=None,
            error_type="gateway_timeout",
            decision=decide_route_error(None, timeout=True),
            policy_key=policy_key_for_status(None, timeout=True),
        ) from exc
    finally:
        if close_client:
            await client.aclose()


async def _send_upstream(
    client: httpx.AsyncClient, upstream: Any, *, stream: bool, body_bytes: bytes
) -> httpx.Response:
    if stream:
        request = client.build_request(
            upstream.method,
            upstream.url,
            headers=upstream.headers,
            content=body_bytes,
        )
        return await client.send(request, stream=True)
    return await client.request(
        upstream.method,
        upstream.url,
        headers=upstream.headers,
        content=body_bytes,
    )
