from __future__ import annotations

import asyncio

from fastapi import Depends, Response

from ..balance_runner import run_balance_script
from ...models import (
    BalanceScript,
    BalanceScriptInput,
    BalanceScriptTestRequest,
    BalanceScriptTestResult,
    CredentialBalance,
    CredentialBalanceRefreshRequest,
)
from ...persistence.balance_store import load_balance_params
from .auth import get_current_admin
from .runtime_context import Any, app_state


async def _proxy_url() -> str:
    runtime = await app_state.settings_repo.get_runtime_settings()
    return str(runtime.get("proxy_url") or "")


async def list_balance_scripts(
    _: Any = Depends(get_current_admin),
) -> list[BalanceScript]:
    await app_state.balance_store.ensure_builtin_scripts()
    return await app_state.balance_store.list_scripts()


async def create_balance_script(
    payload: BalanceScriptInput, _: Any = Depends(get_current_admin)
) -> BalanceScript:
    return await app_state.balance_store.create_script(payload)


async def update_balance_script(
    script_id: str,
    payload: BalanceScriptInput,
    _: Any = Depends(get_current_admin),
) -> BalanceScript:
    return await app_state.balance_store.update_script(script_id, payload)


async def delete_balance_script(
    script_id: str, _: Any = Depends(get_current_admin)
) -> Response:
    await app_state.balance_store.delete_script(script_id)
    return Response(status_code=204)


async def list_credential_balances(
    _: Any = Depends(get_current_admin),
) -> list[CredentialBalance]:
    return await app_state.balance_store.list_balances()


async def refresh_credential_balance(
    site_id: str,
    credential_id: str,
    payload: CredentialBalanceRefreshRequest,
    _: Any = Depends(get_current_admin),
) -> CredentialBalance:
    source = await app_state.balance_store.credential_balance_source(
        site_id, credential_id
    )
    script_id = payload.balance_script_id.strip() or (source or {}).get(
        "balance_script_id", ""
    )
    if not script_id:
        raise ValueError("Credential has no balance script")
    script = await app_state.balance_store.get_script(script_id)
    if script is None:
        raise KeyError(script_id)
    if payload.balance_script_id.strip():
        raw_params = payload.balance_params
    else:
        raw_params = load_balance_params((source or {}).get("balance_params_json"))
    declared = {item.key for item in script.parameters}
    params = {key: str(raw_params.get(key, "")) for key in declared}
    api_key = payload.api_key.strip() or (source or {}).get("api_key", "")
    base_url = payload.base_url.strip() or (source or {}).get("base_url", "")
    if not api_key:
        raise ValueError("API key is required")
    if not base_url:
        raise ValueError("Credential base URL not found")
    outcome = await asyncio.to_thread(
        run_balance_script,
        script.code,
        api_key=api_key,
        base_url=base_url,
        proxy=await _proxy_url(),
        timeout_seconds=script.timeout_seconds,
        params=params,
    )
    if source is None:
        return CredentialBalance(
            credential_id=credential_id,
            total=outcome.total,
            balance=outcome.balance,
            currency=outcome.currency,
            status="ok" if outcome.ok else "error",
            error_message=outcome.error_message,
        )
    return await app_state.balance_store.save_outcome(
        credential_id,
        ok=outcome.ok,
        total=outcome.total,
        balance=outcome.balance,
        currency=outcome.currency,
        error_message=outcome.error_message,
    )


async def test_balance_script(
    payload: BalanceScriptTestRequest, _: Any = Depends(get_current_admin)
) -> BalanceScriptTestResult:
    proxy = payload.proxy.strip() or await _proxy_url()
    outcome = await asyncio.to_thread(
        run_balance_script,
        payload.code,
        api_key=payload.api_key,
        base_url=payload.base_url,
        proxy=proxy,
        timeout_seconds=payload.timeout_seconds,
        params=payload.params,
    )
    return BalanceScriptTestResult(
        ok=outcome.ok,
        total=outcome.total,
        balance=outcome.balance,
        currency=outcome.currency,
        error_message=outcome.error_message,
    )
