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
)
from .auth import get_current_admin
from .runtime_context import Any, app_state


async def _proxy_url() -> str:
    runtime = await app_state.settings_repo.get_runtime_settings()
    return str(runtime.get("proxy_url") or "")


async def list_balance_scripts(
    _: Any = Depends(get_current_admin),
) -> list[BalanceScript]:
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
    _: Any = Depends(get_current_admin),
) -> CredentialBalance:
    target = await app_state.balance_store.credential_refresh_target(
        site_id, credential_id
    )
    outcome = await asyncio.to_thread(
        run_balance_script,
        str(target["code"]),
        api_key=str(target["api_key"]),
        base_url=str(target["base_url"]),
        proxy=await _proxy_url(),
        timeout_seconds=int(target["timeout_seconds"]),
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
    )
    return BalanceScriptTestResult(
        ok=outcome.ok,
        total=outcome.total,
        balance=outcome.balance,
        currency=outcome.currency,
        error_message=outcome.error_message,
    )
