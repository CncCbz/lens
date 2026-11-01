from __future__ import annotations

from copy import deepcopy
import json

from pydantic import ValidationError

from ...models import RouterErrorPolicyConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..shared import (
    Any,
    SETTING_CIRCUIT_BREAKER_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_THRESHOLD,
    SETTING_CORS_ALLOW_ORIGINS,
    SETTING_HEALTH_MIN_SAMPLES,
    SETTING_HEALTH_PENALTY_WEIGHT,
    SETTING_HEALTH_WINDOW_SECONDS,
    SETTING_FIRST_TOKEN_TIMEOUT_SECONDS,
    SETTING_STREAM_IDLE_TIMEOUT_SECONDS,
    SETTING_MODEL_LIST_COMPAT_MODE_ENABLED,
    SETTING_MAX_ATTEMPTS,
    SETTING_MULTIMODAL_AUDIO_GROUP_ID,
    SETTING_MULTIMODAL_IMAGE_GROUP_ID,
    SETTING_MULTIMODAL_RELAY_ENABLED,
    SETTING_PROXY_URL,
    SETTING_RELAY_LOG_BODY_ENABLED,
    SETTING_RELAY_LOG_REQUEST_HEADERS_ENABLED,
    SETTING_RELAY_LOG_RESPONSE_HEADERS_ENABLED,
    SETTING_RELAY_LOG_REQUEST_BODY_ENABLED,
    SETTING_RELAY_LOG_RESPONSE_BODY_ENABLED,
    SETTING_RELAY_LOG_INPUT_ENABLED,
    SETTING_RELAY_LOG_OUTPUT_ENABLED,
    SETTING_RELAY_LOG_TOOLS_ENABLED,
    SETTING_RELAY_LOG_DEBUG_MODE,
    SETTING_RELAY_LOG_KEEP_ENABLED,
    SETTING_RELAY_LOG_KEEP_PERIOD,
    SETTING_ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD,
    SETTING_ROUTER_CIRCUIT_MINIMUM_REQUESTS,
    SETTING_ROUTER_ERROR_POLICY_CONFIG,
    SETTING_SITE_LOGO_URL,
    SETTING_SITE_NAME,
    SETTING_TIME_ZONE,
    SETTING_VERIFICATION_BATCH_SIZE,
    SETTING_VERIFICATION_CHALLENGE_COUNT,
    SETTING_VERIFICATION_CONCURRENCY,
    SETTING_VERIFICATION_MAX_TOKENS,
    SETTING_VERIFICATION_REASONING_EFFORT,
    SETTING_VERIFICATION_SELECTED_MODELS,
    SETTING_VERIFICATION_TIMEOUT_SECONDS,
    SETTING_VERIFICATION_TRUST_THRESHOLD,
    SETTING_VERIFICATION_WARN_THRESHOLD,
    SettingEntity,
    SettingItem,
    monotonic,
    normalize_time_zone,
    select,
)

_REMOVED_SETTING_KEYS = frozenset(
    {"upstream_headers_config", "upstream_param_override_config"}
)


class SettingsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        import asyncio

        self._session_factory = session_factory
        self._settings_cache: list[SettingItem] | None = None
        self._settings_cache_at = 0.0
        self._settings_cache_ttl_seconds = 2.0
        self._settings_cache_lock = asyncio.Lock()
        self._runtime_settings_cache: dict[Any, Any] | None = None
        self._runtime_settings_cache_at = 0.0

    def _clone_settings_items(self, items: list[SettingItem]) -> list[SettingItem]:
        return [SettingItem(key=item.key, value=item.value) for item in items]

    def _store_settings_cache(self, items: list[SettingItem]) -> list[SettingItem]:
        self._settings_cache = self._clone_settings_items(items)
        self._settings_cache_at = monotonic()
        self._runtime_settings_cache = None
        self._runtime_settings_cache_at = 0.0
        return self._clone_settings_items(items)

    def invalidate_settings_cache(self) -> None:
        self._settings_cache = None
        self._settings_cache_at = 0.0
        self._runtime_settings_cache = None
        self._runtime_settings_cache_at = 0.0

    def _clone_runtime_settings(self, runtime: dict[str, Any]) -> dict[str, Any]:
        cloned = dict(runtime)
        allow_origins = cloned.get("cors_allow_origins")
        if isinstance(allow_origins, list):
            cloned["cors_allow_origins"] = list(allow_origins)
        router_error_policy_config = cloned.get("router_error_policy_config")
        if isinstance(router_error_policy_config, dict):
            cloned["router_error_policy_config"] = deepcopy(router_error_policy_config)
        selected_models = cloned.get("verification_selected_models")
        if isinstance(selected_models, list):
            cloned["verification_selected_models"] = list(selected_models)
        return cloned

    @staticmethod
    def _parse_router_error_policy_config(value: str | None) -> dict[str, Any]:
        raw_value = (value or "").strip()
        if not raw_value:
            config = RouterErrorPolicyConfig()
        else:
            try:
                payload = json.loads(raw_value)
                config = RouterErrorPolicyConfig.model_validate(payload)
            except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
                config = RouterErrorPolicyConfig()
        return config.model_dump(mode="json")

    @staticmethod
    def _split_comma_lines(raw_value: str) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for chunk in raw_value.replace("\r", "\n").replace("，", ",").splitlines():
            for item in chunk.split(","):
                normalized = item.strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                items.append(normalized)
        return items

    @staticmethod
    def _parse_bool(value: str | None, *, default: bool) -> bool:
        if value is None:
            return default
        return value.strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _parse_int(value: str | None, *, default: int) -> int:
        if value is None:
            return default
        return int(value.strip())

    @staticmethod
    def _parse_float(value: str | None, *, default: float) -> float:
        if value is None:
            return default
        return float(value.strip())

    async def get_runtime_settings(self) -> dict[str, Any]:
        cached = self._runtime_settings_cache
        if (
            cached is not None
            and (monotonic() - self._runtime_settings_cache_at)
            < self._settings_cache_ttl_seconds
        ):
            return self._clone_runtime_settings(cached)

        items = await self.list_settings()
        mapping = {item.key: item.value for item in items}
        cors_allow_origins = self._split_comma_lines(
            mapping.get(SETTING_CORS_ALLOW_ORIGINS, "")
        )
        time_zone = normalize_time_zone(mapping.get(SETTING_TIME_ZONE))
        old_log_body = self._parse_bool(
            mapping.get(SETTING_RELAY_LOG_BODY_ENABLED), default=False
        )
        log_request_body = self._parse_bool(
            mapping.get(SETTING_RELAY_LOG_REQUEST_BODY_ENABLED), default=old_log_body
        )
        log_response_body = self._parse_bool(
            mapping.get(SETTING_RELAY_LOG_RESPONSE_BODY_ENABLED), default=old_log_body
        )
        runtime = {
            "proxy_url": mapping.get(SETTING_PROXY_URL, "").strip(),
            "time_zone": time_zone,
            "cors_allow_origins": cors_allow_origins or ["*"],
            "relay_log_request_headers_enabled": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_REQUEST_HEADERS_ENABLED), default=True
            ),
            "relay_log_response_headers_enabled": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_RESPONSE_HEADERS_ENABLED), default=True
            ),
            "relay_log_request_body_enabled": log_request_body,
            "relay_log_response_body_enabled": log_response_body,
            "relay_log_body_enabled": log_request_body or log_response_body,
            "relay_log_input_enabled": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_INPUT_ENABLED), default=False
            ),
            "relay_log_output_enabled": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_OUTPUT_ENABLED), default=False
            ),
            "relay_log_tools_enabled": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_TOOLS_ENABLED), default=False
            ),
            "relay_log_debug_mode": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_DEBUG_MODE), default=False
            ),
            "relay_log_keep_enabled": self._parse_bool(
                mapping.get(SETTING_RELAY_LOG_KEEP_ENABLED), default=True
            ),
            "relay_log_keep_period": self._parse_int(
                mapping.get(SETTING_RELAY_LOG_KEEP_PERIOD), default=7
            ),
            "circuit_breaker_threshold": self._parse_int(
                mapping.get(SETTING_CIRCUIT_BREAKER_THRESHOLD), default=3
            ),
            "circuit_breaker_cooldown": self._parse_int(
                mapping.get(SETTING_CIRCUIT_BREAKER_COOLDOWN), default=60
            ),
            "circuit_breaker_max_cooldown": self._parse_int(
                mapping.get(SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN), default=600
            ),
            "max_attempts": self._parse_int(
                mapping.get(SETTING_MAX_ATTEMPTS), default=3
            ),
            "router_circuit_minimum_requests": self._parse_int(
                mapping.get(SETTING_ROUTER_CIRCUIT_MINIMUM_REQUESTS), default=5
            ),
            "router_circuit_failure_rate_threshold": self._parse_float(
                mapping.get(SETTING_ROUTER_CIRCUIT_FAILURE_RATE_THRESHOLD), default=0.6
            ),
            "health_window_seconds": self._parse_int(
                mapping.get(SETTING_HEALTH_WINDOW_SECONDS), default=300
            ),
            "health_penalty_weight": self._parse_float(
                mapping.get(SETTING_HEALTH_PENALTY_WEIGHT), default=0.5
            ),
            "health_min_samples": self._parse_int(
                mapping.get(SETTING_HEALTH_MIN_SAMPLES), default=10
            ),
            "first_token_timeout_seconds": self._parse_float(
                mapping.get(SETTING_FIRST_TOKEN_TIMEOUT_SECONDS), default=180.0
            ),
            "stream_idle_timeout_seconds": self._parse_float(
                mapping.get(SETTING_STREAM_IDLE_TIMEOUT_SECONDS), default=180.0
            ),
            "model_list_compat_mode_enabled": self._parse_bool(
                mapping.get(SETTING_MODEL_LIST_COMPAT_MODE_ENABLED), default=False
            ),
            "multimodal_relay_enabled": self._parse_bool(
                mapping.get(SETTING_MULTIMODAL_RELAY_ENABLED), default=False
            ),
            "multimodal_image_group_id": mapping.get(
                SETTING_MULTIMODAL_IMAGE_GROUP_ID, ""
            ).strip(),
            "multimodal_audio_group_id": mapping.get(
                SETTING_MULTIMODAL_AUDIO_GROUP_ID, ""
            ).strip(),
            "router_error_policy_config": self._parse_router_error_policy_config(
                mapping.get(SETTING_ROUTER_ERROR_POLICY_CONFIG)
            ),
            "site_name": mapping.get(SETTING_SITE_NAME, "Lens").strip() or "Lens",
            "site_logo_url": mapping.get(SETTING_SITE_LOGO_URL, "").strip(),
            "verification_batch_size": min(
                max(
                    self._parse_int(
                        mapping.get(SETTING_VERIFICATION_BATCH_SIZE), default=5
                    ),
                    1,
                ),
                200,
            ),
            "verification_challenge_count": min(
                max(
                    self._parse_int(
                        mapping.get(SETTING_VERIFICATION_CHALLENGE_COUNT), default=3
                    ),
                    1,
                ),
                3,
            ),
            "verification_concurrency": min(
                max(
                    self._parse_int(
                        mapping.get(SETTING_VERIFICATION_CONCURRENCY), default=2
                    ),
                    1,
                ),
                10,
            ),
            "verification_timeout_seconds": min(
                max(
                    self._parse_int(
                        mapping.get(SETTING_VERIFICATION_TIMEOUT_SECONDS), default=90
                    ),
                    15,
                ),
                600,
            ),
            # 0 keeps the built-in budget derived from the challenge size.
            "verification_max_tokens": min(
                max(
                    self._parse_int(
                        mapping.get(SETTING_VERIFICATION_MAX_TOKENS), default=0
                    ),
                    0,
                ),
                262_144,
            ),
            "verification_selected_models": self._parse_selected_models(
                mapping.get(SETTING_VERIFICATION_SELECTED_MODELS)
            ),
            "verification_reasoning_effort": mapping.get(
                SETTING_VERIFICATION_REASONING_EFFORT, "none"
            ).strip(),
            # A verdict is trusted at or above the trust threshold, questioned
            # down to the warn threshold, and flagged below it.
            "verification_trust_threshold": self._parse_percent(
                mapping.get(SETTING_VERIFICATION_TRUST_THRESHOLD), default=80
            ),
            "verification_warn_threshold": self._parse_percent(
                mapping.get(SETTING_VERIFICATION_WARN_THRESHOLD), default=50
            ),
        }
        self._runtime_settings_cache = self._clone_runtime_settings(runtime)
        self._runtime_settings_cache_at = monotonic()
        return self._clone_runtime_settings(runtime)

    @staticmethod
    def _parse_percent(raw: str | None, *, default: int) -> int:
        """Clamp a percentage setting to 0-100, falling back when unparsable."""
        try:
            value = int(str(raw).strip()) if raw is not None else default
        except (TypeError, ValueError):
            return default
        return min(max(value, 0), 100)

    @staticmethod
    def _parse_selected_models(raw: str | None) -> list[str] | None:
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, list):
            return None
        result: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            value = str(item or "").strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result

    async def get_branding_settings(self) -> dict[str, str]:
        runtime = await self.get_runtime_settings()
        return {
            "site_name": str(runtime["site_name"]),
            "site_logo_url": str(runtime["site_logo_url"]),
        }

    async def list_settings(self) -> list[SettingItem]:
        cached = self._settings_cache
        if (
            cached is not None
            and (monotonic() - self._settings_cache_at)
            < self._settings_cache_ttl_seconds
        ):
            return self._clone_settings_items(cached)

        async with self._settings_cache_lock:
            cached = self._settings_cache
            if (
                cached is not None
                and (monotonic() - self._settings_cache_at)
                < self._settings_cache_ttl_seconds
            ):
                return self._clone_settings_items(cached)

            async with self._session_factory() as session:
                result = await session.execute(
                    select(SettingEntity).order_by(SettingEntity.key)
                )
                items = [
                    SettingItem(key=item.key, value=item.value)
                    for item in result.scalars().all()
                    if item.key not in _REMOVED_SETTING_KEYS
                ]
            return self._store_settings_cache(items)

    async def upsert_settings(self, items: list[SettingItem]) -> list[SettingItem]:
        items = [item for item in items if item.key not in _REMOVED_SETTING_KEYS]
        if not items:
            return await self.list_settings()
        keys = [item.key for item in items]
        async with self._session_factory() as session:
            existing = await session.execute(
                select(SettingEntity).where(SettingEntity.key.in_(keys))
            )
            existing_by_key = {
                entity.key: entity for entity in existing.scalars().all()
            }
            for item in items:
                entity = existing_by_key.get(item.key)
                if entity is None:
                    session.add(SettingEntity(key=item.key, value=item.value))
                else:
                    entity.value = item.value
            await session.commit()
            result = await session.execute(
                select(SettingEntity).order_by(SettingEntity.key)
            )
            stored_items = [
                SettingItem(key=item.key, value=item.value)
                for item in result.scalars().all()
            ]
        return self._store_settings_cache(stored_items)
