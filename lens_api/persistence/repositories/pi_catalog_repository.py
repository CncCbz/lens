from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..entities import PiModelCatalogEntity, SettingEntity
from ...models import PiModelCatalogItem
from ..shared import SETTING_PI_CATALOG_LAST_SYNC_AT


class PiCatalogRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def replace_all(self, rows: list[dict[str, object]]) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(PiModelCatalogEntity))
            for row in rows:
                model_key = str(row.get("model_key") or "").strip()
                if not model_key:
                    continue
                modalities = row.get("input_modalities")
                session.add(
                    PiModelCatalogEntity(
                        model_key=model_key,
                        provider=str(row.get("provider") or ""),
                        model_id=str(row.get("model_id") or ""),
                        display_name=str(row.get("display_name") or ""),
                        api=str(row.get("api") or ""),
                        base_url=str(row.get("base_url") or ""),
                        reasoning=1 if row.get("reasoning") else 0,
                        input_modalities_json=json.dumps(
                            modalities if isinstance(modalities, list) else [],
                            ensure_ascii=True,
                        ),
                        context_window=(
                            int(row["context_window"])
                            if row.get("context_window") is not None
                            else None
                        ),
                        max_tokens=(
                            int(row["max_tokens"])
                            if row.get("max_tokens") is not None
                            else None
                        ),
                        input_price_per_million=float(
                            row.get("input_price_per_million") or 0.0
                        ),
                        output_price_per_million=float(
                            row.get("output_price_per_million") or 0.0
                        ),
                        cache_read_price_per_million=float(
                            row.get("cache_read_price_per_million") or 0.0
                        ),
                        cache_write_price_per_million=float(
                            row.get("cache_write_price_per_million") or 0.0
                        ),
                        config_json=str(row.get("config_json") or ""),
                    )
                )
            await session.commit()

    async def list_all(self) -> list[PiModelCatalogItem]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(PiModelCatalogEntity))).scalars().all()
            return [
                PiModelCatalogItem(
                    provider=row.provider,
                    model_id=row.model_id,
                    display_name=row.display_name,
                    api=row.api,
                    base_url=row.base_url,
                    reasoning=bool(row.reasoning),
                    input_modalities=_load_json_list(row.input_modalities_json),
                    context_window=row.context_window,
                    max_tokens=row.max_tokens,
                    input_price_per_million=row.input_price_per_million,
                    output_price_per_million=row.output_price_per_million,
                    cache_read_price_per_million=row.cache_read_price_per_million,
                    cache_write_price_per_million=row.cache_write_price_per_million,
                    config_json=row.config_json,
                )
                for row in rows
            ]

    async def get_last_synced_at(self) -> str | None:
        async with self._session_factory() as session:
            entity = await session.get(SettingEntity, SETTING_PI_CATALOG_LAST_SYNC_AT)
            value = entity.value if entity is not None else ""
            return value if value.strip() else None

    async def set_last_synced_at(self, value: str) -> None:
        async with self._session_factory() as session:
            entity = await session.get(SettingEntity, SETTING_PI_CATALOG_LAST_SYNC_AT)
            if entity is None:
                session.add(
                    SettingEntity(key=SETTING_PI_CATALOG_LAST_SYNC_AT, value=value)
                )
            else:
                entity.value = value
            await session.commit()


def _load_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []
