from __future__ import annotations

from collections.abc import Iterator

from .runtime_context import (
    Any,
    BOOLEAN_SETTING_KEYS,
    BackupStore,
    ConfigBackupDump,
    ConfigImportResult,
    CronjobItem,
    CronjobRunResult,
    CronjobUpdate,
    Depends,
    FLOAT_SETTING_KEYS,
    File,
    GatewayApiKey,
    GatewayApiKeyCreate,
    GatewayApiKeyUpdate,
    INTEGER_SETTING_KEYS,
    ModelGroup,
    ModelGroupCandidatesRequest,
    ModelGroupCandidatesResponse,
    ModelGroupCreate,
    ModelGroupEnsureFromSiteRequest,
    ModelGroupEnsureFromSiteResponse,
    ModelGroupUpdate,
    ModelPriceItem,
    ModelPriceListResponse,
    ModelPriceUpdate,
    Response,
    SETTING_SITE_LOGO_URL,
    SETTING_SITE_NAME,
    SETTING_TIME_ZONE,
    SETTING_CIRCUIT_BREAKER_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN,
    SETTING_CIRCUIT_BREAKER_THRESHOLD,
    SETTING_ROUTER_ERROR_POLICY_CONFIG,
    SettingItem,
    SettingsUpdate,
    StreamingResponse,
    UploadFile,
    _read_system_version,
    app_state,
    datetime,
    json,
    normalize_router_error_policy_config_json,
    resolve_time_zone,
)
from .tasks import _sync_group_prices
from .auth import get_current_admin


async def list_model_groups(_: Any = Depends(get_current_admin)) -> list[ModelGroup]:
    return await app_state.group_repo.list_groups()


async def get_model_group(
    group_id: str, _: Any = Depends(get_current_admin)
) -> ModelGroup:
    return await app_state.group_repo.get_group(group_id)


async def list_model_prices(
    _: Any = Depends(get_current_admin),
) -> ModelPriceListResponse:
    return await app_state.model_price_repo.list_model_prices()


async def update_model_price(
    model_key: str, payload: ModelPriceUpdate, _: Any = Depends(get_current_admin)
) -> ModelPriceItem:
    return await app_state.model_price_repo.upsert_model_price(
        payload.model_copy(update={"model_key": model_key})
    )


async def sync_model_prices(
    _: Any = Depends(get_current_admin),
) -> ModelPriceListResponse:
    await _sync_group_prices(app_state, overwrite_existing=True)
    return await app_state.model_price_repo.list_model_prices()


async def list_cronjobs(
    _: Any = Depends(get_current_admin),
) -> list[CronjobItem]:
    return await app_state.cronjob_runner.list_cronjobs()


async def update_cronjob(
    task_id: str,
    payload: CronjobUpdate,
    _: Any = Depends(get_current_admin),
) -> CronjobItem:
    return await app_state.cronjob_runner.update_cronjob(
        task_id,
        enabled=payload.enabled,
        schedule_type=(
            payload.schedule_type.value if payload.schedule_type is not None else None
        ),
        interval_hours=payload.interval_hours,
        run_at_time=payload.run_at_time,
        weekdays=payload.weekdays,
    )


async def run_cronjob(
    task_id: str,
    _: Any = Depends(get_current_admin),
) -> CronjobRunResult:
    task = await app_state.cronjob_runner.run_cronjob_now(task_id)
    return CronjobRunResult(cronjob=task)


async def model_group_candidates(
    payload: ModelGroupCandidatesRequest, _: Any = Depends(get_current_admin)
) -> ModelGroupCandidatesResponse:
    return await app_state.group_repo.list_group_candidates(payload)


async def ensure_model_groups_from_site(
    payload: ModelGroupEnsureFromSiteRequest, _: Any = Depends(get_current_admin)
) -> ModelGroupEnsureFromSiteResponse:
    return await app_state.group_repo.ensure_groups_from_site(payload)


async def create_model_group(
    payload: ModelGroupCreate, _: Any = Depends(get_current_admin)
) -> ModelGroup:
    return await app_state.group_repo.create_group(payload)


async def update_model_group(
    group_id: str, payload: ModelGroupUpdate, _: Any = Depends(get_current_admin)
) -> ModelGroup:
    return await app_state.group_repo.update_group(group_id, payload)


async def delete_model_group(
    group_id: str, _: Any = Depends(get_current_admin)
) -> Response:
    await app_state.group_repo.delete_group(group_id)
    return Response(status_code=204)


async def list_settings(_: Any = Depends(get_current_admin)) -> list[SettingItem]:
    return await app_state.settings_repo.list_settings()


async def update_settings(
    payload: SettingsUpdate, _: Any = Depends(get_current_admin)
) -> list[SettingItem]:
    normalized_items = []
    current_time_zone = None
    next_time_zone = None
    next_time_zone_value = None
    if any(item.key == SETTING_TIME_ZONE for item in payload.items):
        runtime = await app_state.settings_repo.get_runtime_settings()
        current_time_zone = str(runtime["time_zone"])
    for item in payload.items:
        if item.key in {"upstream_headers_config", "upstream_param_override_config"}:
            continue
        if item.key == SETTING_SITE_NAME:
            normalized_items.append(
                SettingItem(key=item.key, value=item.value.strip() or "Lens")
            )
            continue
        if item.key == SETTING_SITE_LOGO_URL:
            normalized_items.append(SettingItem(key=item.key, value=item.value.strip()))
            continue
        if item.key == SETTING_TIME_ZONE:
            time_zone = resolve_time_zone(item.value)
            next_time_zone = time_zone.key
            next_time_zone_value = time_zone
            normalized_items.append(SettingItem(key=item.key, value=time_zone.key))
            continue
        if item.key == SETTING_ROUTER_ERROR_POLICY_CONFIG:
            normalized_items.append(
                SettingItem(
                    key=item.key,
                    value=normalize_router_error_policy_config_json(item.value),
                )
            )
            continue
        if item.key in INTEGER_SETTING_KEYS:
            value = item.value.strip()
            _parse_integer_setting(item.key, value)
            normalized_items.append(SettingItem(key=item.key, value=value))
            continue
        if item.key in FLOAT_SETTING_KEYS:
            value = item.value.strip()
            _parse_float_setting(item.key, value)
            normalized_items.append(SettingItem(key=item.key, value=value))
            continue
        if item.key in BOOLEAN_SETTING_KEYS:
            normalized_items.append(
                SettingItem(
                    key=item.key,
                    value=(
                        "true"
                        if _parse_boolean_setting(item.key, item.value)
                        else "false"
                    ),
                )
            )
            continue
        normalized_items.append(SettingItem(key=item.key, value=item.value.strip()))
    await _validate_merged_router_error_policies(normalized_items)
    stored_items = await app_state.settings_repo.upsert_settings(normalized_items)
    if next_time_zone is not None and next_time_zone != current_time_zone:
        await app_state.request_log_store.persist_request_log_stats(force=True)
        if next_time_zone_value is not None:
            await app_state.cronjob_runner.reschedule_cronjobs(next_time_zone_value)
    return stored_items


async def list_gateway_api_keys(
    _: Any = Depends(get_current_admin),
) -> list[GatewayApiKey]:
    return await app_state.gateway_api_key_repo.list_gateway_api_keys()


async def create_gateway_api_key(
    payload: GatewayApiKeyCreate, _: Any = Depends(get_current_admin)
) -> GatewayApiKey:
    return await app_state.gateway_api_key_repo.create_gateway_api_key(payload)


async def update_gateway_api_key(
    key_id: str, payload: GatewayApiKeyUpdate, _: Any = Depends(get_current_admin)
) -> GatewayApiKey:
    return await app_state.gateway_api_key_repo.update_gateway_api_key(key_id, payload)


async def delete_gateway_api_key(
    key_id: str, _: Any = Depends(get_current_admin)
) -> Response:
    await app_state.gateway_api_key_repo.delete_gateway_api_key(key_id)
    return Response(status_code=204)


async def export_settings_bundle(
    include_gateway_api_keys: bool = False,
    _: Any = Depends(get_current_admin),
) -> StreamingResponse:
    dump = await app_state.backup_store.export_dump(
        lens_version=_read_system_version(),
        include_gateway_api_keys=include_gateway_api_keys,
    )
    runtime = await app_state.settings_repo.get_runtime_settings()
    timestamp = datetime.now(resolve_time_zone(str(runtime["time_zone"]))).strftime(
        "%Y%m%d%H%M%S"
    )
    content = dump.model_dump(
        mode="json", exclude={"include_request_logs", "request_logs"}
    )
    return StreamingResponse(
        _iter_json_chunks(content),
        media_type="application/json",
        headers={
            "cache-control": "no-store",
            "content-disposition": f'attachment; filename="lens-backup-{timestamp}.json"',
        },
    )


def _iter_json_chunks(value: Any, chunk_size: int = 64 * 1024) -> Iterator[str]:
    parts: list[str] = []
    size = 0
    for part in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(
        value
    ):
        parts.append(part)
        size += len(part)
        if size >= chunk_size:
            yield "".join(parts)
            parts = []
            size = 0
    if parts:
        yield "".join(parts)


async def import_settings_bundle(
    file: UploadFile = File(...), _: Any = Depends(get_current_admin)
) -> ConfigImportResult:
    payload = await _read_upload_file(file)
    dump = _parse_config_backup_dump(payload)
    await _validate_merged_router_error_policies(list(dump.settings))
    result = await app_state.backup_store.import_dump(dump)

    app_state.settings_repo.invalidate_settings_cache()
    return result


async def _validate_merged_router_error_policies(
    normalized_items: list[SettingItem],
) -> None:
    from lens_api.gateway.router import resolve_router_error_policy
    from lens_api.models import RouterErrorPolicyConfig

    current = await app_state.settings_repo.list_settings()
    merged = {item.key: item.value for item in current}
    for item in normalized_items:
        merged[item.key] = item.value

    threshold = _parse_integer_setting(
        SETTING_CIRCUIT_BREAKER_THRESHOLD,
        merged.get(SETTING_CIRCUIT_BREAKER_THRESHOLD, "3"),
    )
    cooldown = _parse_integer_setting(
        SETTING_CIRCUIT_BREAKER_COOLDOWN,
        merged.get(SETTING_CIRCUIT_BREAKER_COOLDOWN, "60"),
    )
    max_cooldown = _parse_integer_setting(
        SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN,
        merged.get(SETTING_CIRCUIT_BREAKER_MAX_COOLDOWN, "600"),
    )
    if cooldown > max_cooldown:
        raise ValueError(
            "circuit_breaker_cooldown cannot exceed circuit_breaker_max_cooldown"
        )

    policy_raw = merged.get(SETTING_ROUTER_ERROR_POLICY_CONFIG, "")
    config = RouterErrorPolicyConfig.model_validate(
        json.loads(policy_raw) if policy_raw.strip() else {"overrides": {}}
    )
    keys = set(config.overrides) | {
        "4xx",
        "5xx",
        "400",
        "401",
        "403",
        "404",
        "408",
        "422",
        "425",
        "429",
        "500",
        "502",
        "503",
        "504",
        "529",
        "timeout",
        "transport_error",
    }
    for key in keys:
        policy = resolve_router_error_policy(
            key,
            config=config,
            circuit_breaker_threshold=threshold,
            circuit_breaker_cooldown=cooldown,
            circuit_breaker_max_cooldown=max_cooldown,
        )
        if policy is None:
            continue
        if policy.cooldown_seconds > policy.max_cooldown_seconds:
            raise ValueError(
                f"Invalid router error policy for {key}: "
                "cooldown_seconds cannot exceed max_cooldown_seconds"
            )


def _parse_integer_setting(key: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer setting: {key}") from exc


def _parse_float_setting(key: str, value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric setting: {key}") from exc


def _parse_boolean_setting(key: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean setting: {key}")


async def _read_upload_file(file: UploadFile) -> bytes:
    try:
        return await file.read()
    finally:
        await file.close()


def _parse_config_backup_dump(payload: bytes) -> ConfigBackupDump:
    try:
        return BackupStore.parse_dump(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid backup file") from exc
