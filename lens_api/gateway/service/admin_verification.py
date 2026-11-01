from __future__ import annotations

from ...models import (
    ModelVerificationLatest,
    ModelVerificationPage,
    ModelVerificationRequest,
    ModelVerificationRun,
)
from .auth import get_current_admin
from .runtime_context import AdminUserEntity, Depends, Query, app_state


async def start_verification(
    payload: ModelVerificationRequest,
    admin: AdminUserEntity = Depends(get_current_admin),
) -> ModelVerificationRun:
    """Queue a run and return at once.

    A run takes tens of seconds, which outlives the timeout of any proxy in
    front of the API, so the client polls instead of holding the request open.
    """
    del admin
    run_id = await app_state.verification_runs.start(payload)
    return ModelVerificationRun(run_id=run_id, status="running")


async def get_verification_run(
    run_id: str,
    admin: AdminUserEntity = Depends(get_current_admin),
) -> ModelVerificationRun:
    del admin
    run = await app_state.verification_runs.get(run_id)
    if run is None:
        raise LookupError(f"Unknown verification run: {run_id}")
    return ModelVerificationRun(
        run_id=run.run_id,
        status=run.status,
        result=run.result,
        error=run.error,
    )


async def list_verifications(
    site_id: str | None = Query(default=None),
    model_name: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    trigger: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: AdminUserEntity = Depends(get_current_admin),
) -> ModelVerificationPage:
    del admin
    return await app_state.verification_store.list(
        site_id=site_id,
        model_name=model_name,
        decision=decision,
        trigger=trigger,
        limit=limit,
        offset=offset,
    )


async def latest_verifications(
    admin: AdminUserEntity = Depends(get_current_admin),
) -> list[ModelVerificationLatest]:
    del admin
    return await app_state.verification_store.latest_by_model()
