from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base


def enabled_column() -> Mapped[int]:
    return mapped_column(Integer, nullable=False, default=1)


def sort_order_column() -> Mapped[int]:
    return mapped_column(Integer, nullable=False, default=0)


def timestamp_column() -> Mapped[datetime]:
    return mapped_column(default=datetime.utcnow, nullable=False)


def auto_timestamp_column() -> Mapped[datetime]:
    return mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class AdminUserEntity(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = auto_timestamp_column()


class SiteEntity(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )


class SiteBaseUrlEntity(Base):
    __tablename__ = "site_base_urls"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    enabled: Mapped[int] = enabled_column()
    sort_order: Mapped[int] = sort_order_column()
    supported_protocols_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )


class SiteCredentialEntity(Base):
    __tablename__ = "site_credentials"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = enabled_column()
    cost_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    sort_order: Mapped[int] = sort_order_column()


class SiteProtocolConfigEntity(Base):
    __tablename__ = "site_protocol_configs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    protocols_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    credential_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    enabled: Mapped[int] = enabled_column()
    headers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    proxy_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="inherit"
    )
    channel_proxy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    concurrency_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rpm_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_limit_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spent_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spent_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    param_override: Mapped[str] = mapped_column(Text, nullable=False, default="")
    match_regex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    router_error_policy_config: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    base_url_id: Mapped[str] = mapped_column(String(80), nullable=False)


class SiteDiscoveredModelEntity(Base):
    __tablename__ = "site_discovered_models"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    protocol_config_id: Mapped[str] = mapped_column(
        String(80), nullable=False, index=True
    )
    credential_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[int] = enabled_column()
    sort_order: Mapped[int] = sort_order_column()
    protocol: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ModelGroupEntity(Base):
    __tablename__ = "model_groups"
    __table_args__ = (
        CheckConstraint(
            "sync_filter_mode IN ('', 'contains', 'regex')",
            name="ck_model_groups_sync_filter_mode",
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    protocols_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    strategy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="round_robin"
    )
    route_group_id: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", index=True
    )
    sync_filter_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default=""
    )
    sync_filter_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    match_overrides_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    pi_config_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    pi_config_auto: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="1"
    )
    multimodal: Mapped[str] = mapped_column(
        String(16), nullable=False, default="auto", server_default="auto"
    )
    multimodal_resolved_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    multimodal_overrides_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    allowed_key_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    restrict_keys: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class ModelGroupItemEntity(Base):
    __tablename__ = "model_group_items"
    __table_args__ = (
        CheckConstraint(
            "credential_id <> ''",
            name="ck_model_group_items_credential_id_not_empty",
        ),
        CheckConstraint(
            "priority >= 0", name="ck_model_group_items_priority_non_negative"
        ),
        CheckConstraint("weight >= 1", name="ck_model_group_items_weight_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[int] = enabled_column()
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SettingEntity(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class GatewayApiKeyEntity(Base):
    __tablename__ = "gateway_api_keys"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    remark: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    api_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[int] = enabled_column()
    allowed_models_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    excluded_models_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    max_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    spent_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = auto_timestamp_column()


class RequestLogEntity(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: uuid.uuid4().hex,
    )
    protocol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    requested_group_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_group_name: Mapped[str | None] = mapped_column(
        String(120), nullable=True, index=True
    )
    upstream_model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    protocol_config_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gateway_key_id: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="succeeded", index=True
    )
    is_stream: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_token_latency_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    cache_write_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    request_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_request_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_request_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_response_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_response_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_response_distilled: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_response_raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_response_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream_protocol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attempts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reasoning_effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_credential_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    primary_credential_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    primary_attempt_channel_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    primary_attempt_channel_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats_archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False, index=True
    )

    __table_args__ = (
        Index(
            "ix_request_logs_protocol_config_created",
            "protocol_config_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_lifecycle_created_id",
            "lifecycle_status",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_channel_created_id",
            "channel_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_gateway_created_id",
            "gateway_key_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_protocol_created_id",
            "protocol",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_stats_archive_created",
            "stats_archived",
            "lifecycle_status",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_cost_created_id",
            "total_cost_usd",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_latency_created_id",
            "latency_ms",
            "created_at",
            "id",
        ),
        Index(
            "ix_request_logs_tokens_created_id",
            "total_tokens",
            "created_at",
            "id",
        ),
    )


class ModelPriceEntity(Base):
    __tablename__ = "model_prices"

    model_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    input_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    output_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    cache_read_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    cache_write_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PiModelCatalogEntity(Base):
    __tablename__ = "pi_model_catalog"

    model_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    api: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reasoning: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_modalities_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    output_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    cache_read_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    cache_write_price_per_million: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = auto_timestamp_column()


class CronjobEntity(Base):
    __tablename__ = "cronjobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[int] = enabled_column()
    schedule_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="interval"
    )
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    run_at_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    weekdays_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="idle", index=True
    )
    last_started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_run_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    lease_owner: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = auto_timestamp_column()


class ImportedStatsTotalEntity(Base):
    __tablename__ = "imported_stats_total"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    wait_time: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ImportedStatsDailyEntity(Base):
    __tablename__ = "imported_stats_daily"

    date: Mapped[str] = mapped_column(String(8), primary_key=True)
    input_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    wait_time: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RequestLogDailyStatsEntity(Base):
    __tablename__ = "request_log_daily_stats"

    date: Mapped[str] = mapped_column(String(8), primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wait_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    cache_write_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class OverviewModelDailyStatsEntity(Base):
    __tablename__ = "overview_model_daily_stats"

    date: Mapped[str] = mapped_column(String(8), primary_key=True)
    model: Mapped[str] = mapped_column(String(200), primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class OverviewChannelDailyStatsEntity(Base):
    __tablename__ = "overview_channel_daily_stats"

    date: Mapped[str] = mapped_column(String(8), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    channel_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class OverviewDimensionDailyStatsEntity(Base):
    __tablename__ = "overview_dimension_daily_stats"

    date: Mapped[str] = mapped_column(String(8), primary_key=True)
    dimension_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    dimension_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    dimension_name: Mapped[str] = mapped_column(String(220), nullable=False, default="")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms_sum: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_token_latency_ms_sum: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    cache_write_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
