from __future__ import annotations

from lens_api.gateway.service.runtime_context import relay_log_capture_flags
from lens_api.persistence.repositories.settings_repository import SettingsRepository


def test_relay_log_capture_flags_defaults() -> None:
    assert relay_log_capture_flags({}) == (True, True, False, False)
    assert relay_log_capture_flags({"relay_log_body_enabled": True}) == (
        True,
        True,
        True,
        True,
    )
    assert relay_log_capture_flags(
        {
            "relay_log_request_headers_enabled": False,
            "relay_log_response_headers_enabled": True,
            "relay_log_request_body_enabled": True,
            "relay_log_response_body_enabled": False,
            "relay_log_body_enabled": True,
        }
    ) == (False, True, True, False)


def test_legacy_body_setting_fills_missing_body_flags() -> None:
    parse = SettingsRepository._parse_bool
    old_body = parse("true", default=False)
    assert parse(None, default=old_body) is True
    assert parse("false", default=old_body) is False
