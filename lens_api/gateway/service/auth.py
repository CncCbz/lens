from __future__ import annotations

from .runtime_context import (
    AdminLoginRequest,
    AdminPasswordChangeRequest,
    AdminProfile,
    AdminProfileUpdateRequest,
    AdminProfileUpdateResponse,
    AdminUserEntity,
    Any,
    AppInfo,
    AuthTokenResponse,
    Depends,
    GatewayApiKey,
    HTTPAuthorizationCredentials,
    ModelGroup,
    HTTPException,
    PublicBranding,
    Request,
    Response,
    UTC,
    _read_system_version,
    app_state,
    conversion_matrix,
    create_access_token,
    datetime,
    decode_access_token,
    run_in_threadpool,
    settings,
    status,
)
from .lifecycle import auth_scheme


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> AdminUserEntity:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )

    payload = await run_in_threadpool(
        decode_access_token, credentials.credentials, settings
    )
    username = payload.get("sub")

    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )

    admin = await app_state.admin_repo.get_by_username(username)
    if admin is None or admin.is_active != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin not found"
        )

    return admin


async def get_current_gateway_key(request: Request) -> GatewayApiKey:
    authorization = request.headers.get("authorization", "")
    x_api_key = request.headers.get("x-api-key", "")
    x_goog_api_key = request.headers.get("x-goog-api-key", "")

    secret = ""
    if authorization.lower().startswith("bearer "):
        secret = authorization[7:].strip()
    elif x_api_key:
        secret = x_api_key.strip()
    elif x_goog_api_key:
        secret = x_goog_api_key.strip()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing gateway API key"
        )

    gateway_key = await app_state.gateway_api_key_repo.get_gateway_api_key_by_secret(
        secret
    )

    if gateway_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway API key"
        )

    if not gateway_key.enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gateway API key is disabled",
        )

    if _is_gateway_key_expired(gateway_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gateway API key has expired",
        )

    if (
        gateway_key.max_cost_usd > 0
        and gateway_key.spent_cost_usd >= gateway_key.max_cost_usd
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gateway API key has reached the max balance",
        )

    return gateway_key


def _is_gateway_key_expired(gateway_key: GatewayApiKey) -> bool:
    if not gateway_key.expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(
            gateway_key.expires_at.replace("Z", "+00:00")
        )
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _gateway_key_allows_model(
    gateway_key: GatewayApiKey, model_name: str | None
) -> bool:
    if not model_name:
        return True
    normalized_name = model_name.strip().lower()
    if gateway_key.allowed_models:
        normalized_allowed = {
            item.strip().lower() for item in gateway_key.allowed_models if item.strip()
        }
        if normalized_name not in normalized_allowed:
            return False
    normalized_excluded = {
        item.strip().lower() for item in gateway_key.excluded_models if item.strip()
    }
    return normalized_name not in normalized_excluded


def _group_restricts_keys(group: ModelGroup) -> bool:
    return group.restrict_keys or bool(group.allowed_key_ids)


def _gateway_key_allows_group(gateway_key: GatewayApiKey, group: ModelGroup) -> bool:
    if _group_restricts_keys(group) and gateway_key.id not in group.allowed_key_ids:
        return False
    return _gateway_key_allows_model(gateway_key, group.name)


async def public_branding() -> PublicBranding:
    branding = await app_state.settings_repo.get_branding_settings()
    return PublicBranding(
        site_name=branding["site_name"], logo_url=branding["site_logo_url"]
    )


async def app_info(_: Any = Depends(get_current_admin)) -> AppInfo:
    runtime = await app_state.settings_repo.get_runtime_settings()
    return AppInfo(
        system_version=_read_system_version(),
        site_name=str(runtime["site_name"]),
        logo_url=str(runtime["site_logo_url"]),
        time_zone=str(runtime["time_zone"]),
        protocol_conversions=conversion_matrix(),
    )


async def login(payload: AdminLoginRequest) -> AuthTokenResponse:
    user = await app_state.admin_repo.authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    access_token, expires_in = await run_in_threadpool(
        create_access_token, user.username, settings
    )
    return AuthTokenResponse(access_token=access_token, expires_in=expires_in)


async def current_admin(
    admin: AdminUserEntity = Depends(get_current_admin),
) -> AdminProfile:
    return AdminProfile(id=admin.id, username=admin.username)


async def update_profile(
    payload: AdminProfileUpdateRequest,
    admin: AdminUserEntity = Depends(get_current_admin),
) -> AdminProfileUpdateResponse:
    normalized_username = payload.username.strip()
    if not normalized_username:
        raise HTTPException(status_code=400, detail="Username is required")

    updated_admin = await app_state.admin_repo.update_profile(
        admin.username,
        normalized_username,
        payload.current_password,
        payload.new_password,
    )

    access_token, expires_in = await run_in_threadpool(
        create_access_token, updated_admin.username, settings
    )
    return AdminProfileUpdateResponse(
        access_token=access_token,
        expires_in=expires_in,
        profile=AdminProfile(id=updated_admin.id, username=updated_admin.username),
    )


async def change_password(
    payload: AdminPasswordChangeRequest,
    admin: AdminUserEntity = Depends(get_current_admin),
) -> Response:
    await app_state.admin_repo.update_password(
        admin.username, payload.current_password, payload.new_password
    )
    return Response(status_code=204)
