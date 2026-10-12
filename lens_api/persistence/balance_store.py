from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models import (
    BalanceScript,
    BalanceScriptInput,
    CredentialBalance,
)
from .entities import (
    BalanceScriptEntity,
    CredentialBalanceEntity,
    SiteBaseUrlEntity,
    SiteCredentialEntity,
)


def _script_item(row: BalanceScriptEntity) -> BalanceScript:
    return BalanceScript(
        id=row.id,
        name=row.name,
        code=row.code,
        timeout_seconds=row.timeout_seconds,
    )


def _balance_item(row: CredentialBalanceEntity) -> CredentialBalance:
    checked_at = row.checked_at.isoformat() if row.checked_at is not None else None
    return CredentialBalance(
        credential_id=row.credential_id,
        total=row.total,
        balance=row.balance,
        currency=row.currency,
        status=row.status,
        error_message=row.error_message,
        checked_at=checked_at,
    )


class BalanceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_scripts(self) -> list[BalanceScript]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(BalanceScriptEntity).order_by(
                            BalanceScriptEntity.name.asc()
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_script_item(row) for row in rows]

    async def create_script(self, payload: BalanceScriptInput) -> BalanceScript:
        script_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            session.add(
                BalanceScriptEntity(
                    id=script_id,
                    name=payload.name.strip(),
                    code=payload.code,
                    timeout_seconds=payload.timeout_seconds,
                )
            )
            await session.commit()
        script = await self.get_script(script_id)
        if script is None:
            raise KeyError(script_id)
        return script

    async def update_script(
        self, script_id: str, payload: BalanceScriptInput
    ) -> BalanceScript:
        async with self._session_factory() as session:
            row = await session.get(BalanceScriptEntity, script_id)
            if row is None:
                raise KeyError(script_id)
            row.name = payload.name.strip()
            row.code = payload.code
            row.timeout_seconds = payload.timeout_seconds
            await session.commit()
        script = await self.get_script(script_id)
        if script is None:
            raise KeyError(script_id)
        return script

    async def get_script(self, script_id: str) -> BalanceScript | None:
        async with self._session_factory() as session:
            row = await session.get(BalanceScriptEntity, script_id)
        return _script_item(row) if row is not None else None

    async def delete_script(self, script_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(BalanceScriptEntity, script_id)
            if row is None:
                raise KeyError(script_id)
            await session.execute(
                delete(BalanceScriptEntity).where(BalanceScriptEntity.id == script_id)
            )
            credentials = (
                (
                    await session.execute(
                        select(SiteCredentialEntity).where(
                            SiteCredentialEntity.balance_script_id == script_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for credential in credentials:
                credential.balance_script_id = None
            await session.commit()

    async def list_balances(self) -> list[CredentialBalance]:
        async with self._session_factory() as session:
            rows = (
                (await session.execute(select(CredentialBalanceEntity))).scalars().all()
            )
        return [_balance_item(row) for row in rows]

    async def list_sync_targets(self) -> list[dict[str, str | int]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        SiteCredentialEntity,
                        SiteBaseUrlEntity,
                        BalanceScriptEntity,
                    )
                    .join(
                        SiteBaseUrlEntity,
                        SiteBaseUrlEntity.id == SiteCredentialEntity.base_url_id,
                    )
                    .join(
                        BalanceScriptEntity,
                        BalanceScriptEntity.id
                        == SiteCredentialEntity.balance_script_id,
                    )
                    .where(
                        SiteCredentialEntity.enabled == 1,
                        SiteBaseUrlEntity.enabled == 1,
                    )
                )
            ).all()
            return [
                {
                    "credential_id": credential.id,
                    "api_key": credential.api_key,
                    "base_url": base_url.url,
                    "code": script.code,
                    "timeout_seconds": script.timeout_seconds,
                }
                for credential, base_url, script in rows
            ]

    async def credential_refresh_target(
        self, site_id: str, credential_id: str
    ) -> dict[str, str | int]:
        async with self._session_factory() as session:
            credential = await session.get(SiteCredentialEntity, credential_id)
            if credential is None or credential.site_id != site_id:
                raise KeyError(credential_id)
            if not credential.balance_script_id:
                raise ValueError("Credential has no balance script")
            base_url = await session.get(SiteBaseUrlEntity, credential.base_url_id)
            script = await session.get(
                BalanceScriptEntity, credential.balance_script_id
            )
            if base_url is None or base_url.site_id != site_id:
                raise ValueError("Credential base URL not found")
            if script is None:
                raise ValueError(
                    f"Balance script not found: {credential.balance_script_id}"
                )
            return {
                "credential_id": credential.id,
                "api_key": credential.api_key,
                "base_url": base_url.url,
                "code": script.code,
                "timeout_seconds": script.timeout_seconds,
            }

    async def save_outcome(
        self,
        credential_id: str,
        *,
        ok: bool,
        total: str,
        balance: str,
        currency: str,
        error_message: str,
    ) -> CredentialBalance:
        checked_at = datetime.now(UTC).replace(tzinfo=None)
        async with self._session_factory() as session:
            row = await session.get(CredentialBalanceEntity, credential_id)
            if row is None:
                row = CredentialBalanceEntity(credential_id=credential_id)
                session.add(row)
            row.total = total
            row.balance = balance
            row.currency = currency
            row.status = "ok" if ok else "error"
            row.error_message = error_message
            row.checked_at = checked_at
            await session.commit()
            await session.refresh(row)
            return _balance_item(row)
