from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import secrets
from typing import Any

import jwt

from .config import Settings

PBKDF2_ITERATIONS = 600_000
JWT_ALGORITHM = "HS256"
REDACTED_CREDENTIAL_VALUE = "<redacted>"

_SENSITIVE_CREDENTIAL_NAMES = frozenset(
    {
        "api-key",
        "api-keys",
        "apikey",
        "authentication",
        "authorization",
        "cf-access-jwt-assertion",
        "client-secret",
        "cookie",
        "password",
        "private-key",
        "proxy-authenticate",
        "proxy-authorization",
        "secret",
        "set-cookie",
        "token",
        "www-authenticate",
        "x-amz-credential",
        "x-amz-security-token",
        "x-api-key",
        "x-auth-token",
        "x-goog-api-key",
    }
)
_SENSITIVE_CREDENTIAL_COMPACT_NAMES = frozenset(
    {
        "accesstoken",
        "apikey",
        "authtoken",
        "authorizationtoken",
        "clientsecret",
        "idtoken",
        "privatekey",
        "refreshtoken",
        "secretkey",
        "securitytoken",
    }
)
_SENSITIVE_CREDENTIAL_COMPACT_SUFFIXES = (
    "apikey",
    "auth",
    "authentication",
    "credential",
    "credentials",
    "jwt",
    "oauth",
    "oauthtoken",
    "password",
    "privatekey",
    "secret",
    "token",
)
_SENSITIVE_CREDENTIAL_SUFFIXES = (
    "-access-token",
    "-api-key",
    "-auth-token",
    "-authorization-token",
    "-id-token",
    "-password",
    "-private-key",
    "-refresh-token",
    "-secret",
    "-security-token",
)


def is_sensitive_credential_name(name: str) -> bool:
    normalized = str(name).strip().lower().replace("_", "-")
    return (
        normalized in _SENSITIVE_CREDENTIAL_NAMES
        or normalized.replace("-", "") in _SENSITIVE_CREDENTIAL_COMPACT_NAMES
        or normalized.replace("-", "").endswith(_SENSITIVE_CREDENTIAL_COMPACT_SUFFIXES)
        or normalized.endswith(_SENSITIVE_CREDENTIAL_SUFFIXES)
    )


def _redact_sensitive_values(value: Any, key: str | None = None) -> Any:
    if key is not None and is_sensitive_credential_name(key):
        return REDACTED_CREDENTIAL_VALUE
    if isinstance(value, dict):
        return {
            item_key: _redact_sensitive_values(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def redact_sensitive_log_content(value: str | None) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return _redact_sensitive_sse_content(value)
    return json.dumps(
        _redact_sensitive_values(payload),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _redact_sensitive_sse_content(value: str) -> str:
    lines = value.splitlines(keepends=True)
    data_lines: list[tuple[int, str]] = []

    def sanitize_event() -> None:
        if not data_lines:
            return
        try:
            payload = json.loads("\n".join(item[1] for item in data_lines))
        except (TypeError, json.JSONDecodeError):
            data_lines.clear()
            return
        first_index = data_lines[0][0]
        first_line = lines[first_index]
        content = first_line.rstrip("\r\n")
        newline = first_line[len(content) :]
        raw_payload = content.split(":", 1)[1] if ":" in content else ""
        whitespace = raw_payload[: len(raw_payload) - len(raw_payload.lstrip())]
        sanitized = json.dumps(
            _redact_sensitive_values(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines[first_index] = f"data:{whitespace}{sanitized}{newline}"
        for index, _ in data_lines[1:]:
            lines[index] = ""
        data_lines.clear()

    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        if not content:
            sanitize_event()
            continue
        if content == "data" or content.startswith("data:"):
            raw_payload = content.split(":", 1)[1] if ":" in content else ""
            data_lines.append((index, raw_payload.removeprefix(" ")))
    sanitize_event()
    return "".join(lines)


def redact_sensitive_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: (
            REDACTED_CREDENTIAL_VALUE
            if is_sensitive_credential_name(key)
            else str(value)
        )
        for key, value in headers.items()
    }


def redact_sensitive_header_json(value: str | None) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    headers = {str(key): str(item) for key, item in payload.items()}
    return json.dumps(
        redact_sensitive_headers(headers),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        algorithm, iterations_text, salt, digest = hashed_password.split("$", 3)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations_text),
    ).hex()
    return hmac.compare_digest(candidate, digest)


def create_access_token(subject: str, settings: Settings) -> tuple[str, int]:
    if not settings.auth_secret_key.strip():
        raise RuntimeError("LENS_AUTH_SECRET_KEY is required")
    expires_in = settings.auth_access_token_minutes * 60
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    token = jwt.encode(
        {
            "sub": subject,
            "exp": expires_at,
        },
        settings.auth_secret_key,
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.auth_secret_key.strip():
        raise RuntimeError("LENS_AUTH_SECRET_KEY is required")
    return jwt.decode(token, settings.auth_secret_key, algorithms=[JWT_ALGORITHM])
