import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from lens_api.core import pi_catalog
from lens_api.gateway.service import admin_sites, model_verification, site_model_probe
from lens_api.gateway.service.upstream_http import _model_list_request
from lens_api.models import (
    ChannelConfig,
    ChannelKeyItem,
    ProtocolKind as P,
    SiteModelFetchRequest,
    SiteModelTestRequest,
)

NEW = [
    P.MISTRAL_CONVERSATIONS,
    P.AZURE_OPENAI_RESPONSES,
    P.OPENAI_CODEX_RESPONSES,
    P.GOOGLE_VERTEX,
    P.BEDROCK_CONVERSE_STREAM,
    P.PI_MESSAGES,
    P.OPENROUTER_IMAGES,
    P.TYPESAFE_SYSTEM_ONE,
    P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
    P.LLAMA_CPP_CLASSIFY,
    P.OPENAI_DECISIONS,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", NEW)
async def test_admin_discovery_preserves_selected_new_protocol(
    monkeypatch: pytest.MonkeyPatch, protocol: P
) -> None:
    captured = []

    async def discover(channel: ChannelConfig) -> list[str]:
        captured.append(channel)
        return ["test-model"]

    monkeypatch.setattr(
        admin_sites,
        "app_state",
        SimpleNamespace(
            channel_store=SimpleNamespace(
                fetch_models_preview=AsyncMock(
                    return_value=[{"credential_id": "k", "credential_name": "Key"}]
                )
            )
        ),
    )
    monkeypatch.setattr(admin_sites, "_fetch_upstream_models", discover)
    result = await admin_sites.fetch_site_models(
        SiteModelFetchRequest(
            protocol=protocol,
            base_url="https://provider.test",
            credential_id="k",
            credentials=[
                {"id": "k", "name": "Key", "api_key": "test-token", "base_url_id": "b"}
            ],
        )
    )
    assert captured[0].protocol == protocol
    assert result[0].model_name == "test-model"


@pytest.mark.parametrize("protocol", NEW)
def test_new_provider_model_discovery_uses_supported_endpoint_or_fails_explicitly(
    protocol: P,
) -> None:
    channel = ChannelConfig(
        id="c",
        name="C",
        protocol=protocol,
        base_url="https://provider.test",
        api_key="test-token",
        keys=[ChannelKeyItem(id="k", key="test-token")],
    )
    if protocol in {P.MISTRAL_CONVERSATIONS, P.OPENROUTER_IMAGES, P.LLAMA_CPP_CLASSIFY}:
        request = _model_list_request(channel)
        assert request["url"] == "https://provider.test/v1/models"
    else:
        with pytest.raises(
            HTTPException, match="configure channel models explicitly"
        ) as error:
            _model_list_request(channel)
        assert error.value.status_code == 400


@pytest.mark.parametrize("protocol", NEW)
def test_pi_catalog_exports_native_api_within_operation_family(protocol: P) -> None:
    api = next(
        name
        for name, value in pi_catalog._PI_API_PROTOCOLS.items()
        if value == protocol
    )
    model = {"id": "test", "api": api}
    exported = pi_catalog._model_with_api(model, "test", [], [protocol])
    assert exported is not None and exported["api"] == api
    incompatible = (
        P.OPENAI_IMAGE if protocol not in {P.OPENROUTER_IMAGES} else P.OPENAI_CHAT
    )
    assert pi_catalog._model_with_api(model, "test", [], [incompatible]) is None


@pytest.mark.parametrize(
    "protocol",
    [
        P.MISTRAL_CONVERSATIONS,
        P.AZURE_OPENAI_RESPONSES,
        P.OPENAI_CODEX_RESPONSES,
        P.GOOGLE_VERTEX,
        P.BEDROCK_CONVERSE_STREAM,
        P.PI_MESSAGES,
    ],
)
def test_verification_builds_native_request_for_all_added_chat_apis(
    protocol: P,
) -> None:
    body = model_verification._challenge_body(
        protocol, "group", "challenge", "high", 4, 1024
    )
    assert body["model"] == "group"
    assert "challenge" in json.dumps(body)
    assert body.get("stream") is True


@pytest.mark.parametrize(
    "protocol",
    [
        P.TYPESAFE_SYSTEM_ONE,
        P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
        P.OPENAI_DECISIONS,
        P.LLAMA_CPP_CLASSIFY,
    ],
)
def test_classifier_probe_builds_executable_request_and_checks_answers(
    protocol: P,
) -> None:
    request = SiteModelTestRequest(
        protocol=protocol,
        base_url="https://provider.test",
        credential={"id": "k", "api_key": "test-token"},
        model_name="test",
        prompt="hello",
    )
    body = site_model_probe._site_model_probe_body(request)
    assert body["model"] == "test"
    assert "hello" in json.dumps(body)
    if protocol == P.LLAMA_CPP_CLASSIFY:
        assert body["_lens_classifier"] is True
    answer = {
        "type": "choice",
        "choice": "yes",
        "probabilities": {"yes": 0.8, "no": 0.2},
        "confidence": 0.6,
    }
    output = {"answers": {"check": answer}}
    if protocol == P.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        output = {"success": True, "result": output}
    elif protocol == P.OPENAI_DECISIONS:
        output = {
            "answers": [
                {
                    "name": "check",
                    **answer,
                    "probabilities": [
                        {"value": "yes", "probability": 0.8},
                        {"value": "no", "probability": 0.2},
                    ],
                }
            ]
        }
    assert "yes" in site_model_probe._site_model_probe_output_text(protocol, output)
    with pytest.raises(ValueError):
        site_model_probe._site_model_probe_output_text(protocol, {"answers": {}})
