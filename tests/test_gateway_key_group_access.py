from lens_api.gateway.service.auth import (
    _gateway_key_allows_group,
    _gateway_key_allows_model,
)
from lens_api.gateway.service.proxy_routes import _build_gemini_models_payload
from lens_api.models import (
    GatewayApiKey,
    ModelGroup,
    ModelGroupItem,
    ProtocolKind,
    RoutingStrategy,
)


def _key(key_id: str, **kwargs: object) -> GatewayApiKey:
    return GatewayApiKey(
        id=key_id,
        api_key=f"sk-{key_id}",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        **kwargs,
    )


def _group(**kwargs: object) -> ModelGroup:
    return ModelGroup(
        id="g1",
        name="private-model",
        protocols=[ProtocolKind.GEMINI],
        strategy=RoutingStrategy.ROUND_ROBIN,
        items=[
            ModelGroupItem(
                channel_id="channel-gemini",
                protocol=ProtocolKind.GEMINI,
                credential_id="cred-1",
                model_name="private-model",
                enabled=True,
            )
        ],
        **kwargs,
    )


def test_public_group_visible_to_all_keys() -> None:
    group = _group()
    assert _gateway_key_allows_group(_key("key-1"), group)
    assert _gateway_key_allows_group(_key("key-2"), group)


def test_private_group_visible_only_to_listed_keys() -> None:
    group = _group(allowed_key_ids=["key-1"])
    assert _gateway_key_allows_group(_key("key-1"), group)
    assert not _gateway_key_allows_group(_key("key-2"), group)


def test_private_group_still_respects_key_allowlist() -> None:
    group = _group(allowed_key_ids=["key-1"])
    allow_key = _key("key-1", allowed_models=["other-model"])
    assert not _gateway_key_allows_group(allow_key, group)
    assert _gateway_key_allows_model(allow_key, "other-model")


def test_gemini_model_list_hides_private_group() -> None:
    group = _group(allowed_key_ids=["key-1"])
    allowed = _build_gemini_models_payload([group], _key("key-1"))
    denied = _build_gemini_models_payload([group], _key("key-2"))
    assert [item["baseModelId"] for item in allowed["models"]] == ["private-model"]
    assert denied["models"] == []


def test_restricted_group_with_no_keys_is_hidden() -> None:
    group = _group(restrict_keys=True, allowed_key_ids=[])
    assert not _gateway_key_allows_group(_key("key-1"), group)
    assert _build_gemini_models_payload([group], _key("key-1"))["models"] == []
