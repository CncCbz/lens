"""pi.dev model catalog fetcher and pi config builder.

pi.dev/models is a static HTML site: the listing page carries the full
catalog (provider/model/name/context/prices plus a detail link per model),
and each detail page embeds the pi models.json provider fragment in a
`<pre class="raw-data-panel"><code>` block (syntax-highlighted spans
included). Extracting that block and stripping the spans yields valid JSON
that pi (`~/.pi/agent/models.json`) accepts as-is.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

PI_MODELS_LIST_URL = "https://pi.dev/models"

_CATALOG_ROW_RE = re.compile(
    r'<tr data-model-row="true"[^>]*'
    r'data-model-name="([^"]*)"[^>]*'
    r'data-model-id="([^"]*)"[^>]*'
    r'data-model-provider="([^"]*)"[^>]*>'
    r'.*?<a href="(/models/[^"]*)"',
    re.S,
)
_CONFIG_BLOCK_RE = re.compile(
    r'<pre class="raw-data-panel"><code>(.*?)</code></pre>', re.S
)
_TAG_RE = re.compile(r"<[^>]+>")

_UA_HEADERS = {"user-agent": "Mozilla/5.0 (compatible; lens/1.0)"}

_PI_FETCH_CONCURRENCY = 12
_PI_FETCH_TIMEOUT_SECONDS = 30


@dataclass(slots=True)
class PiCatalogEntry:
    provider: str
    model_id: str
    display_name: str
    api: str = ""
    base_url: str = ""
    reasoning: bool = False
    input_modalities: list[str] | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    cache_read_price_per_million: float = 0.0
    cache_write_price_per_million: float = 0.0
    config_json: str = ""

    def to_row(self) -> dict[str, object]:
        return {
            "model_key": f"{self.provider}/{self.model_id}",
            "provider": self.provider,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "api": self.api,
            "base_url": self.base_url,
            "reasoning": self.reasoning,
            "input_modalities": self.input_modalities or [],
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "input_price_per_million": self.input_price_per_million,
            "output_price_per_million": self.output_price_per_million,
            "cache_read_price_per_million": self.cache_read_price_per_million,
            "cache_write_price_per_million": self.cache_write_price_per_million,
            "config_json": self.config_json,
        }


def parse_catalog_rows(listing_html: str) -> list[dict[str, str]]:
    """Extract provider/model metadata + detail links from the listing page."""
    rows: list[dict[str, str]] = []
    for match in _CATALOG_ROW_RE.finditer(listing_html):
        display_name, model_id, provider, detail_path = match.groups()
        rows.append(
            {
                "provider": html.unescape(provider).strip(),
                "model_id": html.unescape(model_id).strip(),
                "display_name": html.unescape(display_name).strip(),
                "detail_path": detail_path,
            }
        )
    return rows


def parse_model_config(detail_html: str) -> dict[str, object] | None:
    """Extract the pi models.json provider fragment from a detail page."""
    match = _CONFIG_BLOCK_RE.search(detail_html)
    if not match:
        return None
    raw = html.unescape(_TAG_RE.sub("", match.group(1))).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("pi.dev detail page config JSON failed to parse")
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _provider_payload(
    config: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    """Pull the single provider entry out of a models.json fragment."""
    providers = config.get("providers")
    if not isinstance(providers, dict) or not providers:
        return None
    provider_id, provider_payload = next(iter(providers.items()))
    if not isinstance(provider_payload, dict):
        return None
    return str(provider_id), provider_payload


def normalize_provider_config(config: dict[str, object]) -> dict[str, object] | None:
    """Pull the single provider entry out of a models.json fragment."""
    result = _provider_payload(config)
    if result is None:
        return None
    return {"provider_id": result[0], "payload": result[1]}


def _provider_base_payload(provider_payload: dict[str, object]) -> dict[str, object]:
    """Provider-level fields (api/baseUrl/apiKey/...) without the models list."""
    return {
        key: value
        for key, value in provider_payload.items()
        if key not in {"models", "modelOverrides"}
    }


def _provider_models_list(
    provider_payload: dict[str, object],
) -> list[dict[str, object]]:
    models = provider_payload.get("models")
    if not isinstance(models, list):
        return []
    return [
        model
        for model in models
        if isinstance(model, dict) and str(model.get("id") or "").strip()
    ]


def entry_from_config(config: dict[str, object]) -> PiCatalogEntry | None:
    provider = normalize_provider_config(config)
    if provider is None:
        return None
    provider_id = provider["provider_id"]
    payload = provider["payload"]
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        return None
    model = models[0]
    if not isinstance(model, dict):
        return None

    cost = model.get("cost")
    cost_payload = cost if isinstance(cost, dict) else {}
    modalities = model.get("input")
    input_modalities = (
        [str(item) for item in modalities] if isinstance(modalities, list) else None
    )
    context_window = model.get("contextWindow")
    max_tokens = model.get("maxTokens")

    def _to_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return PiCatalogEntry(
        provider=provider_id,
        model_id=str(model.get("id") or ""),
        display_name=str(model.get("name") or ""),
        api=str(payload.get("api") or ""),
        base_url=str(payload.get("baseUrl") or ""),
        reasoning=bool(model.get("reasoning")),
        input_modalities=input_modalities,
        context_window=_to_int(context_window),
        max_tokens=_to_int(max_tokens),
        input_price_per_million=float(cost_payload.get("input") or 0.0),
        output_price_per_million=float(cost_payload.get("output") or 0.0),
        cache_read_price_per_million=float(cost_payload.get("cacheRead") or 0.0),
        cache_write_price_per_million=float(cost_payload.get("cacheWrite") or 0.0),
        config_json=json.dumps(config, ensure_ascii=False),
    )


async def _fetch_text(
    client: httpx.AsyncClient, url: str
) -> tuple[str | None, str | None]:
    try:
        response = await client.get(url, headers=_UA_HEADERS)
        response.raise_for_status()
        return response.text, None
    except httpx.HTTPError as exc:
        return None, f"GET {url} failed: {exc}"


async def fetch_pi_catalog(limit: int = 0) -> list[PiCatalogEntry]:
    """Fetch the pi.dev catalog: listing page + one detail page per model.

    The detail fetch is concurrency-limited; `limit` caps the number of
    detail pages (0 = all), which keeps on-demand calls cheap.
    """
    timeout = httpx.Timeout(_PI_FETCH_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        listing_html, error = await _fetch_text(client, PI_MODELS_LIST_URL)
        if error is not None:
            raise RuntimeError(error)
        rows = parse_catalog_rows(listing_html or "")
        if not rows:
            raise RuntimeError("pi.dev listing page contained no model rows")
        selected = rows if limit <= 0 else rows[:limit]

        semaphore = asyncio.Semaphore(_PI_FETCH_CONCURRENCY)

        async def fetch_detail(row: dict[str, str]) -> PiCatalogEntry | None:
            async with semaphore:
                detail_html, detail_error = await _fetch_text(
                    client, "https://pi.dev" + row["detail_path"]
                )
                if detail_error is not None:
                    logger.warning(
                        "pi.dev detail fetch failed for %s: %s",
                        row["detail_path"],
                        detail_error,
                    )
                    return None
                config = parse_model_config(detail_html or "")
                if config is None:
                    return None
                entry = entry_from_config(config)
                if entry is None:
                    return None
                entry.display_name = entry.display_name or row["display_name"]
                return entry

        results = await asyncio.gather(
            *(fetch_detail(row) for row in selected), return_exceptions=True
        )
    entries: list[PiCatalogEntry] = []
    for result in results:
        if isinstance(result, PiCatalogEntry):
            entries.append(result)
        elif isinstance(result, BaseException):
            logger.warning("pi.dev detail parse failed: %s", result)
    return entries


# ---------------------------------------------------------------------------
# pi config generation (models.json providers fragments)
# ---------------------------------------------------------------------------

_PLACEHOLDER_API_KEY = "YOUR_API_KEY"


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().strip()
    except ValueError:
        return ""


def _model_key_matches(model_key: str, model_name: str) -> bool:
    normalized_name = model_name.strip().lower()
    if not normalized_name:
        return False
    candidates = {
        model_key.strip().lower(),
        model_key.rsplit("/", 1)[-1].strip().lower(),
        model_key.rsplit(".", 1)[-1].strip().lower(),
    }
    return normalized_name in candidates


def match_catalog_entries(
    entries: list[PiCatalogEntry],
    *,
    model_name: str,
    channel_host: str = "",
    official_providers: dict[str, str] | None = None,
) -> list[PiCatalogEntry]:
    """Find catalog entries for a channel model.

    Prefers a provider whose baseUrl host matches the channel host; falls
    back to any provider hosting the model id, with a family->official
    provider mapping (e.g. claude-* -> anthropic) breaking ties.
    """
    by_host: list[PiCatalogEntry] = []
    by_model: list[PiCatalogEntry] = []
    for entry in entries:
        if not _model_key_matches(entry.model_id, model_name):
            continue
        by_model.append(entry)
        if channel_host and host_of(entry.base_url) == channel_host:
            by_host.append(entry)
    if by_host:
        return by_host

    family = (_normalize_family(model_name),)
    official = official_providers or {}
    official_first: list[PiCatalogEntry] = []
    rest: list[PiCatalogEntry] = []
    for entry in by_model:
        target = family[0]
        if target and official.get(target) == entry.provider:
            official_first.append(entry)
        else:
            rest.append(entry)
    return official_first + rest


def _normalize_family(model_name: str) -> str:
    lowered = model_name.strip().lower()
    for prefix in (
        "claude-",
        "gpt-",
        "o1",
        "o3",
        "o4",
        "o5",
        "gemini-",
        "deepseek-",
        "kimi-",
        "grok-",
        "mistral-",
        "ministral-",
        "codestral-",
        "llama-",
        "qwen",
        "glm-",
        "minimax-",
    ):
        if lowered.startswith(prefix):
            return prefix.rstrip("-")
    return ""


OFFICIAL_PROVIDERS: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "o5": "openai",
    "gemini": "google",
    "deepseek": "deepseek",
    "kimi": "moonshotai",
    "grok": "xai",
    "mistral": "mistral",
    "ministral": "mistral",
    "codestral": "mistral",
    "qwen": "qwen-token-plan",
    "glm": "zai",
    "minimax": "minimax",
}


def _official_rank(provider: str) -> int:
    for index, official in enumerate(OFFICIAL_PROVIDERS.values()):
        if provider == official:
            return index
    return len(OFFICIAL_PROVIDERS)


def _matched_model(entry: PiCatalogEntry, model_name: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(entry.config_json)
    except json.JSONDecodeError:
        return None
    result = _provider_payload(parsed)
    if result is None or result[0] != entry.provider:
        return None
    return next(
        (
            model
            for model in _provider_models_list(result[1])
            if _model_key_matches(str(model.get("id") or ""), model_name)
        ),
        None,
    )


def resolve_provider_for_model(
    model_name: str, catalog_entries: list[PiCatalogEntry]
) -> str:
    """Official channel for a model name; falls back to the first match."""
    name = model_name.strip()
    if not name:
        return ""
    family = _normalize_family(name)
    official = OFFICIAL_PROVIDERS.get(family) if family else None
    candidates = [
        entry for entry in catalog_entries if _model_key_matches(entry.model_id, name)
    ]
    if official:
        for entry in candidates:
            if entry.provider == official:
                return official
    candidates.sort(key=lambda entry: (_official_rank(entry.provider), entry.provider))
    return candidates[0].provider if candidates else ""


def build_group_pi_config_json(
    model_names: list[str],
    catalog_entries: list[PiCatalogEntry],
) -> str:
    """Generate the pi config for a group: the official channel's matched
    model definition (its models[0]), as a bare model object.

    The provider is resolved from the model family (gpt -> openai,
    claude -> anthropic, ...) at generation/export time.
    """
    for model_name in model_names:
        name = model_name.strip()
        if not name:
            continue
        provider = resolve_provider_for_model(name, catalog_entries)
        if not provider:
            continue
        for entry in catalog_entries:
            if entry.provider != provider:
                continue
            if not _model_key_matches(entry.model_id, name):
                continue
            model = _matched_model(entry, name)
            if model is not None:
                return json.dumps(model, indent=2, ensure_ascii=False)
    return "{}"


def _provider_api(provider: str, catalog_entries: list[PiCatalogEntry]) -> str:
    for entry in catalog_entries:
        if entry.provider != provider:
            continue
        return entry.api
    return ""


def _model_with_api(
    model: dict[str, object],
    provider: str,
    catalog_entries: list[PiCatalogEntry],
) -> dict[str, object]:
    """Inject the provider api into the model when it differs from the
    pi default (openai-responses) and the model does not set one."""
    if "api" in model:
        return model
    api = _provider_api(provider, catalog_entries)
    if api and api != "openai-responses":
        return {**model, "api": api}
    return model


def collect_group_models(
    groups: list[object],
    catalog_entries: list[PiCatalogEntry],
    allow_group: Callable[[object], bool] | None = None,
) -> list[dict[str, object]]:
    """Flatten per-group pi configs into a bare models list.

    Each group contributes the model definition from its pi config (or
    generated from the catalog by group name), with the provider api
    injected when it differs from the pi default. Legacy provider-wrapped
    configs are flattened as well.
    """
    models: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    def add_model(model: object, provider: str, override_id: str = "") -> None:
        if not isinstance(model, dict) or not model.get("id"):
            return
        if override_id:
            # Route groups export under their own name/id while keeping
            # the target group's model definition.
            model = {**model, "id": override_id, "name": override_id}
        model_id = str(model["id"])
        if model_id in seen_ids:
            return
        seen_ids.add(model_id)
        models.append(_model_with_api(model, provider, catalog_entries))

    groups_by_id = {getattr(g, "id", ""): g for g in groups}

    for group in groups:
        if allow_group is not None and not allow_group(group):
            continue
        # Route groups inherit the config of their target execution group
        # but export under their own name/id.
        target = group
        route_group_id = getattr(group, "route_group_id", "").strip()
        override_id = getattr(group, "name", "") if route_group_id else ""
        if route_group_id:
            target = groups_by_id.get(route_group_id)
            if target is None:
                continue
        if not any(
            getattr(item, "enabled", False) for item in getattr(target, "items", [])
        ):
            # Only groups with at least one enabled channel are exported.
            continue
        group_name = getattr(target, "name", "") or ""
        config_json = getattr(target, "pi_config", "").strip()
        if config_json:
            try:
                payload = json.loads(config_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if "id" in payload:
                # Bare model object.
                add_model(
                    payload,
                    resolve_provider_for_model(group_name, catalog_entries),
                    override_id,
                )
                continue
            if "model" in payload:
                # Legacy format: {"provider": ..., "model": {...}}
                add_model(
                    payload.get("model"),
                    str(payload.get("provider") or ""),
                    override_id,
                )
                continue
            if "providers" in payload:
                # Legacy format: {"providers": {...}}
                for provider_id, provider_payload in payload["providers"].items():
                    if not isinstance(provider_payload, dict):
                        continue
                    for model in provider_payload.get("models", []):
                        add_model(model, str(provider_id), override_id)
                continue
            continue
        if not group_name.strip():
            continue
        try:
            generated = json.loads(
                build_group_pi_config_json([group_name], catalog_entries)
            )
        except json.JSONDecodeError:
            continue
        add_model(
            generated if isinstance(generated, dict) else None,
            resolve_provider_for_model(group_name, catalog_entries),
            override_id,
        )
    return models
