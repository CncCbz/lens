from __future__ import annotations

from ..credential_ownership import assign_credential_base_urls
from .shared import (
    BACKUP_DUMP_VERSION,
    BalanceScript,
    BalanceScriptEntity,
    ConfigBackupDump,
    ConfigImportResult,
    EXPORTABLE_SETTING_KEYS,
    SettingEntity,
    SettingItem,
    UTC,
    datetime,
    json,
    select,
)


def _normalize_dump_group_items(data: object) -> object:
    """Backfill older backups: map item sort_order to priority, weight to 1."""
    if not isinstance(data, dict):
        return data
    groups = data.get("groups")
    if not isinstance(groups, list):
        return data
    for group in groups:
        if not isinstance(group, dict):
            continue
        group.pop("effective_modalities", None)
        items = group.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if "priority" not in item and "sort_order" in item:
                item["priority"] = item.pop("sort_order")
            item.setdefault("weight", 1)
    return data


def _normalize_dump_credential_owners(data: object) -> object:
    if not isinstance(data, dict):
        return data
    data.setdefault("balance_scripts", [])
    sites = data.get("sites")
    if not isinstance(sites, list):
        return data
    for site in sites:
        if not isinstance(site, dict):
            continue
        credentials = site.get("credentials")
        base_urls = site.get("base_urls")
        protocols = site.get("protocols")
        if not isinstance(credentials, list) or not isinstance(base_urls, list):
            continue
        if not isinstance(protocols, list):
            protocols = []
        needs_owner = any(
            isinstance(item, dict) and not str(item.get("base_url_id") or "")
            for item in credentials
        )
        if not needs_owner:
            for item in credentials:
                if isinstance(item, dict):
                    item.setdefault("balance_script_id", "")
            continue
        assigned, protocol_credentials = assign_credential_base_urls(
            [item for item in credentials if isinstance(item, dict)],
            [item for item in base_urls if isinstance(item, dict)],
            [item for item in protocols if isinstance(item, dict)],
        )
        for item in assigned:
            item.setdefault("balance_script_id", "")
        site["credentials"] = assigned
        for protocol in protocols:
            if not isinstance(protocol, dict):
                continue
            protocol_id = str(protocol.get("id") or "")
            next_credential_id = protocol_credentials.get(protocol_id)
            if not next_credential_id:
                continue
            previous = str(protocol.get("credential_id") or "")
            protocol["credential_id"] = next_credential_id
            models = protocol.get("models")
            if previous == next_credential_id or not isinstance(models, list):
                continue
            for model in models:
                if (
                    isinstance(model, dict)
                    and str(model.get("credential_id") or "") == previous
                ):
                    model["credential_id"] = next_credential_id
    return data


class BackupExportImportMixin:
    @staticmethod
    def parse_dump(payload: bytes) -> "ConfigBackupDump":
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid backup file") from exc
        data = _normalize_dump_group_items(data)
        data = _normalize_dump_credential_owners(data)
        try:
            return ConfigBackupDump.model_validate(data)
        except ValueError as exc:
            raise ValueError("Invalid backup file") from exc

    async def export_dump(
        self,
        *,
        lens_version: str,
        include_gateway_api_keys: bool,
    ) -> ConfigBackupDump:
        async with self._session_factory() as session:
            settings_rows = (
                (
                    await session.execute(
                        select(SettingEntity)
                        .where(SettingEntity.key.in_(EXPORTABLE_SETTING_KEYS))
                        .order_by(SettingEntity.key.asc())
                    )
                )
                .scalars()
                .all()
            )
            sites = await self._load_sites(session)
            groups = await self._load_groups(session)
            model_prices = await self._load_model_prices(session)
            cronjobs = await self._load_cronjobs(session)
            balance_scripts = await self._load_balance_scripts(session)
            stats = await self._load_stats(session)
            gateway_api_keys = (
                await self._load_gateway_api_keys(session)
                if include_gateway_api_keys
                else []
            )

        return ConfigBackupDump(
            version=BACKUP_DUMP_VERSION,
            exported_at=datetime.now(UTC).isoformat(),
            lens_version=lens_version,
            include_request_logs=False,
            include_gateway_api_keys=include_gateway_api_keys,
            settings=[
                SettingItem(key=item.key, value=item.value) for item in settings_rows
            ],
            sites=sites,
            groups=groups,
            model_prices=model_prices,
            cronjobs=cronjobs,
            balance_scripts=balance_scripts,
            stats=stats,
            gateway_api_keys=gateway_api_keys,
            request_logs=[],
        )

    async def import_dump(self, dump: ConfigBackupDump) -> ConfigImportResult:
        if dump.version not in (2, 3, 4, 5, 6, 7, 8):
            raise ValueError(f"Unsupported backup version: {dump.version}")

        async with self._session_factory() as session:
            rows_affected: dict[str, int] = {}

            protocol_config_ids, protocols_by_config_id, available_model_keys = (
                await self._replace_sites(session, dump.sites)
            )
            rows_affected["sites"] = len(dump.sites)
            rows_affected["site_base_urls"] = sum(
                len(site.base_urls) for site in dump.sites
            )
            rows_affected["site_credentials"] = sum(
                len(site.credentials) for site in dump.sites
            )
            rows_affected["site_protocol_configs"] = sum(
                len(site.protocols) for site in dump.sites
            )
            rows_affected["site_models"] = sum(
                len(protocol.models)
                for site in dump.sites
                for protocol in site.protocols
            )

            await self._replace_groups(
                session,
                dump.groups,
                available_protocol_config_ids=protocol_config_ids,
                protocols_by_config_id=protocols_by_config_id,
                available_model_keys=available_model_keys,
            )
            rows_affected["model_groups"] = len(dump.groups)
            rows_affected["model_group_items"] = sum(
                len(group.items) for group in dump.groups
            )

            await self._replace_model_prices(session, dump.model_prices)
            rows_affected["model_prices"] = len(dump.model_prices)

            await self._replace_settings(session, dump.settings)
            rows_affected["settings"] = len(dump.settings)

            await self._replace_cronjobs(session, dump.cronjobs)
            rows_affected["cronjobs"] = len(dump.cronjobs)

            await self._replace_balance_scripts(session, dump.balance_scripts)
            rows_affected["balance_scripts"] = len(dump.balance_scripts)

            await self._replace_stats(session, dump.stats)
            rows_affected["imported_stats_total"] = (
                1 if dump.stats.imported_total is not None else 0
            )
            rows_affected["imported_stats_daily"] = len(dump.stats.imported_daily)
            rows_affected["request_log_daily_stats"] = len(dump.stats.request_daily)
            rows_affected["overview_model_daily_stats"] = len(dump.stats.model_daily)
            rows_affected["overview_channel_daily_stats"] = len(
                dump.stats.channel_daily
            )
            rows_affected["overview_dimension_daily_stats"] = len(
                dump.stats.dimension_daily
            )

            if dump.include_gateway_api_keys:
                await self._replace_gateway_api_keys(session, dump.gateway_api_keys)
                rows_affected["gateway_api_keys"] = len(dump.gateway_api_keys)

            await session.commit()

        return ConfigImportResult(rows_affected=rows_affected)
