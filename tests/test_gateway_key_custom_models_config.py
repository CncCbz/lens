from __future__ import annotations

import pytest

from lens_api.models import PiConfigExportResponse


def test_from_json_empty() -> None:
    assert PiConfigExportResponse.from_json(None) is None
    assert PiConfigExportResponse.from_json("") is None
    assert PiConfigExportResponse.from_json("  ") is None


def test_from_json_roundtrip() -> None:
    payload = PiConfigExportResponse(
        type="pi",
        models=[{"id": "claude-3-7-sonnet", "max_tokens": 16384}],
    )
    parsed = PiConfigExportResponse.from_json(payload.model_dump_json())
    assert parsed == payload


def test_from_json_allows_omitted_models() -> None:
    parsed = PiConfigExportResponse.from_json('{"type": "pi"}')
    assert parsed == PiConfigExportResponse(type="pi", models=[])


def test_from_json_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid custom models config JSON"):
        PiConfigExportResponse.from_json("{")
    with pytest.raises(ValueError, match="Invalid custom models config JSON"):
        PiConfigExportResponse.from_json("[]")
    with pytest.raises(ValueError, match="Invalid custom models config JSON"):
        PiConfigExportResponse.from_json('{"type": "pi", "extra": 1}')
