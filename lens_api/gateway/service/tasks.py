from __future__ import annotations

from urllib.parse import urlsplit

from .runtime_context import (
    AppState,
    UTC,
    build_group_price_payloads,
    build_models_dev_capability_index,
    build_models_dev_price_index,
    datetime,
    resolve_model_capabilities,
)

_MODELS_DEV_URL = "https://models.dev/api.json"


async def _sync_group_prices(state: AppState, overwrite_existing: bool = False) -> None:
    group_names = await state.group_repo.list_group_names(include_routed=True)
    if not group_names:
        await state.model_price_repo.replace_model_prices([])
        return

    response = await state.http.get(_MODELS_DEV_URL)
    response.raise_for_status()
    models_dev_payload = response.json()
    price_index = build_models_dev_price_index(models_dev_payload)
    payloads = build_group_price_payloads(group_names, price_index)
    await state.model_price_repo.sync_model_prices(
        payloads, overwrite_existing=overwrite_existing, allowed_keys=group_names
    )
    await state.model_price_repo.set_model_price_sync_time(
        datetime.now(UTC).isoformat()
    )

    capability_index = build_models_dev_capability_index(models_dev_payload)
    await _sync_group_capabilities(state, capability_index)


async def _sync_group_capabilities(
    state: AppState, capability_index: dict[str, dict[str, bool]]
) -> None:
    channels = await state.channel_store.list()
    host_by_channel_id = {
        channel.id: (urlsplit(str(channel.base_url)).hostname or "") or ""
        for channel in channels
    }
    groups = await state.group_repo.list_groups()
    updates: dict[str, dict[str, bool]] = {}
    all_modalities = ("text", "image", "video", "pdf", "audio")
    for group in groups:
        resolved: dict[str, bool] = {modality: True for modality in all_modalities}
        enabled_items = [item for item in group.items if item.enabled]
        if not enabled_items:
            resolved = {modality: False for modality in all_modalities}
        for item in enabled_items:
            capabilities = resolve_model_capabilities(
                capability_index,
                item.model_name,
                host_by_channel_id.get(item.channel_id),
            )
            # A group only counts as supporting a modality when every
            # enabled item supports it, so any routed channel can handle it.
            for modality in all_modalities:
                resolved[modality] = resolved[modality] and bool(
                    capabilities.get(modality)
                )
        updates[group.id] = resolved
    if updates:
        await state.group_repo.update_multimodal_resolved(updates)
