import base64
import hashlib
import hmac
import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from lens_api.core.config import Settings
from lens_api.gateway import provider_http
from lens_api.gateway.upstreams import build_upstream_request
from lens_api.models import ChannelConfig, ChannelKeyItem, ProtocolKind as P


def channel(
    protocol: P, base: str, key: str = "token", headers: dict[str, str] | None = None
) -> ChannelConfig:
    return ChannelConfig(
        id="c",
        name="C",
        protocol=protocol,
        base_url=base,
        api_key=key,
        keys=[ChannelKeyItem(id="k", key=key)],
        headers=headers or {},
    )


@pytest.mark.parametrize(
    "protocol,base,path",
    [
        (P.AZURE_OPENAI_RESPONSES, "https://provider.test", "/openai/v1/responses"),
        (
            P.AZURE_OPENAI_RESPONSES,
            "https://provider.test/openai/v1",
            "/openai/v1/responses",
        ),
        (
            P.AZURE_OPENAI_RESPONSES,
            "https://provider.test/openai/deployments/deploy/responses?api-version=preview&custom=yes",
            "/openai/deployments/deploy/responses",
        ),
        (
            P.OPENAI_CODEX_RESPONSES,
            "https://provider.test/backend-api/codex",
            "/backend-api/codex/responses",
        ),
        (
            P.OPENAI_CODEX_RESPONSES,
            "https://provider.test/backend-api/codex/responses",
            "/backend-api/codex/responses",
        ),
        (
            P.MISTRAL_CONVERSATIONS,
            "https://provider.test/v1/chat/completions",
            "/v1/chat/completions",
        ),
        (
            P.OPENROUTER_IMAGES,
            "https://provider.test/api/v1",
            "/api/v1/chat/completions",
        ),
    ],
)
def test_provider_full_urls_and_original_queries_are_preserved(
    protocol: P, base: str, path: str
) -> None:
    body = {"model": "model", "input": "hello", "reasoning": {"effort": "low"}}
    original = deepcopy(body)
    upstream = build_upstream_request(
        channel(protocol, base, headers={"chatgpt-account-id": "account"}),
        body,
        Settings(),
    )
    assert urlsplit(upstream.url).path == path
    assert body == original
    if protocol == P.AZURE_OPENAI_RESPONSES:
        assert upstream.headers["api-key"] == "token"
        assert "authorization" not in upstream.headers
        if "preview" in base:
            assert parse_qs(urlsplit(upstream.url).query) == {
                "api-version": ["preview"],
                "custom": ["yes"],
            }
    if protocol == P.OPENAI_CODEX_RESPONSES:
        assert upstream.json_body["stream"] is True
        assert upstream.json_body["store"] is False
        assert upstream.headers["authorization"] == "Bearer token"


def test_codex_json_token_and_jwt_account_claim() -> None:
    claim = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"https://api.openai.com/auth": {"chatgpt_account_id": "account"}}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    token = f"e30.{claim}.sig"
    upstream = build_upstream_request(
        channel(
            P.OPENAI_CODEX_RESPONSES,
            "https://provider.test",
            json.dumps({"access_token": token}),
        ),
        {"model": "model", "input": "hi"},
        Settings(),
    )
    assert upstream.headers["authorization"] == f"Bearer {token}"
    assert upstream.headers["chatgpt-account-id"] == "account"


@pytest.mark.parametrize(
    "credentials,expected",
    [("key", {"key": ["key"]}), ('{"access_token":"access"}', {})],
)
def test_vertex_express_and_regional_tokens(
    credentials: str, expected: dict[str, list[str]]
) -> None:
    upstream = build_upstream_request(
        channel(
            P.GOOGLE_VERTEX,
            "https://provider.test/v1/projects/project/locations/us-central1/publishers/google?custom=1",
            credentials,
        ),
        {"model": "model", "stream": True, "contents": []},
        Settings(),
    )
    assert (
        urlsplit(upstream.url).path
        == "/v1/projects/project/locations/us-central1/publishers/google/models/model:streamGenerateContent"
    )
    assert parse_qs(urlsplit(upstream.url).query) == {
        "custom": ["1"],
        "alt": ["sse"],
        **expected,
    }
    assert upstream.json_body == {"contents": []}
    assert upstream.headers.get("authorization") == (
        "Bearer access" if not expected else None
    )


@pytest.mark.parametrize(
    "protocol,key",
    [
        (P.OPENAI_CODEX_RESPONSES, '{"access_token":""}'),
        (P.GOOGLE_VERTEX, '{"access_token":null}'),
        (P.BEDROCK_CONVERSE_STREAM, '{"access_key_id":"AKID","region":"us-east-1"}'),
        (
            P.BEDROCK_CONVERSE_STREAM,
            '{"access_key_id":"AKID","secret_access_key":"secret"}',
        ),
        (P.PI_MESSAGES, '{"access_token":"token"}'),
    ],
)
def test_provider_invalid_credentials_fail_before_send(protocol: P, key: str) -> None:
    with pytest.raises(HTTPException) as error:
        build_upstream_request(
            channel(protocol, "https://provider.test", key),
            {"model": "model"},
            Settings(),
        )
    assert error.value.status_code == 400
    assert key not in str(error.value.detail)


@pytest.mark.parametrize("operation", ["tokenize", "apply-template", "completion"])
@pytest.mark.parametrize(
    "base",
    [
        "https://provider.test/v1",
        "https://provider.test/v1/tokenize",
        "https://provider.test/v1/apply-template",
        "https://provider.test/v1/completion",
    ],
)
def test_llama_native_operations_keep_model_for_router_mode(
    operation: str, base: str
) -> None:
    upstream = build_upstream_request(
        channel(P.LLAMA_CPP_CLASSIFY, base),
        {"model": "actual", "content": "Yes", "stream": False},
        Settings(),
        path_suffix=operation,
    )
    assert upstream.url == f"https://provider.test/{operation}"
    assert upstream.json_body["model"] == "actual"


def test_openrouter_system_one_images_reach_state_as_image_url_parts() -> None:
    from lens_api.gateway.converters import convert_request

    source = {
        "model": "m",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": '{"text":"cat"}'},
                    {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
                ],
            }
        ],
        "questions": [
            {"name": "q", "type": "predicate", "instructions": "Is a cat present?"}
        ],
    }
    body = convert_request(P.OPENAI_DECISIONS, P.TYPESAFE_SYSTEM_ONE, source, "m")
    original = deepcopy(body)
    upstream = build_upstream_request(
        channel(P.TYPESAFE_SYSTEM_ONE, "https://openrouter.ai/api/v1"), body, Settings()
    )
    assert upstream.url == "https://openrouter.ai/api/v1/systemone"
    assert upstream.json_body["state"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
        {"text": "cat"},
    ]
    assert "assets" not in upstream.json_body
    assert "TypeSafe-Danger-Zone-Images" not in upstream.headers
    assert body == original


def test_bedrock_signs_final_bytes_and_encoded_model_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    monkeypatch.setattr(provider_http, "datetime", FrozenDateTime)
    credentials = json.dumps(
        {
            "AccessKeyId": "AKIDEXAMPLE",
            "SecretAccessKey": "secret-example",
            "SessionToken": "session-example",
            "region": "us-east-1",
        }
    )
    body = {
        "model": "arn:aws:bedrock:us-east-1:123:inference-profile/model",
        "modelId": "routing-only",
        "stream": True,
        "messages": [{"role": "user", "content": [{"text": "猫"}]}],
    }
    upstream = build_upstream_request(
        channel(
            P.BEDROCK_CONVERSE_STREAM,
            "https://bedrock-runtime.us-east-1.amazonaws.com?z=last&a=two+words&a=first",
            credentials,
        ),
        body,
        Settings(),
    )
    assert not ({"model", "modelId", "stream"} & upstream.json_body.keys())
    data = json.dumps(
        upstream.json_body, ensure_ascii=False, separators=(",", ":")
    ).encode()
    signed = provider_http.sign_upstream_headers(upstream, data)
    digest = hashlib.sha256(data).hexdigest()
    assert signed["x-amz-content-sha256"] == digest
    assert signed["x-amz-date"] == "20200102T030405Z"
    assert signed["x-amz-security-token"] == "session-example"
    canonical_headers = f"content-type:application/json\nhost:bedrock-runtime.us-east-1.amazonaws.com\nx-amz-content-sha256:{digest}\nx-amz-date:20200102T030405Z\nx-amz-security-token:session-example\n"
    signed_names = (
        "content-type;host;x-amz-content-sha256;x-amz-date;x-amz-security-token"
    )
    canonical_uri = "/model/arn%253Aaws%253Abedrock%253Aus-east-1%253A123%253Ainference-profile%252Fmodel/converse-stream"
    canonical = "\n".join(
        [
            "POST",
            canonical_uri,
            "a=first&a=two%20words&z=last",
            canonical_headers,
            signed_names,
            digest,
        ]
    )
    scope = "20200102/us-east-1/bedrock/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            "20200102T030405Z",
            scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ]
    )
    date_key = hmac.digest(b"AWS4secret-example", b"20200102", "sha256")
    region_key = hmac.digest(date_key, b"us-east-1", "sha256")
    service_key = hmac.digest(region_key, b"bedrock", "sha256")
    signing_key = hmac.digest(service_key, b"aws4_request", "sha256")
    expected = hmac.new(signing_key, string_to_sign.encode(), "sha256").hexdigest()
    assert (
        signed["authorization"]
        == f"AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/{scope}, SignedHeaders={signed_names}, Signature={expected}"
    )
    assert (
        signed["authorization"]
        != provider_http.sign_upstream_headers(upstream, data + b" ")["authorization"]
    )
    assert upstream.headers.get("x-amz-date") is None
