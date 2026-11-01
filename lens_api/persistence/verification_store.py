from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import (
    ModelVerificationCandidate,
    ModelVerificationLatest,
    ModelVerificationPage,
    ModelVerificationResult,
    ModelVerificationSample,
)
from .entities import SiteModelVerificationEntity

_GATEWAY_SUFFIX_RE = re.compile(
    r"-(?:free|paid|online|latest|high|medium|low|minimal|xhigh|max)$",
    re.IGNORECASE,
)
_DATE_SUFFIX_RE = re.compile(r"-(\d{4}|\d{8})$")
_SEPARATOR_RE = re.compile(r"[\s._-]+")


def _extract_model_identities(value: str | None) -> list[tuple[str, str, str]]:
    """Return (strict_norm, base_norm, date_suffix) tuples for a model name."""
    raw = (value or "").strip().lower()
    if not raw:
        return []
    seeds: list[str] = []
    if "/" in raw:
        tail = raw.rsplit("/", 1)[-1].strip()
        if tail:
            seeds.append(tail)
    seeds.append(raw)
    identities: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for seed in seeds:
        current = seed
        while current:
            stripped = _GATEWAY_SUFFIX_RE.sub("", current).strip("-._ ")
            if not stripped or stripped == current:
                break
            current = stripped
        strict = _SEPARATOR_RE.sub("-", current).strip("-")
        if not strict:
            continue
        date_match = _DATE_SUFFIX_RE.search(strict)
        if date_match:
            date_suffix = date_match.group(1)
            base = strict[: date_match.start()].strip("-") or strict
        else:
            date_suffix = ""
            base = strict
        item = (strict, base, date_suffix)
        if item not in seen:
            seen.add(item)
            identities.append(item)
    return identities


def normalize_verification_model_tokens(value: str | None) -> set[str]:
    tokens: set[str] = set()
    for strict, base, _ in _extract_model_identities(value):
        tokens.add(strict)
        tokens.add(base)
    return tokens


def verification_models_match(claimed: str | None, *candidates: str | None) -> bool:
    claimed_ids = _extract_model_identities(claimed)
    if not claimed_ids:
        return False
    for candidate in candidates:
        for cand_strict, cand_base, cand_date in _extract_model_identities(candidate):
            for claim_strict, claim_base, claim_date in claimed_ids:
                if claim_strict == cand_strict:
                    return True
                if claim_base == cand_base and (not claim_date or not cand_date):
                    return True
    return False


def _dump(items: list) -> str:
    return json.dumps(
        [item.model_dump() for item in items], ensure_ascii=False, default=str
    )


def _load_candidates(raw: str) -> list[ModelVerificationCandidate]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    items: list[ModelVerificationCandidate] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            items.append(ModelVerificationCandidate.model_validate(row))
        except ValueError:
            continue
    return items


def _load_samples(raw: str) -> list[ModelVerificationSample]:
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    items: list[ModelVerificationSample] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            items.append(ModelVerificationSample.model_validate(row))
        except ValueError:
            continue
    return items


def _to_result(
    entity: SiteModelVerificationEntity,
    site_name: str = "",
) -> ModelVerificationResult:
    claimed = entity.model_name
    matches_expected = (
        None if entity.matches_expected is None else bool(entity.matches_expected)
    )
    if entity.prediction or entity.prediction_name:
        matches_expected = verification_models_match(
            claimed, entity.prediction, entity.prediction_name
        )
    return ModelVerificationResult(
        id=entity.id,
        site_id=entity.site_id,
        site_name=site_name,
        channel_name="",
        protocol_config_id=entity.protocol_config_id,
        credential_id=entity.credential_id,
        model_name=entity.model_name,
        expected_model=claimed,
        expected_probability=_expected_probability(entity.results_json, claimed),
        channel_id=entity.channel_id,
        protocol=entity.protocol,
        reasoning_effort=entity.reasoning_effort,
        trigger=entity.trigger,
        challenge_count=entity.challenge_count,
        decision=entity.decision,
        prediction=entity.prediction,
        prediction_name=entity.prediction_name,
        family_prediction=entity.family_prediction,
        family_prediction_name=entity.family_prediction_name,
        ranking_score=entity.ranking_score,
        probability=entity.probability,
        probability_status=entity.probability_status,
        used_outputs=entity.used_outputs,
        model_like=(None if entity.model_like is None else bool(entity.model_like)),
        matches_expected=matches_expected,
        error_message=entity.error_message,
        results=_load_candidates(entity.results_json),
        samples=_load_samples(entity.samples_json),
        detector_built_at=entity.detector_built_at,
        request_id=entity.request_id,
        duration_ms=entity.duration_ms,
        created_at=entity.created_at.isoformat() if entity.created_at else "",
    )


def _expected_probability(results_json: str, *claimed_names: str) -> float | None:
    """Score the claimed model actually received.

    The stored top-1 probability belongs to whichever model won, so reusing it
    as the claim's confidence would report a mismatch as confident.
    """
    claims = [name.strip() for name in claimed_names if name and name.strip()]
    if not claims:
        return None
    try:
        rows = json.loads(results_json or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    for claimed in claims:
        claimed_ids = _extract_model_identities(claimed)
        if not claimed_ids:
            continue
        strict_dated = {strict for strict, _, date_suffix in claimed_ids if date_suffix}
        if strict_dated:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in ("model", "display_name"):
                    for cand_strict, _, _ in _extract_model_identities(
                        str(row.get(key) or "")
                    ):
                        if cand_strict in strict_dated:
                            probability = row.get("probability")
                            if probability is None:
                                return None
                            try:
                                return float(probability)
                            except (TypeError, ValueError):
                                return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if verification_models_match(
                claimed,
                str(row.get("model") or ""),
                str(row.get("display_name") or ""),
            ):
                probability = row.get("probability")
                if probability is None:
                    return None
                try:
                    return float(probability)
                except (TypeError, ValueError):
                    return None
    return None


class VerificationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(
        self,
        *,
        target: dict[str, str],
        channel_id: str,
        protocol: str,
        reasoning_effort: str,
        trigger: str,
        challenge_count: int,
        decision: str,
        prediction: str,
        prediction_name: str,
        family_prediction: str,
        family_prediction_name: str,
        ranking_score: float | None,
        probability: float | None,
        probability_status: str,
        used_outputs: int,
        model_like: bool | None,
        matches_expected: bool | None,
        error_message: str,
        candidates: list[ModelVerificationCandidate],
        samples: list[ModelVerificationSample],
        detector_built_at: str,
        request_id: str,
        duration_ms: int,
    ) -> ModelVerificationResult:
        entity = SiteModelVerificationEntity(
            id=uuid.uuid4().hex,
            site_id=target["site_id"],
            protocol_config_id=target["protocol_config_id"],
            credential_id=target["credential_id"],
            model_name=target["model_name"],
            channel_id=channel_id,
            protocol=protocol,
            reasoning_effort=reasoning_effort,
            trigger=trigger,
            challenge_count=challenge_count,
            decision=decision,
            prediction=prediction,
            prediction_name=prediction_name,
            family_prediction=family_prediction,
            family_prediction_name=family_prediction_name,
            ranking_score=ranking_score,
            probability=probability,
            probability_status=probability_status,
            used_outputs=used_outputs,
            model_like=None if model_like is None else int(model_like),
            matches_expected=(
                None if matches_expected is None else int(matches_expected)
            ),
            error_message=error_message,
            results_json=_dump(candidates),
            samples_json=_dump(samples),
            detector_built_at=detector_built_at,
            request_id=request_id,
            duration_ms=duration_ms,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        async with self._session_factory() as session:
            session.add(entity)
            await session.commit()
            return _to_result(entity)

    async def list(
        self,
        *,
        site_id: str | None = None,
        model_name: str | None = None,
        decision: str | None = None,
        trigger: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ModelVerificationPage:
        """One page of history, newest first.

        Filters are applied in SQL so the count matches what a page contains;
        filtering after the query would page over a set the caller never sees.
        """
        conditions = []
        if site_id:
            conditions.append(SiteModelVerificationEntity.site_id == site_id)
        if model_name:
            conditions.append(SiteModelVerificationEntity.model_name == model_name)
        if decision:
            conditions.append(SiteModelVerificationEntity.decision == decision)
        if trigger:
            conditions.append(SiteModelVerificationEntity.trigger == trigger)

        safe_limit = max(limit, 0)
        safe_offset = max(offset, 0)
        items_stmt = select(SiteModelVerificationEntity)
        count_stmt = select(func.count()).select_from(SiteModelVerificationEntity)
        for condition in conditions:
            items_stmt = items_stmt.where(condition)
            count_stmt = count_stmt.where(condition)
        items_stmt = (
            items_stmt.order_by(SiteModelVerificationEntity.created_at.desc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        async with self._session_factory() as session:
            entities = (await session.execute(items_stmt)).scalars().all()
            total = int((await session.execute(count_stmt)).scalar_one())
        return ModelVerificationPage(
            items=[_to_result(entity) for entity in entities],
            total=total,
            limit=safe_limit,
            offset=safe_offset,
        )

    async def latest_by_model(self) -> list[ModelVerificationLatest]:
        """Newest round per (protocol config, credential, model)."""
        async with self._session_factory() as session:
            entities = (
                (
                    await session.execute(
                        select(SiteModelVerificationEntity).order_by(
                            SiteModelVerificationEntity.created_at.desc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        seen: set[tuple[str, str, str]] = set()
        latest: list[ModelVerificationLatest] = []
        for entity in entities:
            key = (entity.protocol_config_id, entity.credential_id, entity.model_name)
            if key in seen:
                continue
            seen.add(key)
            claimed = entity.model_name
            matches_expected = (
                None
                if entity.matches_expected is None
                else bool(entity.matches_expected)
            )
            if entity.prediction or entity.prediction_name:
                matches_expected = verification_models_match(
                    claimed, entity.prediction, entity.prediction_name
                )
            latest.append(
                ModelVerificationLatest(
                    model_name=entity.model_name,
                    expected_model=claimed,
                    protocol_config_id=entity.protocol_config_id,
                    credential_id=entity.credential_id,
                    channel_id=entity.channel_id,
                    trigger=entity.trigger,
                    decision=entity.decision,
                    prediction=entity.prediction,
                    prediction_name=entity.prediction_name,
                    probability=entity.probability,
                    # Only the claimed name: including the prediction would
                    # resolve back to the winner and defeat the point.
                    expected_probability=_expected_probability(
                        entity.results_json, claimed
                    ),
                    matches_expected=matches_expected,
                    error_message=entity.error_message,
                    created_at=(
                        entity.created_at.isoformat() if entity.created_at else ""
                    ),
                )
            )
        return latest

    async def last_checked_at(self) -> dict[tuple[str, str, str], datetime]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        SiteModelVerificationEntity.protocol_config_id,
                        SiteModelVerificationEntity.credential_id,
                        SiteModelVerificationEntity.model_name,
                        SiteModelVerificationEntity.created_at,
                    ).order_by(SiteModelVerificationEntity.created_at.desc())
                )
            ).all()
        result: dict[tuple[str, str, str], datetime] = {}
        for protocol_config_id, credential_id, model_name, created_at in rows:
            key = (protocol_config_id, credential_id, model_name)
            if key not in result and created_at is not None:
                result[key] = created_at
        return result

    async def delete_for_model(
        self, *, protocol_config_id: str, credential_id: str, model_name: str
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(SiteModelVerificationEntity).where(
                    SiteModelVerificationEntity.protocol_config_id
                    == protocol_config_id,
                    SiteModelVerificationEntity.credential_id == credential_id,
                    SiteModelVerificationEntity.model_name == model_name,
                )
            )
            await session.commit()
