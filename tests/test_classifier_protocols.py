import asyncio
import copy
import json
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any

import httpx
import pytest

from lens_api.core.config import Settings
from lens_api.gateway.converters import convert_request, convert_response
from lens_api.gateway.converters.classifier_protocols import classifier_context
from lens_api.gateway.service.proxy_upstream import _send_upstream
from lens_api.gateway.upstreams import build_upstream_request
from lens_api.models import ChannelConfig, ChannelKeyItem, ProtocolKind

P = ProtocolKind
PROTOCOLS = [
    P.TYPESAFE_SYSTEM_ONE,
    P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
    P.OPENAI_DECISIONS,
]
QUESTIONS = {
    "pick": {
        "type": "choice",
        "instructions": "Pick",
        "criteria": {"cat": "Cat", "dog": "Dog"},
    },
    "rate": {"type": "score", "instructions": "Rate", "criteria": ["Low", "High"]},
    "yes": {
        "type": "noul",
        "instructions": "True?",
        "criteria": {"true": "Present", "false": "Absent"},
    },
}


def request(protocol: P) -> dict[str, Any]:
    if protocol == P.OPENAI_DECISIONS:
        return {
            "model": "client",
            "input": '{"text":"猫"}',
            "questions": [
                {
                    "type": "choice",
                    "name": "pick",
                    "instructions": "Pick",
                    "choices": [
                        {"value": "cat", "description": "Cat"},
                        {"value": "dog", "description": "Dog"},
                    ],
                },
                {
                    "type": "score",
                    "name": "rate",
                    "instructions": "Rate",
                    "levels": [{"label": "Low"}, {"label": "High"}],
                },
                {"type": "predicate", "name": "yes", "instructions": "True?"},
            ],
        }
    body = {"state": {"text": "猫"}, "questions": copy.deepcopy(QUESTIONS)}
    return (
        {"model": "client", "input": body}
        if protocol == P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE
        else {"model": "client", **body}
    )


def response(protocol: P) -> dict[str, Any]:
    answers = {
        "pick": {
            "type": "choice",
            "choice": "cat",
            "probabilities": {"cat": 0.8, "dog": 0.2},
            "confidence": 0.6,
        },
        "rate": {"type": "score", "score": 0.75, "confidence": 0.5},
        "yes": {"type": "noul", "noul": 0.9},
    }
    if protocol == P.LLAMA_CPP_CLASSIFY:
        answers["yes"] = {"type": "bool", "probability": 0.9}
    usage = {"input_tokens": 12, "output_tokens": 3}
    if protocol == P.OPENAI_DECISIONS:
        return {
            "answers": [
                {
                    "name": "pick",
                    **answers["pick"],
                    "probabilities": [
                        {"value": "cat", "probability": 0.8},
                        {"value": "dog", "probability": 0.2},
                    ],
                },
                {"name": "rate", **answers["rate"]},
                {"name": "yes", "type": "predicate", "probability": 0.9},
            ],
            "usage": usage,
        }
    output = {"answers": answers, "usage": usage}
    return (
        {"success": True, "result": {"state": "Completed", "result": output}}
        if protocol == P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE
        else output
    )


@pytest.mark.parametrize("client", PROTOCOLS)
@pytest.mark.parametrize("upstream", [*PROTOCOLS, P.LLAMA_CPP_CLASSIFY])
def test_classifier_request_and_response_matrix(client: P, upstream: P) -> None:
    original = request(client)
    snapshot = copy.deepcopy(original)
    converted = convert_request(client, upstream, original, "upstream")
    assert converted["model"] == "upstream"
    assert original == snapshot
    if client != upstream:
        normalized = classifier_context(upstream, converted)
        assert normalized["state"] == {"text": "猫"}
        assert set(normalized["questions"]) == {"pick", "rate", "yes"}
        if upstream == P.LLAMA_CPP_CLASSIFY:
            assert converted["_lens_classifier"] is True
    result = json.loads(
        convert_response(
            client,
            upstream,
            json.dumps(response(upstream)).encode(),
            "client",
            request_body=original,
        )
    )
    if client == P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        output = result["result"]
        if "state" in output:
            output = output["result"]
    else:
        output = result
    if client == P.OPENAI_DECISIONS:
        by_name = {a["name"]: a for a in output["answers"]}
        assert by_name["yes"]["probability"] == 0.9
        assert by_name["pick"]["probabilities"] == [
            {"value": "cat", "probability": 0.8},
            {"value": "dog", "probability": 0.2},
        ]
    else:
        assert output["answers"]["yes"] == {"type": "noul", "noul": 0.9}
        assert output["answers"]["pick"]["probabilities"] == {"cat": 0.8, "dog": 0.2}
    assert output["usage"] == {"input_tokens": 12, "output_tokens": 3}


def test_classifier_images_round_trip_and_reject_unrepresentable_options() -> None:
    body = request(P.OPENAI_DECISIONS)
    body["input"] = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": '{"text":"猫"}'},
                {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
            ],
        }
    ]
    output = convert_request(P.OPENAI_DECISIONS, P.TYPESAFE_SYSTEM_ONE, body, "m")
    assert output["assets"] == {
        "<image-1>": {"type": "image", "format": "png", "data_base64": "YQ=="}
    }
    assert output["state"] == ["<image-1>", {"text": "猫"}]
    restored = convert_request(P.TYPESAFE_SYSTEM_ONE, P.OPENAI_DECISIONS, output, "m")
    assert restored["input"] == body["input"]
    clef = convert_request(
        P.OPENAI_DECISIONS,
        P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
        body,
        "@cf/cloudflare/clef",
    )
    assert clef["input"]["images"] == ["data:image/png;base64,YQ=="]
    with pytest.raises(ValueError, match="does not support images"):
        convert_request(P.OPENAI_DECISIONS, P.LLAMA_CPP_CLASSIFY, body, "m")
    body = request(P.TYPESAFE_SYSTEM_ONE)
    body["unknown_constraint"] = True
    with pytest.raises(ValueError, match="options"):
        convert_request(P.TYPESAFE_SYSTEM_ONE, P.OPENAI_DECISIONS, body, "m")
    assert (
        convert_request(P.TYPESAFE_SYSTEM_ONE, P.TYPESAFE_SYSTEM_ONE, body)[
            "unknown_constraint"
        ]
        is True
    )


@pytest.mark.parametrize("count", [5, 128, 129])
def test_decisions_image_limit_matches_pinned_reference(count: int) -> None:
    body = request(P.TYPESAFE_SYSTEM_ONE)
    body["assets"] = {
        f"<image-{i + 1}>": {"type": "image", "format": "png", "data_base64": "YQ=="}
        for i in range(count)
    }
    body["state"] = [*body["assets"], {"text": "cat"}]
    original = copy.deepcopy(body)
    if count > 128:
        with pytest.raises(ValueError, match="at most 128 images"):
            convert_request(P.TYPESAFE_SYSTEM_ONE, P.OPENAI_DECISIONS, body, "model")
    else:
        converted = convert_request(
            P.TYPESAFE_SYSTEM_ONE, P.OPENAI_DECISIONS, body, "model"
        )
        content = converted["input"][0]["content"]
        assert len(content) == count + 1
        assert json.loads(content[0]["text"]) == {"text": "cat"}
        assert all(
            part == {"type": "input_image", "image_url": "data:image/png;base64,YQ=="}
            for part in content[1:]
        )
    assert body == original


@pytest.mark.parametrize("state", [[1], [1, 2], [], None, {"images": []}])
def test_classifier_state_is_not_reinterpreted_without_image_parts(state: Any) -> None:
    body = request(P.TYPESAFE_SYSTEM_ONE)
    body["state"] = state
    output = convert_request(P.TYPESAFE_SYSTEM_ONE, P.OPENAI_DECISIONS, body, "m")
    assert json.loads(output["input"]) == state


@pytest.mark.parametrize("bad", [True, float("inf"), -1, 2])
def test_classifier_response_rejects_invalid_probabilities(bad: Any) -> None:
    body = response(P.OPENAI_DECISIONS)
    body["answers"][-1]["probability"] = bad
    with pytest.raises(ValueError):
        convert_response(
            P.TYPESAFE_SYSTEM_ONE,
            P.OPENAI_DECISIONS,
            json.dumps(body).encode(),
            request_body=request(P.TYPESAFE_SYSTEM_ONE),
        )


def test_classifier_response_rejects_failed_cloudflare_or_missing_answers() -> None:
    with pytest.raises(ValueError, match="failed"):
        convert_response(
            P.TYPESAFE_SYSTEM_ONE,
            P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
            b'{"success":false}',
            request_body=request(P.TYPESAFE_SYSTEM_ONE),
        )
    body = response(P.OPENAI_DECISIONS)
    body["answers"] = body["answers"][:-1]
    with pytest.raises(ValueError, match="Answer"):
        convert_response(
            P.TYPESAFE_SYSTEM_ONE,
            P.OPENAI_DECISIONS,
            json.dumps(body).encode(),
            request_body=request(P.TYPESAFE_SYSTEM_ONE),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["success", "missing", "shared", "http_error", "multi_token"]
)
async def test_llama_classifier_uses_real_multistep_requests(mode: str) -> None:
    source = {
        "model": "client",
        "state": {"text": "猫"},
        "questions": {"q": QUESTIONS["yes"]},
    }
    converted = convert_request(
        P.TYPESAFE_SYSTEM_ONE, P.LLAMA_CPP_CLASSIFY, source, "actual"
    )
    channel = ChannelConfig(
        id="llama",
        name="llama",
        protocol=P.LLAMA_CPP_CLASSIFY,
        base_url="https://llama.test/v1",
        api_key="test-token",
        keys=[ChannelKeyItem(id="k", key="test-token")],
    )
    upstream = build_upstream_request(channel, converted, Settings())
    assert upstream.url.endswith("/classify")
    seen = []
    closed = []

    class TrackedStream(httpx.AsyncByteStream):
        def __init__(self, body: dict[str, Any]) -> None:
            self.body = json.dumps(body).encode()

        async def __aiter__(self):
            yield self.body

        async def aclose(self) -> None:
            closed.append(True)

    async def handler(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        assert payload["model"] == "actual"
        assert "_lens_classifier" not in payload
        assert req.headers["authorization"] == "Bearer test-token"
        seen.append((req.url.path, payload))
        operation = req.url.path.rsplit("/", 1)[-1]
        assert "/v1/" not in req.url.path
        if operation == "tokenize":
            content = payload["content"]
            tokens = [1] if content == "\n" else [1, 10 if "Yes" in content else 20]
            if not content.startswith("\n"):
                tokens = tokens[-1:]
            if mode == "shared" and content != "\n":
                tokens[-1] = 10
            if mode == "multi_token" and content != "\n":
                tokens.extend([30, 31])
            data = {"tokens": tokens}
        elif operation == "apply-template":
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
            assert "Yes means: Present" in payload["messages"][1]["content"]
            data = {"prompt": "rendered<think>"}
        else:
            assert operation == "completion"
            assert payload["prompt"] == "rendered<think></think>"
            assert payload["n_predict"] == 1 and payload["post_sampling_probs"] is False
            if mode == "http_error":
                return httpx.Response(
                    429, stream=TrackedStream({"error": "rate limit"})
                )
            entries = [{"id": 10, "logprob": math.log(0.8)}]
            if mode != "missing" and payload["n_probs"] >= 4096:
                entries.append({"id": 20, "logprob": math.log(0.2)})
            data = {
                "completion_probabilities": [{"top_logprobs": entries}],
                "timings": {"prompt_n": 5, "predicted_n": 1},
            }
        return httpx.Response(200, stream=TrackedStream(data))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as client:
        if mode == "success":
            result = await _send_upstream(
                client,
                upstream,
                body_bytes=json.dumps(upstream.json_body).encode(),
                timeout=httpx.Timeout(2),
            )
            assert result.json()["answers"]["q"]["probability"] == pytest.approx(0.8)
            assert result.json()["usage"] == {"input_tokens": 10, "output_tokens": 2}
            restored = json.loads(
                convert_response(
                    P.TYPESAFE_SYSTEM_ONE,
                    P.LLAMA_CPP_CLASSIFY,
                    result.content,
                    request_body=source,
                )
            )
            assert restored["answers"]["q"]["noul"] == pytest.approx(0.8)
            assert [
                p["n_probs"] for path, p in seen if path.endswith("completion")
            ] == [256, 4096]
        else:
            with pytest.raises(
                httpx.HTTPStatusError if mode == "http_error" else ValueError
            ):
                await _send_upstream(
                    client,
                    upstream,
                    body_bytes=json.dumps(upstream.json_body).encode(),
                    timeout=httpx.Timeout(2),
                )
    assert len(closed) == len(seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_protocol", PROTOCOLS)
@pytest.mark.parametrize("upstream_protocol", [*PROTOCOLS, P.LLAMA_CPP_CLASSIFY])
async def test_classifier_full_target_flow_retains_original_request_context(
    monkeypatch: pytest.MonkeyPatch, client_protocol: P, upstream_protocol: P
) -> None:
    from lens_api.gateway.service import proxy_flow, proxy_upstream
    from lens_api.gateway.service.runtime_context import _RequestDeadline
    from time import perf_counter

    original = request(client_protocol)
    channel = ChannelConfig(
        id="c",
        name="C",
        protocol=upstream_protocol,
        base_url="https://provider.test",
        api_key="test-token",
        keys=[ChannelKeyItem(id="k", key="test-token")],
    )
    target = SimpleNamespace(
        channel=channel,
        model_name="actual",
        credential_id="k",
        credential_name="Key",
        probe_owner=None,
    )
    plan = SimpleNamespace(
        requested_group=None,
        resolved_group=None,
        execution_group=None,
        requested_group_name="client",
        resolved_group_name="client",
    )
    log = SimpleNamespace(update=AsyncMock(), attempts=[], request_id="r")
    next_token = 100
    tokens = {}
    seen = []

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal next_token
        body = json.loads(req.content)
        seen.append((req.url.path, body))
        if upstream_protocol != P.LLAMA_CPP_CLASSIFY:
            return httpx.Response(200, json=response(upstream_protocol))
        if req.url.path == "/tokenize":
            content = body["content"]
            if content == "\n":
                return httpx.Response(200, json={"tokens": [1]})
            label = content.lstrip("\n")
            if label not in tokens:
                tokens[label] = next_token
                next_token += 1
            return httpx.Response(
                200,
                json={
                    "tokens": (
                        [1, tokens[label]]
                        if content.startswith("\n")
                        else [tokens[label]]
                    )
                },
            )
        if req.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "prompt"})
        assert req.url.path == "/completion"
        return httpx.Response(
            200,
            json={
                "completion_probabilities": [
                    {
                        "top_logprobs": [
                            {"id": value, "logprob": -i}
                            for i, value in enumerate(tokens.values())
                        ]
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    ) as http:
        monkeypatch.setattr(
            proxy_upstream, "_resolve_http_client", lambda _: (http, False)
        )
        monkeypatch.setattr(
            proxy_upstream, "_safe_estimate_cost", AsyncMock(return_value=(0, 0, 0))
        )
        monkeypatch.setattr(
            proxy_upstream.app_state.router,
            "record_success",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            proxy_flow,
            "relay_log_capture_flags",
            lambda _: (False, False, False, False, True, True, True),
        )
        monkeypatch.setattr(
            proxy_flow, "_response_headers_for_log", lambda *args, **kwargs: {}
        )
        result = await proxy_flow._try_target(
            target=target,
            protocol=client_protocol,
            body=original,
            runtime={"relay_log_debug_mode": False, "proxy_url": ""},
            upstream_user_agent="test",
            inbound_headers=None,
            plan=plan,
            log_ctx=log,
            errors=[],
            failure_status_codes=[],
            deadline=_RequestDeadline(
                started_at=perf_counter(),
                first_token_timeout_seconds=5,
                stream_idle_timeout_seconds=5,
            ),
            route_release=lambda: None,
        )
    assert result is not None and result.status_code == 200
    assert log.attempts[0].success
    assert "pick" in result.body.decode()
    assert all("_lens_classifier" not in body for _, body in seen)
    if upstream_protocol == P.LLAMA_CPP_CLASSIFY:
        assert {path for path, _ in seen} == {
            "/tokenize",
            "/apply-template",
            "/completion",
        }


@pytest.mark.asyncio
async def test_llama_classifier_cancellation_closes_inflight_body() -> None:
    converted = convert_request(
        P.TYPESAFE_SYSTEM_ONE,
        P.LLAMA_CPP_CLASSIFY,
        request(P.TYPESAFE_SYSTEM_ONE),
        "actual",
    )
    channel = ChannelConfig(
        id="llama",
        name="llama",
        protocol=P.LLAMA_CPP_CLASSIFY,
        base_url="https://llama.test",
        api_key="key",
        keys=[ChannelKeyItem(id="k", key="key")],
    )
    upstream = build_upstream_request(channel, converted, Settings())
    closed = asyncio.Event()
    entered = asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"{}"

        async def aclose(self) -> None:
            closed.set()

    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(
            _send_upstream(
                client,
                upstream,
                body_bytes=json.dumps(upstream.json_body).encode(),
                timeout=httpx.Timeout(1),
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
