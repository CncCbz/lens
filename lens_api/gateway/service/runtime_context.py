from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from html.parser import HTMLParser
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime
from functools import lru_cache
from http import HTTPStatus
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import jwt
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import OperationalError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...core.auth import create_access_token, decode_access_token
from ...core.config import settings
from ...core.db import create_engine, create_session_factory
from ...core.model_prices import (
    build_group_price_payloads,
    build_models_dev_capability_index,
    build_models_dev_price_index,
    resolve_model_capabilities,
)
from ...core.protocol_reachability import conversion_matrix
from ...core.time_zone import resolve_time_zone
from ...models import (
    AdminLoginRequest,
    AdminProfileUpdateRequest,
    AdminProfileUpdateResponse,
    AdminPasswordChangeRequest,
    AdminProfile,
    AppInfo,
    AuthTokenResponse,
    ChannelConfig,
    ChannelHealth,
    ChannelStatus,
    ConfigBackupDump,
    ConfigImportResult,
    ErrorResponse,
    GatewayApiKey,
    GatewayApiKeyCreate,
    GatewayApiKeyUpdate,
    ModelGroup,
    ModelGroupCandidatesRequest,
    ModelGroupCandidatesResponse,
    ModelGroupCreate,
    ModelGroupEnsureFromSiteRequest,
    ModelGroupEnsureFromSiteResponse,
    ModelGroupMultimodalMode,
    ModelGroupUpdate,
    ModelPriceItem,
    ModelPriceListResponse,
    ModelPriceUpdate,
    PiConfigExportResponse,
    PiConfigGenerateResponse,
    MultimodalRelayConfig,
    MultimodalRelayGroupStatus,
    MultimodalRelayUpdate,
    OverviewDailyPoint,
    OverviewChannelAnalytics,
    OverviewChannelHealthPoint,
    OverviewDimensionUsageAnalytics,
    OverviewModelAnalytics,
    OverviewPerformanceAnalytics,
    OverviewSummary,
    ProtocolKind,
    PublicBranding,
    RequestLogDetail,
    RequestLogItem,
    RequestLogLifecycleStatus,
    RequestLogPage,
    RequestLogSortMode,
    RequestLogStatusFilter,
    RoutePreviewRequest,
    RoutePreviewResponse,
    RoutePreviewTarget,
    RoutingStrategy,
    CronjobItem,
    CronjobRunResult,
    CronjobUpdate,
    SettingItem,
    SettingsUpdate,
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
    normalize_router_error_policy_config_json,
)
from ...persistence.admin_repository import AdminRepository
from ...persistence.backup_store import BackupStore
from ...persistence.channel_store import ChannelStore
from ...persistence.shared import (
    SETTING_CIRCUIT_BREAKER_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_THRESHOLD,
    SETTING_HEALTH_MIN_SAMPLES,
    SETTING_HEALTH_PENALTY_WEIGHT,
    SETTING_HEALTH_WINDOW_SECONDS,
    SETTING_MAX_ATTEMPTS,
    SETTING_MULTIMODAL_AUDIO_GROUP_ID,
    SETTING_MULTIMODAL_IMAGE_GROUP_ID,
    SETTING_MULTIMODAL_RELAY_ENABLED,
    SETTING_RELAY_LOG_BODY_ENABLED,
    SETTING_RELAY_LOG_KEEP_PERIOD,
    SETTING_ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD,
    SETTING_ROUTER_CIRCUIT_MINIMUM_REQUESTS,
    SETTING_SITE_LOGO_URL,
    SETTING_SITE_NAME,
    SETTING_TIME_ZONE,
    SETTING_ROUTER_ERROR_POLICY_CONFIG,
)
from ...persistence.repositories import (
    GatewayApiKeyRepository,
    GroupRepository,
    ModelPriceRepository,
    PiCatalogRepository,
    RequestLogStore,
    SettingsRepository,
)
from ...persistence.cronjob_store import CronjobSpec, CronjobStore
from ...persistence.entities import AdminUserEntity
from ..converters import (
    can_reach_protocol,
    convert_request,
    convert_response,
    convert_stream_iterator,
    needs_conversion,
)
from ..router import (
    GatewayRouter,
    RouteErrorDecision,
    RouteSelection,
    RouteTarget,
    decide_route_error,
)
from ..cronjob_runner import CronjobAlreadyRunningError, CronjobRunner
from ..upstreams import (
    UpstreamRequest,
    build_upstream_headers,
    build_upstream_request,
    resolve_channel_api_key,
    resolve_channel_model_list_url,
    resolve_upstream_proxy_url,
)

TASK_REQUEST_LOG_PRUNE = "request_log_prune"
TASK_MODEL_PRICE_SYNC = "model_price_sync"
TASK_PI_CATALOG_SYNC = "pi_catalog_sync"
TASK_REQUEST_LOG_STATS_PERSIST = "request_log_stats_persist"

GENERIC_USER_AGENT_TOKENS = (
    "python-httpx",
    "python/httpx",
    "python-requests",
    "python/requests",
    "python/http",
    "aiohttp",
    "httpcore",
    "urllib",
)

ANTHROPIC_FORWARD_HEADER_PREFIXES = (
    "anthropic-",
    "x-anthropic-",
    "x-claude-code-",
    "x-claude-remote-",
    "x-stainless-",
)
ANTHROPIC_FORWARD_HEADERS = frozenset(
    {
        "x-app",
        "x-app-name",
        "x-app-ver",
        "x-client-app",
        "x-environment-runner-version",
    }
)

CRONJOB_SPECS = (
    CronjobSpec(
        id=TASK_REQUEST_LOG_PRUNE,
        name="请求日志清理",
        description="按日志保留天数清理过期请求日志",
        default_interval_hours=1,
    ),
    CronjobSpec(
        id=TASK_MODEL_PRICE_SYNC,
        name="模型价格同步",
        description="从 models.dev 同步模型价格",
        default_interval_hours=24,
    ),
    CronjobSpec(
        id=TASK_PI_CATALOG_SYNC,
        name="pi.dev 模型目录同步",
        description="从 pi.dev 同步模型配置目录",
        default_interval_hours=24,
    ),
    CronjobSpec(
        id=TASK_REQUEST_LOG_STATS_PERSIST,
        name="请求日志统计落库",
        description="归档请求日志统计数据",
        default_interval_hours=1,
    ),
)

logger = logging.getLogger(__name__)

INTEGER_SETTING_KEYS = {
    SETTING_RELAY_LOG_KEEP_PERIOD,
    SETTING_CIRCUIT_BREAKER_THRESHOLD,
    SETTING_CIRCUIT_BREAKER_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN,
    SETTING_MAX_ATTEMPTS,
    SETTING_ROUTER_CIRCUIT_MINIMUM_REQUESTS,
    SETTING_HEALTH_WINDOW_SECONDS,
    SETTING_HEALTH_MIN_SAMPLES,
}
FLOAT_SETTING_KEYS = {
    SETTING_HEALTH_PENALTY_WEIGHT,
    SETTING_ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD,
}
BOOLEAN_SETTING_KEYS = {
    SETTING_RELAY_LOG_BODY_ENABLED,
    SETTING_MULTIMODAL_RELAY_ENABLED,
}


@lru_cache(maxsize=1)
def _read_system_version() -> str:
    from lens_api import __version__

    return __version__


class AppState:
    def __init__(self) -> None:
        self.http = self._create_http_client()
        self.engine = create_engine(settings.database_url)
        self.session_factory = create_session_factory(self.engine)
        self.admin_repo = AdminRepository(self.session_factory)
        self.settings_repo = SettingsRepository(self.session_factory)
        self.gateway_api_key_repo = GatewayApiKeyRepository(self.session_factory)
        self.group_repo = GroupRepository(self.session_factory)
        self.model_price_repo = ModelPriceRepository(self.session_factory)
        self.pi_catalog_repo = PiCatalogRepository(self.session_factory)
        self.request_log_store = RequestLogStore(
            self.session_factory,
            settings_repo=self.settings_repo,
            gateway_key_repo=self.gateway_api_key_repo,
        )

        self.cronjob_store = CronjobStore(self.session_factory)
        self.channel_store = ChannelStore(self.session_factory)
        self.backup_store = BackupStore(self.session_factory)
        self.router = GatewayRouter()
        self.cronjob_runner = CronjobRunner(
            store=self.cronjob_store,
            specs=CRONJOB_SPECS,
            handlers={
                TASK_REQUEST_LOG_PRUNE: self.request_log_store.prune_request_logs,
                TASK_MODEL_PRICE_SYNC: self._sync_model_prices,
                TASK_PI_CATALOG_SYNC: self._sync_pi_catalog,
                TASK_REQUEST_LOG_STATS_PERSIST: self.request_log_store.persist_request_log_stats,
            },
            time_zone_provider=self._runtime_time_zone,
            logger=logger,
        )

    @staticmethod
    def _create_http_client() -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            timeout=settings.request_timeout_seconds,
            connect=settings.connect_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_keepalive_connections,
        )
        return httpx.AsyncClient(timeout=timeout, limits=limits)

    async def _runtime_time_zone(self) -> ZoneInfo:
        runtime = await self.settings_repo.get_runtime_settings()
        return resolve_time_zone(str(runtime["time_zone"]))

    async def _sync_model_prices(self) -> None:
        from .tasks import _sync_group_prices

        await _sync_group_prices(self, overwrite_existing=True)

    async def _sync_pi_catalog(self) -> None:
        from .tasks import _sync_pi_catalog

        await _sync_pi_catalog(self)


@dataclass(slots=True)
class RoutingPlan:
    requested_group_name: str | None
    resolved_group_name: str | None
    requested_group: ModelGroup | None
    resolved_group: ModelGroup | None
    strategy: RoutingStrategy
    route_targets: list[RouteTarget] | None
    use_model_matching: bool
    cursor_key: str | None = None


@dataclass(slots=True)
class UpstreamResult:
    response: Response
    status_code: int
    is_stream: bool = False
    first_token_latency_ms: int = 0
    upstream_model_name: str | None = None
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    request_content: str | None = None
    response_content: str | None = None
    upstream_response_headers: str | None = None
    upstream_response_content: str | None = None
    upstream_response_distilled: str | None = None
    client_response_raw_content: str | None = None
    client_response_headers: str | None = None
    stream_capture: StreamCapture | None = None


@dataclass(slots=True)
class AttemptLog:
    request_id: str
    channel_id: str
    channel_name: str
    credential_id: str | None
    credential_name: str
    model_name: str | None
    status_code: int | None
    success: bool
    duration_ms: int
    error_message: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    cooldown_candidate: bool | None = None
    user_actionable: bool | None = None
    skip_retry: bool | None = None
    provider_status_code: int | None = None
    provider_error_code: str | None = None
    retry_after_seconds: float | None = None
    error_policy_key: str | None = None
    cooldown_scope: str | None = None
    cooldown_seconds_applied: float | None = None
    reasoning_effort: str | None = None
    relay_kind: str | None = None
    request_headers: str | None = None
    request_url: str | None = None
    request_body: str | None = None
    response_headers: str | None = None
    response_body: str | None = None


@dataclass(frozen=True, slots=True)
class _RequestDeadline:
    started_at: float
    timeout_seconds: float

    def remaining_seconds(self) -> float | None:
        if self.timeout_seconds <= 0:
            return None
        return max(self.timeout_seconds - (perf_counter() - self.started_at), 0.0)

    def expired(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0

    def message(self) -> str:
        timeout_seconds = float(max(self.timeout_seconds, 0))
        if timeout_seconds.is_integer():
            timeout_label = str(int(timeout_seconds))
        else:
            timeout_label = f"{timeout_seconds:.3f}".rstrip("0").rstrip(".")
        return f"Gateway request timed out after {timeout_label}s"


class UpstreamRequestError(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: Any,
        *,
        router_status_code: int | None,
        error_type: str = "upstream_error",
        decision: RouteErrorDecision | None = None,
        provider_status_code: int | None = None,
        provider_error_code: str | None = None,
        retry_after_seconds: float | None = None,
        policy_key: str | None = None,
        skip_route_failure: bool = False,
        stop_fallback: bool = False,
        request_content: str | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.router_status_code = router_status_code
        self.error_type = error_type
        decision_status_code = (
            router_status_code if router_status_code is not None else status_code
        )
        self.decision = decision or decide_route_error(decision_status_code)
        self.provider_status_code = provider_status_code
        self.provider_error_code = provider_error_code
        self.retry_after_seconds = retry_after_seconds
        self.policy_key = policy_key
        self.skip_route_failure = (
            skip_route_failure or not self.decision.cooldown_candidate
        )
        self.stop_fallback = stop_fallback or self.decision.skip_retry
        self.request_content = request_content


def _lens_response_headers(
    *,
    request_id: str,
    attempt_count: int = 0,
    final_channel_id: str | None = None,
    final_model: str | None = None,
    fallback_used: bool = False,
) -> dict[str, str]:
    headers = {
        "x-lens-request-id": request_id,
        "x-lens-attempt-count": str(max(attempt_count, 0)),
        "x-lens-fallback-used": "true" if fallback_used else "false",
    }
    if final_channel_id:
        headers["x-lens-final-channel"] = final_channel_id
    if final_model:
        headers["x-lens-final-model"] = final_model
    return headers


def _attempt_logs_to_dicts(attempts: list[AttemptLog]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for attempt in attempts:
        item = {
            "request_id": attempt.request_id,
            "channel_id": attempt.channel_id,
            "channel_name": attempt.channel_name,
            "credential_id": attempt.credential_id,
            "credential_name": attempt.credential_name,
            "model_name": attempt.model_name,
            "status_code": attempt.status_code,
            "success": attempt.success,
            "duration_ms": attempt.duration_ms,
            "error_message": attempt.error_message,
        }
        for key in (
            "error_category",
            "retryable",
            "cooldown_candidate",
            "user_actionable",
            "skip_retry",
            "provider_status_code",
            "provider_error_code",
            "retry_after_seconds",
            "error_policy_key",
            "cooldown_scope",
            "cooldown_seconds_applied",
            "reasoning_effort",
            "relay_kind",
            "request_headers",
            "request_url",
            "request_body",
            "response_headers",
            "response_body",
        ):
            value = getattr(attempt, key)
            if value is not None:
                item[key] = value
        items.append(item)
    return items


@dataclass(slots=True)
class StreamCapture:
    capture_body: bool
    saw_first_chunk: bool = False
    chat_expected_choices: int = 1
    chat_finished_choices: set[int] = field(default_factory=set)
    first_token_latency_ms: int = 0
    response_content_chunks: list[str] = field(default_factory=list)
    client_response_content_chunks: list[str] = field(default_factory=list)
    event_buffer: str = ""
    event_format: str | None = None
    completed: bool = False
    client_disconnected: bool = False
    first_token_update_task: asyncio.Task[None] | None = None
    parse_errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    resolved_model: str | None = None
    errors: list[str] = field(default_factory=list)
    request_log_id: int | None = None
    stream_started_at: float = 0.0
    client_to_close: httpx.AsyncClient | None = None
    upstream_response: httpx.Response | None = None
    deadline: _RequestDeadline | None = None
    error_status_code: int | None = None
    concurrency_release: Callable[[], None] | None = None
    probe_owner: object | None = None
    route_health_recorded: bool = False


app_state = AppState()
