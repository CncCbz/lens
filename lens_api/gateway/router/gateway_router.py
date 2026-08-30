from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from enum import Enum
from functools import lru_cache
import re
from threading import Lock
from time import monotonic, time
from typing import Any

from ...core.runtime_channel_ids import protocol_config_id_from_runtime_channel_id
from ...models import (
    ChannelConfig,
    ChannelHealth,
    ChannelKeyHealth,
    ChannelKeyItem,
    ChannelStatus,
    ProtocolKind,
    RouteState,
    RouterErrorPolicy,
    RouterErrorPolicyConfig,
    RouterErrorPolicyOverride,
    RouterSnapshot,
    RoutingStrategy,
)
from ..converters import can_reach_protocol

_RPM_WINDOW_SECONDS = 60.0


class ErrorCategory(Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RouteErrorDecision:
    category: ErrorCategory
    retryable: bool
    cooldown_candidate: bool
    user_actionable: bool = False
    skip_retry: bool = False


_DEFAULT_CATEGORY_POLICIES: dict[str, RouterErrorPolicy] = {
    "4xx": RouterErrorPolicy(
        same_target_retries=0,
        fallback=False,
        cooldown_scope="none",
        failure_threshold=1,
        cooldown_seconds=0,
        max_cooldown_seconds=0,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    ),
    "5xx": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=3,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    ),
}

_DEFAULT_EXACT_POLICIES: dict[str, RouterErrorPolicy] = {
    "400": RouterErrorPolicy(
        same_target_retries=0,
        fallback=False,
        cooldown_scope="none",
        failure_threshold=1,
        cooldown_seconds=0,
        max_cooldown_seconds=0,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    ),
    "401": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="credential",
        failure_threshold=1,
        cooldown_seconds=300,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    ),
    "403": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="credential",
        failure_threshold=1,
        cooldown_seconds=300,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    ),
    "404": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=1,
        cooldown_seconds=300,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    ),
    "408": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=2,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    ),
    "422": RouterErrorPolicy(
        same_target_retries=0,
        fallback=False,
        cooldown_scope="none",
        failure_threshold=1,
        cooldown_seconds=0,
        max_cooldown_seconds=0,
        respect_retry_after=False,
        count_toward_failure_rate=False,
    ),
    "425": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=1,
        cooldown_seconds=5,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    ),
    "429": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="credential",
        failure_threshold=1,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=True,
        count_toward_failure_rate=False,
    ),
    "503": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=3,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=True,
        count_toward_failure_rate=True,
    ),
    "504": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=2,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    ),
    "529": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=1,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=True,
        count_toward_failure_rate=True,
    ),
    "timeout": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=2,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    ),
    "transport_error": RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=2,
        cooldown_seconds=60,
        max_cooldown_seconds=600,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    ),
}


def policy_key_for_status(
    status_code: int | None,
    *,
    timeout: bool = False,
    transport_error: bool = False,
) -> str | None:
    if timeout:
        return "timeout"
    if transport_error:
        return "transport_error"
    if status_code is None:
        return "timeout"
    if 400 <= status_code <= 599:
        return str(status_code)
    return None


def _category_key_for_policy(policy_key: str) -> str | None:
    if policy_key in ("4xx", "5xx"):
        return policy_key
    if policy_key.isdigit():
        status_code = int(policy_key)
        if 400 <= status_code < 500:
            return "4xx"
        if 500 <= status_code < 600:
            return "5xx"
    return None


def _apply_policy_override(
    base: RouterErrorPolicy, override: RouterErrorPolicyOverride | None
) -> RouterErrorPolicy:
    if override is None:
        return base
    data = base.model_dump()
    for field, value in override.model_dump(exclude_none=True).items():
        data[field] = value
    return RouterErrorPolicy.model_validate(data)


def _server_defaults_from_globals(
    *,
    circuit_breaker_threshold: int,
    circuit_breaker_cooldown: int,
    circuit_breaker_max_cooldown: int,
) -> RouterErrorPolicy:
    threshold = max(circuit_breaker_threshold, 1)
    cooldown = max(circuit_breaker_cooldown, 0)
    max_cooldown = max(circuit_breaker_max_cooldown, cooldown)
    return RouterErrorPolicy(
        same_target_retries=3,
        fallback=True,
        cooldown_scope="target",
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        max_cooldown_seconds=max_cooldown,
        respect_retry_after=False,
        count_toward_failure_rate=True,
    )


def _builtin_exact_policy(
    key: str,
    *,
    circuit_breaker_threshold: int,
    circuit_breaker_cooldown: int,
    circuit_breaker_max_cooldown: int,
) -> RouterErrorPolicy | None:
    if key == "503":
        base = _server_defaults_from_globals(
            circuit_breaker_threshold=circuit_breaker_threshold,
            circuit_breaker_cooldown=circuit_breaker_cooldown,
            circuit_breaker_max_cooldown=circuit_breaker_max_cooldown,
        )
        return base.model_copy(update={"respect_retry_after": True})
    return _DEFAULT_EXACT_POLICIES.get(key)


def resolve_router_error_policy(
    policy_key: str | None,
    *,
    config: RouterErrorPolicyConfig | Mapping[str, Any] | None = None,
    circuit_breaker_threshold: int = 3,
    circuit_breaker_cooldown: int = 60,
    circuit_breaker_max_cooldown: int = 600,
) -> RouterErrorPolicy | None:
    """Merge category/exact defaults with user overrides.

    Precedence (later wins): category default -> exact default -> user category -> user exact.
    """
    if policy_key is None:
        return None
    key = str(policy_key).strip().lower()
    if not key:
        return None

    if isinstance(config, RouterErrorPolicyConfig):
        overrides = config.overrides
    elif isinstance(config, Mapping):
        overrides = RouterErrorPolicyConfig.model_validate(config).overrides
    else:
        overrides = {}

    category = _category_key_for_policy(key)
    if category == "5xx":
        base = _server_defaults_from_globals(
            circuit_breaker_threshold=circuit_breaker_threshold,
            circuit_breaker_cooldown=circuit_breaker_cooldown,
            circuit_breaker_max_cooldown=circuit_breaker_max_cooldown,
        )
    elif category == "4xx":
        base = _DEFAULT_CATEGORY_POLICIES["4xx"]
    elif key in _DEFAULT_EXACT_POLICIES or key in ("timeout", "transport_error"):
        base = _DEFAULT_EXACT_POLICIES[key]
    else:
        return None

    if category is not None and key != category:
        exact = _builtin_exact_policy(
            key,
            circuit_breaker_threshold=circuit_breaker_threshold,
            circuit_breaker_cooldown=circuit_breaker_cooldown,
            circuit_breaker_max_cooldown=circuit_breaker_max_cooldown,
        )
        if exact is not None:
            base = exact
        base = _apply_policy_override(base, overrides.get(category))
        return _apply_policy_override(base, overrides.get(key))

    if key in ("4xx", "5xx"):
        return _apply_policy_override(base, overrides.get(key))

    return _apply_policy_override(base, overrides.get(key))


def decision_from_policy(
    policy: RouterErrorPolicy | None,
    *,
    category: ErrorCategory = ErrorCategory.UNKNOWN,
    user_actionable: bool = False,
) -> RouteErrorDecision | None:
    if policy is None:
        return None
    cooldown_candidate = policy.cooldown_scope != "none"
    retryable = policy.fallback or policy.same_target_retries > 0
    return RouteErrorDecision(
        category=category,
        retryable=retryable,
        cooldown_candidate=cooldown_candidate,
        user_actionable=user_actionable,
        skip_retry=not retryable,
    )


def classify_error(status_code: int | None) -> ErrorCategory:
    if status_code is None:
        return ErrorCategory.TIMEOUT
    if status_code in (401, 403):
        return ErrorCategory.AUTH
    if status_code == 429:
        return ErrorCategory.RATE_LIMIT
    if 500 <= status_code < 600:
        return ErrorCategory.SERVER
    return ErrorCategory.UNKNOWN


def decide_route_error(
    status_code: int | None,
    *,
    body_too_large: bool = False,
    transport_error: bool = False,
    timeout: bool = False,
) -> RouteErrorDecision:
    if body_too_large:
        return RouteErrorDecision(
            ErrorCategory.UNKNOWN,
            retryable=False,
            cooldown_candidate=False,
            user_actionable=True,
            skip_retry=True,
        )
    if timeout or transport_error or status_code is None:
        return RouteErrorDecision(
            ErrorCategory.TIMEOUT, retryable=True, cooldown_candidate=True
        )
    category = classify_error(status_code)
    if status_code in (400, 404, 422):
        return RouteErrorDecision(
            category,
            retryable=False,
            cooldown_candidate=False,
            user_actionable=True,
            skip_retry=True,
        )
    if status_code in (401, 403):
        return RouteErrorDecision(
            category,
            retryable=True,
            cooldown_candidate=True,
            user_actionable=True,
        )
    if status_code in (408, 429, 500, 502, 503, 504, 529):
        return RouteErrorDecision(category, retryable=True, cooldown_candidate=True)
    return RouteErrorDecision(
        category, retryable=False, cooldown_candidate=False, skip_retry=True
    )


def parse_retry_after_seconds(
    headers: Mapping[str, str] | None, *, now: float | None = None
) -> float | None:
    if not headers:
        return None
    lookup = {key.lower(): value for key, value in headers.items()}
    for key in ("retry-after-ms", "x-ms-retry-after-ms"):
        value = lookup.get(key)
        if value:
            try:
                return max(float(value.strip()) / 1000.0, 0.0)
            except ValueError:
                pass
    value = lookup.get("retry-after")
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(parsed.timestamp() - (time() if now is None else now), 0.0)


@dataclass(slots=True)
class _ScopedHealthState:
    consecutive_failures: int = 0
    last_error: str | None = None
    last_error_category: ErrorCategory | None = None
    opened_until: float = 0.0
    last_cooldown: float = 0.0
    probe_owner: object | None = None


@dataclass(slots=True)
class _HealthWindow:
    successes: int = 0
    failures: int = 0
    window_start: float = 0.0

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def failure_rate(self) -> float:
        return self.failures / self.total if self.total > 0 else 0.0

    def confidence(self, min_samples: int = 10) -> float:
        return min(1.0, self.total / min_samples)


@dataclass(slots=True)
class _SWRRNode:
    current_weight: int = 0


@dataclass(slots=True)
class RouteTarget:
    channel: ChannelConfig
    model_name: str | None = None
    credential_id: str | None = None
    credential_name: str | None = None
    priority: int = 0
    weight: int = 1
    probe_owner: object | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class RouteSelection:
    primary: RouteTarget
    fallbacks: list[RouteTarget] = field(default_factory=list)


class AllTargetsCooledError(LookupError):
    def __init__(self, message: str, *, recovery_seconds: int = 0) -> None:
        super().__init__(message)
        self.recovery_seconds = recovery_seconds


def _matches_model(channel: ChannelConfig, requested_model: str | None) -> bool:
    if not requested_model:
        return True

    if channel.model_patterns:
        for pattern in channel.model_patterns:
            if _matches_pattern(pattern, requested_model):
                return True
        return False

    return True


@lru_cache(maxsize=2048)
def _compile_model_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _matches_pattern(pattern: str, value: str) -> bool:
    try:
        return bool(_compile_model_pattern(pattern).search(value))
    except re.error:
        return False


class GatewayRouter:
    def __init__(
        self,
        *,
        health_window_seconds: int = 300,
        health_penalty_weight: float = 0.5,
        health_min_samples: int = 10,
        circuit_minimum_requests: int = 5,
        circuit_failure_rate_threshold: float = 0.6,
    ) -> None:
        self._lock = Lock()
        self._channel_health: dict[str, _ScopedHealthState] = defaultdict(
            _ScopedHealthState
        )
        self._credential_health: dict[tuple[str, str], _ScopedHealthState] = {}
        self._target_health: dict[tuple[str, str], _ScopedHealthState] = {}
        self._channel_windows: dict[str, _HealthWindow] = defaultdict(_HealthWindow)
        self._credential_windows: dict[tuple[str, str], _HealthWindow] = {}
        self._target_windows: dict[tuple[str, str], _HealthWindow] = {}
        self._swrr_nodes: dict[tuple[str, str, str], _SWRRNode] = {}
        self._channel_inflight: dict[str, int] = {}
        self._channel_rpm: dict[str, deque[float]] = {}
        self._health_window_seconds = health_window_seconds
        self._health_penalty_weight = health_penalty_weight
        self._health_min_samples = health_min_samples
        self._circuit_minimum_requests = circuit_minimum_requests
        self._circuit_failure_rate_threshold = circuit_failure_rate_threshold

    def configure_health_scoring(
        self,
        *,
        health_window_seconds: int,
        health_penalty_weight: float,
        health_min_samples: int,
        circuit_minimum_requests: int = 5,
        circuit_failure_rate_threshold: float = 0.6,
    ) -> None:
        with self._lock:
            self._health_window_seconds = max(health_window_seconds, 1)
            self._health_penalty_weight = max(health_penalty_weight, 0.0)
            self._health_min_samples = max(health_min_samples, 1)
            self._circuit_minimum_requests = max(circuit_minimum_requests, 1)
            self._circuit_failure_rate_threshold = min(
                max(circuit_failure_rate_threshold, 0.0), 1.0
            )

    def select(
        self,
        channels: list[ChannelConfig],
        protocol: ProtocolKind,
        requested_model: str | None = None,
        strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN,
        allowed_channel_ids: set[str] | None = None,
        use_model_matching: bool = True,
        route_targets: list[RouteTarget] | None = None,
        cursor_key: str | None = None,
        mutate: bool = True,
    ) -> RouteSelection:
        with self._lock:
            active = self._build_active_pool(
                channels,
                protocol,
                requested_model,
                allowed_channel_ids,
                use_model_matching,
                route_targets,
                strategy=strategy,
            )
            if not active:
                all_matching = self._build_active_pool(
                    channels,
                    protocol,
                    requested_model,
                    allowed_channel_ids,
                    use_model_matching,
                    route_targets,
                    skip_health_filter=True,
                    strategy=strategy,
                )
                if all_matching:
                    recovery = 0
                    now = monotonic()
                    for target in all_matching:
                        recovery = max(
                            recovery,
                            self._target_cooldown_remaining_seconds(target, now=now),
                        )
                    # Prefer shortest positive recovery for Retry-After.
                    positive = [
                        self._target_cooldown_remaining_seconds(target, now=now)
                        for target in all_matching
                    ]
                    positive = [value for value in positive if value > 0]
                    recovery = min(positive) if positive else 0
                    raise AllTargetsCooledError(
                        f"All {len(all_matching)} matching channels are in cooldown",
                        recovery_seconds=recovery,
                    )
                detail = f"No enabled channels available for protocol={protocol.value}"
                if requested_model:
                    detail = f"No enabled channels matched {requested_model}"
                raise LookupError(detail)

            route_key = cursor_key or protocol.value
            if strategy == RoutingStrategy.FAILOVER:
                active = sorted(active, key=lambda target: target.priority)
                primary_index = 0
            elif strategy == RoutingStrategy.PRIORITY_WEIGHTED:
                return self._select_priority_weighted(active, route_key, mutate=mutate)
            else:
                primary_index = self._swrr_pick_index(active, route_key, mutate=mutate)

            primary = active[primary_index]
            fallbacks = active[primary_index + 1 :] + active[:primary_index]

            return RouteSelection(primary=primary, fallbacks=fallbacks)

    def snapshot(self, channels: list[ChannelConfig]) -> RouterSnapshot:
        with self._lock:
            now = monotonic()
            routes = [
                self._build_route_state(channels, protocol, now=now)
                for protocol in ProtocolKind
            ]
            health = [
                self._build_channel_health(channel, now=now) for channel in channels
            ]

        return RouterSnapshot(routes=routes, health=health)

    def cooldown_snapshot(self) -> list[ChannelHealth]:
        """In-memory cooldown view without loading channel configs from DB."""
        with self._lock:
            now = monotonic()
            channel_ids = set(self._channel_health)
            channel_ids.update(channel_id for channel_id, _ in self._credential_health)
            channel_ids.update(channel_id for channel_id, _ in self._target_health)
            return [
                self._build_channel_cooldown_health(channel_id, now=now)
                for channel_id in sorted(channel_ids)
            ]

    def record_success(
        self,
        channel_id: str,
        *,
        credential_id: str | None = None,
        model_name: str | None = None,
        probe_owner: object | None = None,
    ) -> None:
        with self._lock:
            if not self._probe_result_is_current_locked(
                channel_id,
                credential_id=credential_id,
                model_name=model_name,
                probe_owner=probe_owner,
            ):
                return
            was_probe = probe_owner is not None
            self._clear_route_target_locked(
                channel_id,
                credential_id=credential_id,
                model_name=model_name,
                clear_windows=was_probe,
            )
            self._update_scope_window_locked(
                "channel",
                channel_id,
                model_name=None,
                credential_id=None,
                success=True,
            )

    def record_failure(
        self,
        channel_id: str,
        error: str,
        *,
        status_code: int | None = None,
        credential_id: str | None = None,
        model_name: str | None = None,
        channel_keys: list[ChannelKeyItem] | None = None,
        policy: RouterErrorPolicy | None = None,
        retry_after_seconds: float | None = None,
        threshold: int = 0,
        cooldown_seconds: int = 0,
        max_cooldown_seconds: int = 0,
        probe_owner: object | None = None,
    ) -> float:
        del channel_keys  # legacy arg; multi-key stacking removed
        category = classify_error(status_code)
        if policy is None:
            policy = resolve_router_error_policy(
                policy_key_for_status(status_code),
                circuit_breaker_threshold=threshold or 3,
                circuit_breaker_cooldown=cooldown_seconds or 60,
                circuit_breaker_max_cooldown=max_cooldown_seconds or 600,
            )
        with self._lock:
            if not self._probe_result_is_current_locked(
                channel_id,
                credential_id=credential_id,
                model_name=model_name,
                probe_owner=probe_owner,
            ):
                return 0.0
            self._clear_probe_owner_locked(
                channel_id,
                credential_id=credential_id,
                model_name=model_name,
                probe_owner=probe_owner,
            )

            if policy is None or policy.cooldown_scope == "none":
                return 0.0

            scope, state = self._resolve_scope_state_locked(
                policy.cooldown_scope,
                channel_id,
                credential_id=credential_id,
                model_name=model_name,
                create=True,
            )
            assert state is not None
            state.consecutive_failures += 1
            state.last_error = error
            state.last_error_category = category
            state.probe_owner = None

            if policy.count_toward_failure_rate:
                self._update_scope_window_locked(
                    scope,
                    channel_id,
                    model_name=model_name,
                    credential_id=credential_id,
                    success=False,
                )

            applied = self._maybe_open_scope_locked(
                state,
                policy=policy,
                retry_after_seconds=retry_after_seconds,
                scope=scope,
                channel_id=channel_id,
                credential_id=credential_id,
                model_name=model_name,
            )
            return applied

    def record_key_failure(
        self,
        channel_id: str,
        key_id: str,
        status_code: int | None = None,
        *,
        max_cooldown_seconds: int = 0,
        retry_after_seconds: float | None = None,
    ) -> float:
        policy = resolve_router_error_policy(
            policy_key_for_status(status_code),
            circuit_breaker_max_cooldown=max_cooldown_seconds or 600,
        )
        if policy is None:
            policy = _DEFAULT_EXACT_POLICIES["401"].model_copy(
                update={"cooldown_scope": "credential"}
            )
        else:
            policy = policy.model_copy(update={"cooldown_scope": "credential"})
        return self.record_failure(
            channel_id,
            f"key failure {status_code}",
            status_code=status_code,
            credential_id=key_id,
            policy=policy,
            retry_after_seconds=retry_after_seconds,
            max_cooldown_seconds=max_cooldown_seconds,
        )

    def record_key_success(self, channel_id: str, key_id: str) -> None:
        with self._lock:
            self._credential_health.pop((channel_id, key_id), None)
            self._credential_windows.pop((channel_id, key_id), None)

    def clear_cooldown(self, channel_id: str) -> None:
        with self._lock:
            self._channel_health.pop(channel_id, None)
            self._channel_windows.pop(channel_id, None)
            for key in [k for k in self._credential_health if k[0] == channel_id]:
                self._credential_health.pop(key, None)
                self._credential_windows.pop(key, None)
            for key in [k for k in self._target_health if k[0] == channel_id]:
                self._target_health.pop(key, None)
                self._target_windows.pop(key, None)

    def is_channel_available(self, channel_id: str) -> bool:
        with self._lock:
            state = self._channel_health.get(channel_id)
            if state is None or state.opened_until <= 0:
                return True
            if state.opened_until <= monotonic():
                return True
            return False

    def is_target_available(self, target: RouteTarget) -> bool:
        with self._lock:
            return self._target_is_available(target, now=monotonic())

    def acquire_target(
        self, target: RouteTarget
    ) -> tuple[Callable[[], None] | None, str | None]:
        """Reserve a target. Second value is a capacity reason when rejected."""
        with self._lock:
            concurrency_key = protocol_config_id_from_runtime_channel_id(
                target.channel.id
            )
            current = self._channel_inflight.get(concurrency_key, 0)
            if (
                target.channel.concurrency_limit > 0
                and current >= target.channel.concurrency_limit
            ):
                return None, "concurrency"
            if self._rpm_limit_reached_locked(
                concurrency_key, target.channel.rpm_limit
            ):
                return None, "rpm"
            if self._usage_limit_reached(target.channel):
                return None, "usage"
            if not self._try_acquire_target_locked(target):
                return None, None
            self._channel_inflight[concurrency_key] = current + 1
            if target.channel.rpm_limit > 0:
                self._channel_rpm.setdefault(concurrency_key, deque()).append(
                    monotonic()
                )

        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                remaining = self._channel_inflight.get(concurrency_key, 0) - 1
                if remaining > 0:
                    self._channel_inflight[concurrency_key] = remaining
                else:
                    self._channel_inflight.pop(concurrency_key, None)

        return release, None

    def _rpm_limit_reached_locked(self, key: str, rpm_limit: int) -> bool:
        window = self._channel_rpm.get(key)
        if window is None:
            return False
        cutoff = monotonic() - _RPM_WINDOW_SECONDS
        while window and window[0] <= cutoff:
            window.popleft()
        if not window:
            self._channel_rpm.pop(key, None)
            return False
        return rpm_limit > 0 and len(window) >= rpm_limit

    @staticmethod
    def _usage_limit_reached(channel: ChannelConfig) -> bool:
        if channel.token_limit > 0 and channel.spent_tokens >= channel.token_limit:
            return True
        if (
            channel.cost_limit_usd > 0
            and channel.spent_cost_usd >= channel.cost_limit_usd
        ):
            return True
        return False

    def _try_acquire_target_locked(self, target: RouteTarget) -> bool:
        now = monotonic()
        if not self._target_is_available(target, now=now, allow_probe=True):
            return False
        probe_states: list[_ScopedHealthState] = []
        for _scope, state in self._iter_target_states(target):
            if state is None:
                continue
            if state.opened_until > now:
                return False
            if (
                state.opened_until <= now
                and state.consecutive_failures > 0
                and state.last_cooldown > 0
            ):
                if state.probe_owner is not None:
                    return False
                probe_states.append(state)
        if probe_states:
            target.probe_owner = object()
            for state in probe_states:
                state.probe_owner = target.probe_owner
                state.opened_until = 0.0
        else:
            target.probe_owner = None
        return True

    def release_probe(self, target: RouteTarget) -> None:
        if target.probe_owner is None:
            return
        with self._lock:
            self._clear_probe_owner_locked(
                target.channel.id,
                credential_id=target.credential_id,
                model_name=target.model_name,
                probe_owner=target.probe_owner,
            )

    def _build_active_pool(
        self,
        channels: list[ChannelConfig],
        protocol: ProtocolKind,
        requested_model: str | None,
        allowed_channel_ids: set[str] | None = None,
        use_model_matching: bool = True,
        route_targets: list[RouteTarget] | None = None,
        *,
        skip_health_filter: bool = False,
        strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN,
    ) -> list[RouteTarget]:
        active = self._filter_enabled_targets(
            channels,
            protocol,
            requested_model,
            allowed_channel_ids,
            use_model_matching,
            route_targets,
        )

        if not skip_health_filter:
            now = monotonic()
            active = [
                target
                for target in active
                if self._target_is_available(target, now=now)
            ]
            active = self._prefer_native_targets(active, protocol)
            if strategy == RoutingStrategy.ROUND_ROBIN and len(active) > 1:
                active.sort(key=lambda t: self._score_target(t), reverse=True)

        return active

    def _prefer_native_targets(
        self, targets: list[RouteTarget], protocol: ProtocolKind
    ) -> list[RouteTarget]:
        target_keys = [
            (
                target,
                (
                    protocol_config_id_from_runtime_channel_id(target.channel.id),
                    target.credential_id,
                    target.model_name,
                ),
            )
            for target in targets
        ]
        native_available_by_key: dict[tuple[str, str | None, str | None], bool] = {}
        for target, key in target_keys:
            if target.channel.protocol == protocol:
                native_available_by_key[key] = True
            elif key not in native_available_by_key:
                native_available_by_key[key] = False

        result: list[RouteTarget] = []
        for target, key in target_keys:
            is_native = target.channel.protocol == protocol
            if native_available_by_key.get(key, False) and not is_native:
                continue
            result.append(target)
        return result

    def _filter_enabled_targets(
        self,
        channels: list[ChannelConfig],
        protocol: ProtocolKind,
        requested_model: str | None,
        allowed_channel_ids: set[str] | None,
        use_model_matching: bool,
        route_targets: list[RouteTarget] | None,
    ) -> list[RouteTarget]:
        if route_targets is not None:
            active: list[RouteTarget] = []
            for target in route_targets:
                if target.channel.status != ChannelStatus.ENABLED:
                    continue
                if not can_reach_protocol(target.channel.protocol, protocol):
                    continue
                if (
                    allowed_channel_ids is not None
                    and target.channel.id not in allowed_channel_ids
                ):
                    continue
                active.extend(self._expand_target_credentials(target))
            return active

        active: list[RouteTarget] = []
        for channel in sorted(channels, key=lambda item: item.name):
            if channel.protocol != protocol or channel.status != ChannelStatus.ENABLED:
                continue
            if (
                allowed_channel_ids is not None
                and channel.id not in allowed_channel_ids
            ):
                continue
            if use_model_matching and not _matches_model(channel, requested_model):
                continue
            active.extend(
                self._expand_target_credentials(
                    RouteTarget(channel=channel, model_name=requested_model)
                )
            )
        return active

    def _expand_target_credentials(self, target: RouteTarget) -> list[RouteTarget]:
        if target.credential_id:
            key = self._find_key(target.channel, target.credential_id)
            if key is None or not key.enabled:
                return []
            return [
                RouteTarget(
                    channel=target.channel,
                    model_name=target.model_name,
                    credential_id=key.id,
                    credential_name=target.credential_name or key.remark,
                    priority=target.priority,
                    weight=target.weight,
                )
            ]

        if not target.channel.keys:
            return [target]

        return [
            RouteTarget(
                channel=target.channel,
                model_name=target.model_name,
                credential_id=key.id,
                credential_name=key.remark,
                priority=target.priority,
                weight=target.weight,
            )
            for key in self._candidate_keys(target.channel, target.model_name)
        ]

    def _candidate_keys(
        self, channel: ChannelConfig, model_name: str | None
    ) -> list[ChannelKeyItem]:
        enabled_keys = [key for key in channel.keys if key.enabled]
        if not model_name or not channel.models:
            return enabled_keys

        credential_ids = {
            item.credential_id
            for item in channel.models
            if item.enabled and _matches_pattern(item.model_name, model_name)
        }
        return [key for key in enabled_keys if key.id in credential_ids]

    def _select_priority_weighted(
        self,
        active: list[RouteTarget],
        route_key: str,
        *,
        mutate: bool,
    ) -> RouteSelection:
        min_priority = min(target.priority for target in active)
        layer_indices = [
            index
            for index, target in enumerate(active)
            if target.priority == min_priority
        ]
        layer = [active[index] for index in layer_indices]
        picked_in_layer = self._swrr_pick_index(layer, route_key, mutate=mutate)
        primary_index = layer_indices[picked_in_layer]
        primary = active[primary_index]
        layer_remaining = [
            active[index] for index in layer_indices if index != primary_index
        ]
        lower = [
            target for index, target in enumerate(active) if index not in layer_indices
        ]
        lower.sort(key=lambda target: (target.priority, -target.weight))
        return RouteSelection(primary=primary, fallbacks=layer_remaining + lower)

    @staticmethod
    def _find_key(channel: ChannelConfig, credential_id: str) -> ChannelKeyItem | None:
        for key in channel.keys:
            if key.id == credential_id:
                return key
        return None

    def _swrr_pick_index(
        self, active: list[RouteTarget], route_key: str, *, mutate: bool
    ) -> int:
        total_weight = 0
        best_idx = 0
        next_weights: list[int] = []

        for i, target in enumerate(active):
            node_key = (
                route_key,
                target.channel.id,
                target.credential_id or "",
                target.model_name or "",
            )
            node = self._swrr_nodes.get(node_key)
            current_weight = node.current_weight if node is not None else 0
            weight = max(int(target.weight), 1)
            next_weight = current_weight + weight
            next_weights.append(next_weight)
            total_weight += weight
            if next_weight > next_weights[best_idx]:
                best_idx = i

        if mutate:
            for i, target in enumerate(active):
                node_key = (
                    route_key,
                    target.channel.id,
                    target.credential_id or "",
                    target.model_name or "",
                )
                node = self._swrr_nodes.get(node_key)
                if node is None:
                    node = _SWRRNode()
                    self._swrr_nodes[node_key] = node
                node.current_weight = next_weights[i]
            best = active[best_idx]
            self._swrr_nodes[
                (
                    route_key,
                    best.channel.id,
                    best.credential_id or "",
                    best.model_name or "",
                )
            ].current_weight -= total_weight
        return best_idx

    def _build_route_state(
        self,
        channels: list[ChannelConfig],
        protocol: ProtocolKind,
        *,
        now: float,
    ) -> RouteState:
        pool = self._build_active_pool(
            channels, protocol, None, skip_health_filter=True
        )
        ordered_targets, _, next_channel_id = self._prepare_diagnostic_targets(
            pool,
            strategy=RoutingStrategy.ROUND_ROBIN,
            cursor_key=protocol.value,
            protocol=protocol,
            now=now,
        )
        availability = [
            self._target_is_available(target, now=now) for target in ordered_targets
        ]
        target_states = [
            self._target_state(target, now=now) for target in ordered_targets
        ]
        return RouteState(
            protocol=protocol,
            next_index=0,
            next_channel_id=next_channel_id,
            channel_ids=[target.channel.id for target in ordered_targets],
            available_channel_ids=[
                target.channel.id
                for target, available in zip(ordered_targets, availability)
                if available
            ],
            cooldown_channel_ids=[
                target.channel.id
                for target, available in zip(ordered_targets, availability)
                if not available
            ],
            open_channel_ids=[
                target.channel.id
                for target, state in zip(ordered_targets, target_states)
                if state == "open"
            ],
            probe_channel_ids=[
                target.channel.id
                for target, state in zip(ordered_targets, target_states)
                if state == "probe"
            ],
            requested_model=None,
        )

    def _prepare_diagnostic_targets(
        self,
        targets: list[RouteTarget],
        *,
        strategy: RoutingStrategy,
        cursor_key: str | None,
        protocol: ProtocolKind,
        now: float,
    ) -> tuple[list[RouteTarget], int, str | None]:
        if not targets:
            return [], 0, None
        available: list[RouteTarget] = []
        cooled: list[RouteTarget] = []
        for target in targets:
            (
                available if self._target_is_available(target, now=now) else cooled
            ).append(target)
        if strategy == RoutingStrategy.ROUND_ROBIN:
            available.sort(key=lambda target: self._score_target(target), reverse=True)
            cooled.sort(key=lambda target: self._score_target(target), reverse=True)

        if not available:
            return cooled, 0, None

        route_key = cursor_key or protocol.value
        if strategy == RoutingStrategy.FAILOVER:
            available = sorted(available, key=lambda target: target.priority)
            primary_index = 0
            ordered_available = available
        elif strategy == RoutingStrategy.PRIORITY_WEIGHTED:
            min_priority = min(target.priority for target in available)
            layer_indices = [
                index
                for index, target in enumerate(available)
                if target.priority == min_priority
            ]
            layer = [available[index] for index in layer_indices]
            picked = self._swrr_pick_index(layer, route_key, mutate=False)
            primary_index = layer_indices[picked]
            ordered_available = (
                [available[primary_index]]
                + [
                    available[index]
                    for index in layer_indices
                    if index != primary_index
                ]
                + [
                    target
                    for index, target in enumerate(available)
                    if index not in layer_indices
                ]
            )
        else:
            primary_index = self._swrr_pick_index(available, route_key, mutate=False)
            ordered_available = available[primary_index:] + available[:primary_index]
        return (
            ordered_available + cooled,
            primary_index,
            ordered_available[0].channel.id,
        )

    def _score_target(self, target: RouteTarget) -> float:
        penalty = 0.0
        for scope, _state in self._iter_target_states(target):
            window = self._get_scope_window(
                scope,
                target.channel.id,
                model_name=target.model_name,
                credential_id=target.credential_id,
                create=False,
            )
            if window is None:
                continue
            window = self._expire_scope_window(
                scope,
                target.channel.id,
                model_name=target.model_name,
                credential_id=target.credential_id,
                window=window,
            )
            penalty = max(
                penalty,
                window.failure_rate
                * self._health_penalty_weight
                * window.confidence(self._health_min_samples),
            )
        return 1.0 - penalty

    def _resolve_scope_state_locked(
        self,
        scope: str,
        channel_id: str,
        *,
        credential_id: str | None,
        model_name: str | None,
        create: bool,
    ) -> tuple[str, _ScopedHealthState | None]:
        effective = scope
        if effective == "credential" and not credential_id:
            effective = "target" if model_name else "channel"
        if effective == "target" and not model_name:
            effective = "channel"

        if effective == "credential":
            key = (channel_id, credential_id or "")
            state = self._credential_health.get(key)
            if state is None and create:
                state = _ScopedHealthState()
                self._credential_health[key] = state
            return effective, state
        if effective == "target":
            key = (channel_id, model_name or "")
            state = self._target_health.get(key)
            if state is None and create:
                state = _ScopedHealthState()
                self._target_health[key] = state
            return effective, state
        state = self._channel_health.get(channel_id)
        if state is None and create:
            state = _ScopedHealthState()
            self._channel_health[channel_id] = state
        elif state is None:
            state = self._channel_health[channel_id]  # defaultdict
        return "channel", state

    def _iter_target_states(
        self, target: RouteTarget
    ) -> list[tuple[str, _ScopedHealthState | None]]:
        return self._iter_route_states_locked(
            target.channel.id,
            credential_id=target.credential_id,
            model_name=target.model_name,
        )

    def _iter_route_states_locked(
        self,
        channel_id: str,
        *,
        credential_id: str | None,
        model_name: str | None,
    ) -> list[tuple[str, _ScopedHealthState | None]]:
        items: list[tuple[str, _ScopedHealthState | None]] = [
            ("channel", self._channel_health.get(channel_id)),
        ]
        if model_name:
            items.append(("target", self._target_health.get((channel_id, model_name))))
        if credential_id:
            items.append(
                (
                    "credential",
                    self._credential_health.get((channel_id, credential_id)),
                )
            )
        return items

    def _probe_result_is_current_locked(
        self,
        channel_id: str,
        *,
        credential_id: str | None,
        model_name: str | None,
        probe_owner: object | None,
    ) -> bool:
        owners = [
            state.probe_owner
            for _scope, state in self._iter_route_states_locked(
                channel_id,
                credential_id=credential_id,
                model_name=model_name,
            )
            if state is not None and state.probe_owner is not None
        ]
        if probe_owner is None:
            return not owners
        return any(owner is probe_owner for owner in owners)

    def _clear_probe_owner_locked(
        self,
        channel_id: str,
        *,
        credential_id: str | None,
        model_name: str | None,
        probe_owner: object | None,
    ) -> None:
        if probe_owner is None:
            return
        for _scope, state in self._iter_route_states_locked(
            channel_id,
            credential_id=credential_id,
            model_name=model_name,
        ):
            if state is not None and state.probe_owner is probe_owner:
                state.probe_owner = None

    def _clear_route_target_locked(
        self,
        channel_id: str,
        *,
        credential_id: str | None,
        model_name: str | None,
        clear_windows: bool,
    ) -> None:
        # Always clear channel consecutive state on success for this target path.
        channel_state = self._channel_health.get(channel_id)
        if channel_state is not None:
            channel_state.consecutive_failures = 0
            channel_state.last_error = None
            channel_state.last_error_category = None
            channel_state.opened_until = 0.0
            channel_state.probe_owner = None
            if clear_windows:
                channel_state.last_cooldown = 0.0
                self._channel_windows.pop(channel_id, None)
            else:
                # keep last_cooldown history only until probe success
                pass

        if credential_id:
            key = (channel_id, credential_id)
            if clear_windows:
                self._credential_health.pop(key, None)
                self._credential_windows.pop(key, None)
            else:
                state = self._credential_health.get(key)
                if state is not None:
                    state.consecutive_failures = 0
                    state.opened_until = 0.0
                    state.probe_owner = None
                    state.last_error = None
                    state.last_error_category = None

        if model_name:
            key = (channel_id, model_name)
            if clear_windows:
                self._target_health.pop(key, None)
                self._target_windows.pop(key, None)
            else:
                state = self._target_health.get(key)
                if state is not None:
                    state.consecutive_failures = 0
                    state.opened_until = 0.0
                    state.probe_owner = None
                    state.last_error = None
                    state.last_error_category = None

    def _get_scope_window(
        self,
        scope: str,
        channel_id: str,
        *,
        model_name: str | None,
        credential_id: str | None,
        create: bool,
    ) -> _HealthWindow | None:
        if scope == "credential":
            key = (channel_id, credential_id or "")
            window = self._credential_windows.get(key)
            if window is None and create:
                window = _HealthWindow()
                self._credential_windows[key] = window
            return window
        if scope == "target":
            key = (channel_id, model_name or "")
            window = self._target_windows.get(key)
            if window is None and create:
                window = _HealthWindow()
                self._target_windows[key] = window
            return window
        if create:
            return self._channel_windows[channel_id]
        return self._channel_windows.get(channel_id)

    def _expire_scope_window(
        self,
        scope: str,
        channel_id: str,
        *,
        model_name: str | None,
        credential_id: str | None,
        window: _HealthWindow,
    ) -> _HealthWindow:
        now = monotonic()
        if (
            window.window_start > 0
            and now - window.window_start > self._health_window_seconds
        ):
            window = _HealthWindow(window_start=now)
            if scope == "credential":
                self._credential_windows[(channel_id, credential_id or "")] = window
            elif scope == "target":
                self._target_windows[(channel_id, model_name or "")] = window
            else:
                self._channel_windows[channel_id] = window
        return window

    def _update_scope_window_locked(
        self,
        scope: str,
        channel_id: str,
        *,
        model_name: str | None,
        credential_id: str | None,
        success: bool,
    ) -> None:
        window = self._get_scope_window(
            scope,
            channel_id,
            model_name=model_name,
            credential_id=credential_id,
            create=True,
        )
        assert window is not None
        window = self._expire_scope_window(
            scope,
            channel_id,
            model_name=model_name,
            credential_id=credential_id,
            window=window,
        )
        if window.window_start == 0:
            window.window_start = monotonic()
        if success:
            window.successes += 1
        else:
            window.failures += 1

    def _maybe_open_scope_locked(
        self,
        state: _ScopedHealthState,
        *,
        policy: RouterErrorPolicy,
        retry_after_seconds: float | None,
        scope: str,
        channel_id: str,
        credential_id: str | None,
        model_name: str | None,
    ) -> float:
        del (
            state,
            policy,
            retry_after_seconds,
            scope,
            channel_id,
            credential_id,
            model_name,
        )
        # ponytail: cooldown removed; restore circuit open here if exclusion is needed again
        return 0.0

    def _target_state(self, target: RouteTarget, *, now: float) -> str:
        worst = "available"
        for _scope, state in self._iter_target_states(target):
            if state is None:
                continue
            if state.opened_until > now:
                return "open"
            if state.consecutive_failures > 0 and state.last_cooldown > 0:
                worst = "probe"
        return worst

    def _target_is_available(
        self, target: RouteTarget, *, now: float, allow_probe: bool = False
    ) -> bool:
        for _scope, state in self._iter_target_states(target):
            if state is None:
                continue
            if state.opened_until > now:
                return False
            if (
                allow_probe
                and state.consecutive_failures > 0
                and state.last_cooldown > 0
                and state.probe_owner is not None
            ):
                return False
        if target.credential_id:
            return True
        if target.channel.keys:
            return self._has_available_key(target.channel, now=now)
        return True

    def _has_available_key(self, channel: ChannelConfig, *, now: float) -> bool:
        return any(
            self._is_key_available(channel.id, key.id, now=now)
            for key in channel.keys
            if key.enabled
        )

    def _is_key_available(self, channel_id: str, key_id: str, *, now: float) -> bool:
        state = self._credential_health.get((channel_id, key_id))
        return state is None or state.opened_until <= now

    def _build_channel_cooldown_health(
        self, channel_id: str, *, now: float
    ) -> ChannelHealth:
        state = self._channel_health.get(channel_id) or _ScopedHealthState()
        opened_until = state.opened_until
        consecutive = state.consecutive_failures
        last_error = state.last_error
        last_category = state.last_error_category
        last_cooldown = state.last_cooldown
        for (cid, _mid), tstate in self._target_health.items():
            if cid != channel_id:
                continue
            opened_until = max(opened_until, tstate.opened_until)
            if tstate.consecutive_failures > consecutive:
                consecutive = tstate.consecutive_failures
                last_error = tstate.last_error
                last_category = tstate.last_error_category
                last_cooldown = tstate.last_cooldown
        key_health = [
            self._build_key_health(channel_id, key_id, now=now)
            for (cid, key_id) in sorted(self._credential_health)
            if cid == channel_id
        ]
        for item in key_health:
            opened_until = max(opened_until, item.cooled_until)
        available_key_count = sum(1 for item in key_health if item.available)
        cooled_key_count = sum(1 for item in key_health if not item.available)
        display_state = (
            "open"
            if opened_until > now
            else ("probe" if consecutive > 0 and last_cooldown > 0 else "available")
        )
        return ChannelHealth(
            channel_id=channel_id,
            state=display_state,
            consecutive_failures=consecutive,
            last_error=last_error,
            last_error_category=last_category.value if last_category else None,
            opened_until=opened_until,
            cooldown_remaining_seconds=self._remaining_seconds(opened_until, now=now),
            last_cooldown_seconds=int(last_cooldown),
            available=opened_until <= now,
            available_key_count=available_key_count,
            cooled_key_count=cooled_key_count,
            key_health=key_health,
        )

    def _build_channel_health(
        self, channel: ChannelConfig, *, now: float
    ) -> ChannelHealth:
        state = self._channel_health.get(channel.id) or _ScopedHealthState()
        # Aggregate worst open/probe across channel-scoped and any target/credential.
        opened_until = state.opened_until
        consecutive = state.consecutive_failures
        last_error = state.last_error
        last_category = state.last_error_category
        last_cooldown = state.last_cooldown
        for (cid, _mid), tstate in self._target_health.items():
            if cid != channel.id:
                continue
            opened_until = max(opened_until, tstate.opened_until)
            if tstate.consecutive_failures > consecutive:
                consecutive = tstate.consecutive_failures
                last_error = tstate.last_error
                last_category = tstate.last_error_category
                last_cooldown = tstate.last_cooldown
        for (cid, _kid), kstate in self._credential_health.items():
            if cid != channel.id:
                continue
            opened_until = max(opened_until, kstate.opened_until)

        key_health = [
            self._build_key_health(channel.id, key.id, now=now)
            for key in channel.keys
            if key.enabled
        ]
        available_key_count = sum(1 for item in key_health if item.available)
        cooled_key_count = sum(1 for item in key_health if not item.available)
        available = opened_until <= now and (
            not channel.keys or available_key_count > 0
        )
        window = self._channel_windows.get(channel.id) or _HealthWindow()
        display_state = (
            "open"
            if opened_until > now
            else ("probe" if consecutive > 0 and last_cooldown > 0 else "available")
        )
        return ChannelHealth(
            channel_id=channel.id,
            state=display_state,
            consecutive_failures=consecutive,
            last_error=last_error,
            last_error_category=(last_category.value if last_category else None),
            opened_until=opened_until,
            cooldown_remaining_seconds=self._remaining_seconds(opened_until, now=now),
            last_cooldown_seconds=int(last_cooldown),
            score=self._score_target(RouteTarget(channel=channel)),
            failure_rate=window.failure_rate,
            window_request_count=window.total,
            available=available,
            available_key_count=available_key_count,
            cooled_key_count=cooled_key_count,
            key_health=key_health,
        )

    def _build_key_health(
        self, channel_id: str, key_id: str, *, now: float
    ) -> ChannelKeyHealth:
        state = self._credential_health.get((channel_id, key_id))
        cooled_until = state.opened_until if state is not None else 0.0
        last_cooldown = state.last_cooldown if state is not None else 0.0
        consecutive_failures = state.consecutive_failures if state is not None else 0
        return ChannelKeyHealth(
            credential_id=key_id,
            consecutive_failures=consecutive_failures,
            cooled_until=cooled_until,
            cooldown_remaining_seconds=self._remaining_seconds(cooled_until, now=now),
            last_cooldown_seconds=int(last_cooldown),
            available=cooled_until <= now,
        )

    def _target_cooldown_remaining_seconds(
        self, target: RouteTarget, *, now: float
    ) -> int:
        remaining = 0
        for _scope, state in self._iter_target_states(target):
            if state is None:
                continue
            remaining = max(
                remaining, self._remaining_seconds(state.opened_until, now=now)
            )
        return remaining

    def min_recovery_seconds(self, targets: list[RouteTarget]) -> int:
        with self._lock:
            now = monotonic()
            values = [
                self._target_cooldown_remaining_seconds(target, now=now)
                for target in targets
            ]
            positive = [value for value in values if value > 0]
            return min(positive) if positive else 0

    @staticmethod
    def _remaining_seconds(until: float, *, now: float) -> int:
        if until <= now:
            return 0
        return max(int(until - now), 0)


__all__ = [
    "AllTargetsCooledError",
    "GatewayRouter",
    "RouteErrorDecision",
    "RouteSelection",
    "RouteTarget",
    "decide_route_error",
    "decision_from_policy",
    "parse_retry_after_seconds",
    "policy_key_for_status",
    "resolve_router_error_policy",
]
