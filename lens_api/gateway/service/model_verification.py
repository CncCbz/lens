from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from ...models import (
    ModelGroup,
    ModelVerificationCandidate,
    ModelVerificationRequest,
    ModelVerificationResult,
    ModelVerificationSample,
    PlaygroundTarget,
)
from ...persistence.verification_store import verification_models_match
from .proxy_flow import PLAYGROUND_GATEWAY_KEY_ID, _proxy_protocol
from .runtime_context import (
    UTC,
    GatewayApiKey,
    ProtocolKind,
    StreamingResponse,
    app_state,
    datetime,
)

logger = logging.getLogger(__name__)

# Only these protocols can carry the challenge: they return plain text.
SUPPORTED_PROTOCOLS = frozenset(
    {
        ProtocolKind.OPENAI_CHAT,
        ProtocolKind.OPENAI_RESPONSES,
        ProtocolKind.ANTHROPIC,
        ProtocolKind.GEMINI,
    }
)

_SAMPLE_STATES = frozenset({"complete", "truncated", "failed", "cancelled"})

_DETECTOR_LOCK = asyncio.Lock()
_DETECTOR: Any = None
_DETECTOR_BUILT_AT = ""
_DETECTOR_FAILED_AT = 0.0
_DETECTOR_RETRY_SECONDS = 300.0


def _verification_gateway_key() -> GatewayApiKey:
    now = datetime.now(UTC).isoformat()
    return GatewayApiKey(
        id=PLAYGROUND_GATEWAY_KEY_ID,
        api_key="",
        remark="verification",
        enabled=True,
        created_at=now,
        updated_at=now,
    )


async def _load_detector() -> tuple[Any, str]:
    """Load the reference bank once; a failure is retried after a cooldown."""
    global _DETECTOR, _DETECTOR_BUILT_AT, _DETECTOR_FAILED_AT
    if _DETECTOR is not None:
        return _DETECTOR, _DETECTOR_BUILT_AT
    async with _DETECTOR_LOCK:
        if _DETECTOR is not None:
            return _DETECTOR, _DETECTOR_BUILT_AT
        if time.monotonic() - _DETECTOR_FAILED_AT < _DETECTOR_RETRY_SECONDS:
            raise RuntimeError("The reference bank is unavailable; retry later")
        try:
            import lmfpd
        except ImportError as exc:
            _DETECTOR_FAILED_AT = time.monotonic()
            raise RuntimeError(
                "The verifier is not installed; install the lmfpd package"
            ) from exc
        try:
            # Loading parses several MB, so it must stay off the event loop.
            detector = await asyncio.to_thread(lmfpd.load)
        except Exception as exc:
            _DETECTOR_FAILED_AT = time.monotonic()
            logger.warning("Failed to load the verification reference bank: %s", exc)
            raise RuntimeError(f"Failed to load the reference bank: {exc}") from exc
        _DETECTOR = detector
        _DETECTOR_BUILT_AT = str(getattr(detector, "built_at", ""))
        return _DETECTOR, _DETECTOR_BUILT_AT


def _generate_challenges(count: int) -> list[dict[str, Any]]:
    import lmfpd

    return list(lmfpd.generate_challenges(count))


def _challenge_body(
    protocol: ProtocolKind,
    group_name: str,
    prompt: str,
    reasoning_effort: str,
    expected_count: int,
) -> dict[str, Any]:
    """The body the gateway forwards.

    `model` is the routing key, so it carries the model group name, not the
    upstream model name: the group is what resolves to a route target. The
    forced target then pins which member of that group is used.

    The budget covers the answer plus whatever a thinking model spends before
    writing it; too small a budget leaves the reasoning unfinished and the
    answer empty.
    """
    # A thinking model can burn the whole budget before writing anything, so
    # the answer length is only a floor: reasoning runs on top of it.
    budget = max(expected_count * _BUDGET_PER_ANSWER, _MIN_BUDGET)
    if protocol == ProtocolKind.OPENAI_CHAT:
        body: dict[str, Any] = {
            "model": group_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": budget,
        }
    elif protocol == ProtocolKind.OPENAI_RESPONSES:
        body = {
            "model": group_name,
            "input": prompt,
            "stream": True,
            "max_output_tokens": budget,
        }
    elif protocol == ProtocolKind.ANTHROPIC:
        body = {
            "model": group_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": budget,
        }
    elif protocol == ProtocolKind.GEMINI:
        body = {
            "model": group_name,
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "stream": True,
        }
    else:
        raise ValueError(f"Protocol {protocol.value} cannot be verified")
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    return body


_TEXT_KEYS = ("choices", "content", "candidates", "output")


def _response_text(payload: Any) -> str:
    """Best-effort text extraction across the four verifiable protocols."""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list):
        parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            parts.append(item["text"])
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
            if isinstance(choice.get("text"), str):
                parts.append(choice["text"])
        if parts:
            return "".join(parts)
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    output = payload.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
        if parts:
            return "".join(parts)
    content = payload.get("content")
    if isinstance(content, list):
        parts = [
            str(item.get("text"))
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and item.get("text")
        ]
        if parts:
            return "\n".join(parts)
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        parts = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            item_content = candidate.get("content")
            if not isinstance(item_content, dict):
                continue
            for part in item_content.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        if parts:
            return "".join(parts)
    return ""


def _response_model(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("model", "modelVersion", "model_version"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _decode_chunk(chunk: bytes) -> str:
    try:
        return chunk.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _collect_sse_text(raw: str) -> tuple[str, str | None]:
    """Return the answer text and the model the upstream reported."""
    parts: list[str] = []
    model: str | None = None
    for block in re.split(r"\r?\n\r?\n", raw):
        data_lines = [
            line[5:].strip() for line in block.splitlines() if line.startswith("data:")
        ]
        if not data_lines:
            continue
        joined = "\n".join(line for line in data_lines if line and line != "[DONE]")
        if not joined:
            continue
        try:
            payload = json.loads(joined)
        except json.JSONDecodeError:
            continue
        model = model or _response_model(payload)
        parts.append(_response_text(payload))
    return "".join(parts), model


async def _drain_response(response: Any) -> tuple[str, str | None, int | None]:
    """Read a gateway response, streaming or not, into plain text."""
    if isinstance(response, StreamingResponse):
        # Consume through the ASGI interface: that is what runs the response's
        # finalizer, which settles the request log and releases the channel.
        chunks: list[str] = []
        first_byte_ms: int | None = None
        started = time.perf_counter()

        async def send(message: dict[str, Any]) -> None:
            nonlocal first_byte_ms
            if message.get("type") != "http.response.body":
                return
            body = message.get("body") or b""
            if not body:
                return
            if first_byte_ms is None:
                first_byte_ms = int((time.perf_counter() - started) * 1000)
            chunks.append(
                body if isinstance(body, str) else body.decode("utf-8", "ignore")
            )

        async def receive() -> dict[str, Any]:
            # Never report a disconnect: the response cancels this wait itself
            # once the stream is finished, and a disconnect would end it early.
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 0),
            "scheme": "http",
        }
        await response(scope, receive, send)
        text, model = _collect_sse_text("".join(chunks))
        return text, model, first_byte_ms

    body = getattr(response, "body", b"")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "", None, None
    return _response_text(payload), _response_model(payload), None


async def _probe_challenge(
    *,
    protocol: ProtocolKind,
    target: PlaygroundTarget,
    group_name: str,
    prompt: str,
    expected_count: int,
    reasoning_effort: str,
) -> tuple[ModelVerificationSample, str, str]:
    body = _challenge_body(
        protocol, group_name, prompt, reasoning_effort, expected_count
    )
    try:
        response = await _proxy_protocol(
            protocol,
            body,
            _verification_gateway_key(),
            None,
            None,
            forced_target=target,
        )
    except Exception as exc:
        logger.warning("Verification request failed: %s", exc)
        return (
            ModelVerificationSample(
                state="failed",
                expected_count=expected_count,
                error=f"{type(exc).__name__}: {exc}",
            ),
            "",
            "",
        )
    status_code = getattr(response, "status_code", 0)
    if status_code >= 400:
        detail = ""
        body_bytes = getattr(response, "body", b"")
        if body_bytes:
            detail = body_bytes.decode("utf-8", errors="ignore")[:400]
        return (
            ModelVerificationSample(
                state="failed",
                expected_count=expected_count,
                error=f"HTTP {status_code}: {detail}".strip(),
            ),
            "",
            "",
        )
    text, response_model, first_byte_ms = await _drain_response(response)
    import lmfpd

    count = len(lmfpd.parse_numbers(text))
    state = "complete" if count >= expected_count else "truncated"
    if count <= 0:
        state = "failed"
    return (
        ModelVerificationSample(
            state=state,
            count=count,
            expected_count=expected_count,
            first_byte_ms=first_byte_ms,
            response_model=response_model,
            error=None if count > 0 else "No integers in the answer",
        ),
        text,
        str(getattr(response, "headers", {}).get("x-lens-request-id") or ""),
    )


def _resolve_channel_id(protocol_config_id: str, protocol: ProtocolKind) -> str:
    return f"{protocol_config_id}_{protocol.value}"


async def _verification_protocol(
    target: dict[str, str],
) -> tuple[ProtocolKind, str, ModelGroup]:
    """Find the runtime channel the target belongs to and the group routing it."""
    channels = await app_state.channel_store.list()
    for channel in channels:
        if channel.id != target["protocol_config_id"] and not channel.id.startswith(
            f"{target['protocol_config_id']}_"
        ):
            continue
        if not any(
            model.model_name == target["model_name"]
            and model.credential_id == target["credential_id"]
            for model in channel.models
        ):
            continue
        group = await _group_for_channel(channel.id, target)
        return channel.protocol, channel.id, group
    raise LookupError("The selected channel and model are not part of this model group")


async def _group_for_channel(channel_id: str, target: dict[str, str]) -> ModelGroup:
    groups = await app_state.group_repo.list_groups()
    for group in groups:
        if group.route_group_id.strip():
            continue
        for item in group.items:
            if (
                item.enabled
                and item.channel_id == channel_id
                and item.credential_id == target["credential_id"]
                and item.model_name == target["model_name"]
            ):
                return group
    raise LookupError("No model group routes to the selected channel and model")


def _wire_effort(group: ModelGroup, level: str) -> str:
    """Translate a level into the spelling the group declares for this model.

    ``thinkingLevelMap`` pins each level: a value is the wire spelling, null
    means unsupported. Without a map the level is sent unchanged.
    """
    normalized = level.strip().lower()
    if not normalized:
        return ""
    try:
        config = json.loads(group.pi_config or "{}")
    except json.JSONDecodeError:
        return normalized
    if not isinstance(config, dict):
        return normalized
    mapping = config.get("thinkingLevelMap")
    if not isinstance(mapping, dict):
        return normalized
    # "none" is the disable switch: the group declares it under "off", and a
    # null there means nothing goes on the wire rather than an unsupported level.
    if normalized == "none":
        off = mapping.get("off")
        return "" if off is None else str(off).strip()
    if normalized not in mapping:
        return normalized
    wire = mapping.get(normalized)
    if wire is None:
        raise ValueError(
            f"Model group {group.name} does not support thinking level {normalized}"
        )
    return str(wire).strip() or normalized


# A model that stops early or spends its budget on reasoning yields too few
# numbers; a fresh attempt usually completes, so retry before giving up.
# Reasoning is unbounded in practice, so leave room for several times the
# answer length before the model is cut off mid-thought.
_BUDGET_PER_ANSWER = 32
_MIN_BUDGET = 8192

_MIN_VALID_SHARE = 0.55
_PROBE_ATTEMPTS = 3


def _min_numbers(expected_count: int) -> int:
    return max(80, int(expected_count * _MIN_VALID_SHARE + 0.999999))


async def _probe_until_scorable(
    *,
    protocol: ProtocolKind,
    target: PlaygroundTarget,
    group_name: str,
    challenge: dict[str, Any],
    reasoning_effort: str,
) -> tuple[ModelVerificationSample, str, str]:
    expected_count = int(challenge["expected_count"])
    minimum = _min_numbers(expected_count)
    best: tuple[ModelVerificationSample, str, str] | None = None
    for attempt in range(_PROBE_ATTEMPTS):
        probe = await _probe_challenge(
            protocol=protocol,
            target=target,
            group_name=group_name,
            prompt=str(challenge["prompt"]),
            expected_count=expected_count,
            reasoning_effort=reasoning_effort,
        )
        if best is None or probe[0].count > best[0].count:
            best = probe
        if probe[0].count >= minimum:
            return probe
        # A failed request will not fix itself; only a short answer is worth
        # another go.
        if probe[0].state == "failed" and attempt > 0:
            break
    assert best is not None
    return best


async def run_verification(
    payload: ModelVerificationRequest,
    trigger: str = "manual",
) -> ModelVerificationResult:
    target = payload.target.model_dump()
    started = time.perf_counter()
    protocol, channel_id, group = await _verification_protocol(target)
    wire_effort = _wire_effort(group, payload.reasoning_effort)
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"Protocol {protocol.value} cannot be verified; "
            "only text protocols return a fingerprint"
        )

    detector, built_at = await _load_detector()
    challenges = _generate_challenges(payload.count)
    forced = PlaygroundTarget(
        channel_id=channel_id,
        model_name=target["model_name"],
        credential_id=target["credential_id"],
    )
    probes = await asyncio.gather(
        *(
            _probe_until_scorable(
                protocol=protocol,
                target=forced,
                group_name=group.name,
                challenge=challenge,
                reasoning_effort=wire_effort,
            )
            for challenge in challenges
        )
    )
    samples = [sample for sample, _text, _rid in probes]
    texts = [text for _sample, text, _rid in probes]
    request_id = next((rid for _s, _t, rid in probes if rid), "")
    scorable = [
        {"text": text, "expected_count": sample.expected_count}
        for text, sample in zip(texts, samples)
        if text.strip() and sample.count > 0
    ]

    decision = "unscorable"
    prediction = ""
    prediction_name = ""
    family_prediction = ""
    family_prediction_name = ""
    ranking_score: float | None = None
    probability: float | None = None
    probability_status = ""
    used_outputs = 0
    model_like: bool | None = None
    candidates: list[ModelVerificationCandidate] = []
    error_message = ""

    if scorable:
        try:
            analysis = await asyncio.to_thread(
                detector.analyze, scorable, payload.strict
            )
        except Exception as exc:
            error_message = f"Scoring failed: {exc}"
        else:
            decision = str(analysis.get("decision") or "")
            prediction = str(analysis.get("prediction") or "")
            prediction_name = str(analysis.get("prediction_name") or "")
            family_prediction = str(analysis.get("family_prediction") or "")
            family_prediction_name = str(analysis.get("family_prediction_name") or "")
            ranking_score = analysis.get("ranking_score")
            probability = analysis.get("probability")
            probability_status = str(analysis.get("probability_status") or "")
            used_outputs = int(analysis.get("used_outputs") or 0)
            sequence = analysis.get("sequence")
            if isinstance(sequence, dict):
                model_like = bool(sequence.get("model_like"))
            candidates = [
                ModelVerificationCandidate(
                    model=str(item.get("model") or ""),
                    display_name=str(item.get("display_name") or ""),
                    family=str(item.get("family") or ""),
                    family_name=str(item.get("family_name") or ""),
                    score=float(item.get("score") or 0.0),
                    probability=item.get("probability"),
                )
                for item in analysis.get("results") or []
                if isinstance(item, dict)
            ]
            evidence = analysis.get("evidence")
            reason = ""
            if isinstance(evidence, dict):
                reason = str(evidence.get("reason") or "")
            if decision in {"unscorable", "not_model"} and reason:
                error_message = reason
    else:
        error_message = "No valid answers; every challenge failed"

    # The expected model defaults to the canonical execution group name (or
    # the channel model name when not overridden): "does the API return what
    # its canonical model claims" is the question this feature exists to answer.
    raw_expected = payload.expected_model.strip()
    raw_model_name = str(target["model_name"]).strip()
    canonical_group_name = group.name.strip()
    if (not raw_expected or raw_expected == raw_model_name) and canonical_group_name:
        expected = canonical_group_name
    else:
        expected = raw_expected or raw_model_name
    matches_expected: bool | None = None
    if expected and (prediction or prediction_name):
        matches_expected = verification_models_match(
            expected, prediction, prediction_name
        ) or verification_models_match(raw_model_name, prediction, prediction_name)

    result = await app_state.verification_store.save(
        target=target,
        channel_id=channel_id,
        protocol=protocol.value,
        reasoning_effort=payload.reasoning_effort,
        trigger=trigger,
        challenge_count=len(challenges),
        decision=decision,
        prediction=prediction,
        prediction_name=prediction_name,
        family_prediction=family_prediction,
        family_prediction_name=family_prediction_name,
        ranking_score=ranking_score,
        probability=probability,
        probability_status=probability_status,
        used_outputs=used_outputs,
        model_like=model_like,
        matches_expected=matches_expected,
        error_message=error_message,
        candidates=candidates,
        samples=samples,
        detector_built_at=built_at,
        request_id=request_id,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    # The expectation is derived, not stored: the column would only repeat
    # `model_name` for every row that never overrode it.
    return result.model_copy(update={"expected_model": expected})
