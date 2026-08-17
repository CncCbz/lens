from typing import Any

PRICE_PAYLOAD_FIELDS = (
    "input_price_per_million",
    "output_price_per_million",
    "cache_read_price_per_million",
    "cache_write_price_per_million",
)


def normalize_model_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _has_price_value(price_payload: dict[str, float]) -> bool:
    return any(price_payload[field] > 0 for field in PRICE_PAYLOAD_FIELDS)


def _extract_context_window(model_payload: dict[str, Any]) -> int | None:
    limit = model_payload.get("limit")
    if not isinstance(limit, dict):
        return None
    value = limit.get("context")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_models_dev_price_index(
    payload: dict[str, Any],
) -> dict[str, dict[str, float]]:
    index: dict[str, dict[str, float]] = {}
    for provider_id, provider_payload in payload.items():
        if not isinstance(provider_payload, dict):
            continue
        models = provider_payload.get("models")
        if not isinstance(models, dict):
            continue

        normalized_provider_id = normalize_model_key(provider_id)
        for model_id, model_payload in models.items():
            if not isinstance(model_payload, dict):
                continue
            cost_payload = model_payload.get("cost")
            if not isinstance(cost_payload, dict):
                continue

            aliases = {
                normalize_model_key(str(model_id)),
                normalize_model_key(f"{normalized_provider_id}/{model_id}"),
            }
            if "/" in (model_id or ""):
                tail = model_id.rsplit("/", 1)[-1].strip()
                if tail:
                    aliases.add(normalize_model_key(tail))
            price_payload = {
                "input_price_per_million": float(cost_payload.get("input") or 0.0),
                "output_price_per_million": float(cost_payload.get("output") or 0.0),
                "cache_read_price_per_million": float(
                    cost_payload.get("cache_read") or 0.0
                ),
                "cache_write_price_per_million": float(
                    cost_payload.get("cache_write") or 0.0
                ),
                "context_window": _extract_context_window(model_payload),
            }
            for alias in aliases:
                if not alias:
                    continue
                existing = index.get(alias)
                if existing is None or (
                    not _has_price_value(existing) and _has_price_value(price_payload)
                ):
                    index[alias] = price_payload
    return index


def build_group_price_payloads(
    group_names: list[str], price_index: dict[str, dict[str, float | int | None]]
) -> list[dict[str, float | int | str | None]]:
    payloads: list[dict[str, float | int | str | None]] = []
    seen: set[str] = set()

    for raw_name in group_names:
        display_name = raw_name.strip()
        model_key = normalize_model_key(display_name)
        if not model_key or model_key in seen:
            continue
        seen.add(model_key)

        price_payload = price_index.get(model_key)
        if price_payload is None and "/" in model_key:
            price_payload = price_index.get(model_key.split("/", 1)[1])
        if price_payload is None:
            continue

        payloads.append(
            {
                "model_key": model_key,
                "display_name": display_name,
                "input_price_per_million": price_payload["input_price_per_million"],
                "output_price_per_million": price_payload["output_price_per_million"],
                "cache_read_price_per_million": price_payload[
                    "cache_read_price_per_million"
                ],
                "cache_write_price_per_million": price_payload[
                    "cache_write_price_per_million"
                ],
                "context_window": price_payload.get("context_window"),
            }
        )

    return payloads


def build_models_dev_capability_index(
    payload: dict[str, Any],
) -> dict[str, dict[str, bool]]:
    """Map model aliases to media capability flags from models.dev modalities."""
    index: dict[str, dict[str, bool]] = {}
    for provider_id, provider_payload in payload.items():
        if not isinstance(provider_payload, dict):
            continue
        models = provider_payload.get("models")
        if not isinstance(models, dict):
            continue

        normalized_provider_id = normalize_model_key(provider_id)
        for model_id, model_payload in models.items():
            if not isinstance(model_payload, dict):
                continue
            modalities = model_payload.get("modalities")
            if not isinstance(modalities, dict):
                continue
            input_modalities = modalities.get("input")
            if not isinstance(input_modalities, list):
                continue
            normalized_modalities = {str(item).lower() for item in input_modalities}
            capabilities = {
                modality: modality in normalized_modalities
                for modality in ("text", "image", "video", "pdf", "audio")
            }

            aliases = {
                normalize_model_key(str(model_id)),
                normalize_model_key(f"{normalized_provider_id}/{model_id}"),
            }
            if "/" in (model_id or ""):
                # Nested model ids (e.g. "openai/o3-mini" served by a proxy
                # provider) must not overwrite the direct provider/model alias
                # or the bare model alias, so only keep the full path.
                aliases = {normalize_model_key(f"{normalized_provider_id}/{model_id}")}
            for alias in aliases:
                if not alias:
                    continue
                existing = index.get(alias)
                if existing is None:
                    index[alias] = capabilities
                else:
                    index[alias] = {
                        modality: existing[modality] or capabilities[modality]
                        for modality in capabilities
                    }
    return index


def _lookup_capabilities(
    capability_index: dict[str, dict[str, bool]],
    model_key: str,
    host_key: str,
) -> dict[str, bool] | None:
    generic: dict[str, bool] | None = None
    for alias, capabilities in capability_index.items():
        if "/" not in alias:
            if alias == model_key and generic is None:
                generic = capabilities
            continue
        provider_part, model_part = alias.split("/", 1)
        if model_part != model_key:
            continue
        if host_key and provider_part in host_key:
            return capabilities
    return generic


def _candidate_model_keys(model_key: str) -> list[str]:
    """Full key first, then progressively shorter dash-prefixed candidates.

    e.g. "grok-4.5-free" -> ["grok-4.5-free", "grok-4.5", "grok"].
    Handles vendor suffix variants ("-free", "-latest", ...) that models.dev
    does not list while the base model id is present.
    """
    parts = model_key.split("-")
    return ["-".join(parts[:i]) for i in range(len(parts), 0, -1)]


def resolve_model_capabilities(
    capability_index: dict[str, dict[str, bool]],
    model_name: str,
    base_url_host: str | None = None,
) -> dict[str, bool]:
    """Resolve capabilities for a model name, preferring a provider-specific hit.

    models.dev data is provider-scoped; the same model id can differ across
    providers. When the channel host matches a known provider id we prefer that
    entry, otherwise fall back to the generic alias lookup. Vendor suffix
    variants (e.g. "-free") are stripped progressively until a hit is found.
    """
    normalized = normalize_model_key(model_name)
    if not normalized:
        return {}

    host_key = normalize_model_key(base_url_host or "")
    for candidate in _candidate_model_keys(normalized):
        capabilities = _lookup_capabilities(capability_index, candidate, host_key)
        if capabilities is not None:
            return capabilities
    return {}
