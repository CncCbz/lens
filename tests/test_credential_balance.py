from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from lens_api.core.db import Base, create_engine, create_session_factory
from lens_api.gateway.balance_runner import run_balance_script
from lens_api.gateway.service.admin_balance import refresh_credential_balance
from lens_api.gateway.service.runtime_context import app_state
from lens_api.models import (
    BalanceScriptInput,
    CredentialBalanceRefreshRequest,
    ChannelProxyMode,
    ProtocolKind,
    SiteBaseUrlInput,
    SiteCreate,
    SiteCredentialInput,
    SiteModelInput,
    SiteProtocolConfigInput,
    SiteUpdate,
)
from lens_api.persistence.backup_store.store import BackupStore
from lens_api.persistence.balance_store import BalanceStore
from lens_api.persistence.channel_store.store import ChannelStore
from lens_api.persistence.entities import CredentialBalanceEntity
from sqlalchemy import select, text
import sqlalchemy as sa

PARENT_REVISION = "b7e2a9c4d1f6"


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("lens_database_url", database_url)
    return config


async def _reset_database(database_url: str) -> None:
    engine = create_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


async def _insert(conn, table: str, values: dict) -> None:
    def columns(sync_conn):
        return sa.inspect(sync_conn).get_columns(table)

    available = {column["name"]: column for column in await conn.run_sync(columns)}
    payload: dict = {}
    for name, column in available.items():
        if name in values:
            payload[name] = values[name]
            continue
        if column.get("nullable") or column.get("default") is not None:
            continue
        if column.get("autoincrement"):
            continue
        column_type = column["type"].python_type
        if column_type is str:
            payload[name] = ""
        elif column_type is int:
            payload[name] = 0
        elif column_type is float:
            payload[name] = 0.0
        elif column_type is bool:
            payload[name] = False
        else:
            raise AssertionError(f"{table}.{name} needs a value")
    names = ", ".join(payload)
    binds = ", ".join(f":{name}" for name in payload)
    await conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({binds})"), payload)


def _site_update(site) -> SiteUpdate:
    return SiteUpdate(
        name=site.name,
        base_urls=[
            SiteBaseUrlInput(
                id=item.id,
                url=item.url,
                name=item.name,
                enabled=item.enabled,
                supported_protocols=item.supported_protocols,
            )
            for item in site.base_urls
        ],
        credentials=[
            SiteCredentialInput(
                id=item.id,
                name=item.name,
                api_key=item.api_key,
                enabled=item.enabled,
                cost_multiplier=item.cost_multiplier,
                base_url_id=item.base_url_id,
                balance_script_id=item.balance_script_id,
            )
            for item in site.credentials
        ],
        protocols=[
            SiteProtocolConfigInput(
                id=item.id,
                name=item.name,
                protocols=item.protocols,
                enabled=item.enabled,
                headers=item.headers,
                proxy_mode=item.proxy_mode,
                channel_proxy=item.channel_proxy,
                concurrency_limit=item.concurrency_limit,
                rpm_limit=item.rpm_limit,
                token_limit=item.token_limit,
                cost_limit_usd=item.cost_limit_usd,
                param_override=item.param_override,
                match_regex=item.match_regex,
                router_error_policy_config=item.router_error_policy_config,
                base_url_id=item.base_url_id,
                credential_id=item.credential_id,
                models=[
                    SiteModelInput(
                        id=model.id,
                        credential_id=model.credential_id,
                        model_name=model.model_name,
                        enabled=model.enabled,
                        protocol=model.protocol or ProtocolKind.OPENAI_CHAT,
                    )
                    for model in item.models
                ],
            )
            for item in site.protocols
        ],
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_alembic_migration_keeps_split_credentials_usable(
    postgres_url: str,
) -> None:
    await _reset_database(postgres_url)
    config = _alembic_config(postgres_url)
    command.upgrade(config, PARENT_REVISION)

    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await _insert(conn, "sites", {"id": "site-1", "name": "lanvce"})
        await _insert(
            conn,
            "site_base_urls",
            {
                "id": "base-1",
                "site_id": "site-1",
                "url": "https://api.tokenpour.com/",
                "name": "TokenPour",
                "enabled": 1,
                "sort_order": 0,
                "supported_protocols_json": "[]",
            },
        )
        await _insert(
            conn,
            "site_base_urls",
            {
                "id": "base-2",
                "site_id": "site-1",
                "url": "https://api.siliconflow.cn/",
                "name": "SiliconFlow",
                "enabled": 1,
                "sort_order": 1,
                "supported_protocols_json": "[]",
            },
        )
        for credential_id, name, api_key, sort_order in (
            ("cred-shared", "shared", "sk-shared", 0),
            ("cred-stable", "stable", "sk-stable", 1),
            ("cred-orphan", "orphan", "sk-orphan", 2),
        ):
            await _insert(
                conn,
                "site_credentials",
                {
                    "id": credential_id,
                    "site_id": "site-1",
                    "name": name,
                    "api_key": api_key,
                    "enabled": 1,
                    "cost_multiplier": 1.0,
                    "sort_order": sort_order,
                },
            )
        await _insert(
            conn,
            "site_protocol_configs",
            {
                "id": "pcfg-grok",
                "site_id": "site-1",
                "name": "grok",
                "protocols_json": '["openai_chat"]',
                "credential_id": "cred-shared",
                "enabled": 1,
                "headers_json": "{}",
                "proxy_mode": "inherit",
                "base_url_id": "base-1",
            },
        )
        await _insert(
            conn,
            "site_protocol_configs",
            {
                "id": "pcfg-sf",
                "site_id": "site-1",
                "name": "sf",
                "protocols_json": '["openai_chat"]',
                "credential_id": "cred-shared",
                "enabled": 1,
                "headers_json": "{}",
                "proxy_mode": "inherit",
                "base_url_id": "base-2",
            },
        )
        await _insert(
            conn,
            "site_protocol_configs",
            {
                "id": "pcfg-stable",
                "site_id": "site-1",
                "name": "stable",
                "protocols_json": '["openai_chat"]',
                "credential_id": "cred-stable",
                "enabled": 1,
                "headers_json": "{}",
                "proxy_mode": "inherit",
                "base_url_id": "base-1",
            },
        )
        await _insert(
            conn,
            "site_discovered_models",
            {
                "id": "model-grok",
                "protocol_config_id": "pcfg-grok",
                "credential_id": "cred-shared",
                "model_name": "grok-4.7",
                "enabled": 1,
                "sort_order": 0,
                "protocol": "openai_chat",
            },
        )
        await _insert(
            conn,
            "site_discovered_models",
            {
                "id": "model-qwen",
                "protocol_config_id": "pcfg-sf",
                "credential_id": "cred-shared",
                "model_name": "qwen",
                "enabled": 1,
                "sort_order": 0,
                "protocol": "openai_chat",
            },
        )
        await _insert(
            conn,
            "model_group_items",
            {
                "group_id": "group-1",
                "channel_id": "pcfg-grok_openai_chat",
                "credential_id": "cred-shared",
                "model_name": "grok-4.7",
                "enabled": 1,
                "priority": 0,
                "weight": 1,
            },
        )
    await engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(postgres_url)
    store = ChannelStore(create_session_factory(engine))
    site = (await store.list_sites())[0]
    await store.update_site(site.id, _site_update(site))
    site = (await store.list_sites())[0]

    by_id = {item.id: item for item in site.credentials}
    shared = by_id["cred-shared"]
    assert shared.base_url_id == "base-1"
    assert shared.api_key == "sk-shared"
    assert by_id["cred-stable"].id == "cred-stable"
    assert by_id["cred-stable"].base_url_id == "base-1"
    assert by_id["cred-orphan"].base_url_id == "base-1"
    copies = [
        item
        for item in site.credentials
        if item.api_key == "sk-shared" and item.id != "cred-shared"
    ]
    assert len(copies) == 1
    assert copies[0].base_url_id == "base-2"
    protocols = {item.id: item for item in site.protocols}
    assert protocols["pcfg-grok"].credential_id == "cred-shared"
    assert protocols["pcfg-sf"].credential_id == copies[0].id
    assert protocols["pcfg-sf"].models[0].credential_id == copies[0].id
    assert protocols["pcfg-grok"].models[0].credential_id == "cred-shared"
    for protocol in site.protocols:
        credential = by_id[protocol.credential_id]
        assert credential.base_url_id == protocol.base_url_id

    async with engine.connect() as conn:
        group_credential = (
            await conn.execute(text("SELECT credential_id FROM model_group_items"))
        ).scalar_one()
    assert group_credential == "cred-shared"
    await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_save_rejects_credential_on_another_address(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    store = ChannelStore(create_session_factory(engine))
    payload = dict(
        name="Alpha",
        base_urls=[
            SiteBaseUrlInput(id="base-1", url="https://one.example"),
            SiteBaseUrlInput(id="base-2", url="https://two.example"),
        ],
        credentials=[
            SiteCredentialInput(
                id="cred-1",
                name="key",
                api_key="sk-1",
                base_url_id="missing",
            )
        ],
        protocols=[],
    )
    with pytest.raises(ValueError, match="base URL not found"):
        await store.create_site(SiteCreate(**payload))

    await store.create_site(
        SiteCreate(
            name="Alpha",
            base_urls=payload["base_urls"],
            credentials=[
                SiteCredentialInput(
                    id="cred-1",
                    name="one",
                    api_key="sk-1",
                    base_url_id="base-1",
                ),
                SiteCredentialInput(
                    id="cred-2",
                    name="two",
                    api_key="sk-2",
                    base_url_id="base-2",
                ),
            ],
            protocols=[
                SiteProtocolConfigInput(
                    id="pcfg-1",
                    name="combo",
                    protocols=[ProtocolKind.OPENAI_CHAT],
                    credential_id="cred-1",
                    base_url_id="base-1",
                    models=[],
                )
            ],
        )
    )
    with pytest.raises(ValueError, match="does not belong"):
        await store.update_site(
            (await store.list_sites())[0].id,
            SiteUpdate(
                name="Alpha",
                base_urls=payload["base_urls"],
                credentials=[
                    SiteCredentialInput(
                        id="cred-1",
                        name="one",
                        api_key="sk-1",
                        base_url_id="base-1",
                    ),
                    SiteCredentialInput(
                        id="cred-2",
                        name="two",
                        api_key="sk-2",
                        base_url_id="base-2",
                    ),
                ],
                protocols=[
                    SiteProtocolConfigInput(
                        id="pcfg-1",
                        name="combo",
                        protocols=[ProtocolKind.OPENAI_CHAT],
                        credential_id="cred-2",
                        base_url_id="base-1",
                        models=[],
                    )
                ],
            ),
        )
    await engine.dispose()


def test_balance_script_parses_json_and_failures() -> None:
    ok = run_balance_script(
        'import json; print(json.dumps({"total":"10","balance":"3","currency":"USD"}))',
        api_key="sk",
        base_url="https://example.com",
        proxy="",
        timeout_seconds=10,
    )
    assert ok.ok
    assert (ok.total, ok.balance, ok.currency) == ("10", "3", "USD")

    invalid = run_balance_script(
        "print('nope')",
        api_key="sk",
        base_url="https://example.com",
        proxy="",
        timeout_seconds=10,
    )
    assert not invalid.ok

    failed = run_balance_script(
        "import sys; sys.exit(2)",
        api_key="sk",
        base_url="https://example.com",
        proxy="",
        timeout_seconds=10,
    )
    assert not failed.ok

    timed_out = run_balance_script(
        "import time; time.sleep(5)",
        api_key="sk",
        base_url="https://example.com",
        proxy="",
        timeout_seconds=1,
    )
    assert not timed_out.ok
    assert "timed out" in timed_out.error_message


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_manual_refresh_updates_only_the_selected_key(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    channel_store = ChannelStore(factory)
    balance_store = BalanceStore(factory)
    script = await balance_store.create_script(
        BalanceScriptInput(
            name="static",
            code='import json; print(json.dumps({"total":"8","balance":"5","currency":"USD"}))',
            timeout_seconds=10,
        )
    )
    site = await channel_store.create_site(
        SiteCreate(
            name="Alpha",
            base_urls=[SiteBaseUrlInput(id="base-1", url="https://example.com")],
            credentials=[
                SiteCredentialInput(
                    id="cred-1",
                    name="one",
                    api_key="sk-1",
                    base_url_id="base-1",
                    balance_script_id=script.id,
                ),
                SiteCredentialInput(
                    id="cred-2",
                    name="two",
                    api_key="sk-2",
                    base_url_id="base-1",
                    balance_script_id=script.id,
                ),
            ],
            protocols=[],
        )
    )
    target = await balance_store.credential_refresh_target(site.id, "cred-1")
    outcome = run_balance_script(
        str(target["code"]),
        api_key=str(target["api_key"]),
        base_url=str(target["base_url"]),
        proxy="",
        timeout_seconds=int(target["timeout_seconds"]),
    )
    saved = await balance_store.save_outcome(
        "cred-1",
        ok=outcome.ok,
        total=outcome.total,
        balance=outcome.balance,
        currency=outcome.currency,
        error_message=outcome.error_message,
    )
    assert saved.credential_id == "cred-1"
    assert saved.balance == "5"
    async with factory() as session:
        rows = (await session.execute(select(CredentialBalanceEntity))).scalars().all()
    assert [row.credential_id for row in rows] == ["cred-1"]
    await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_refresh_uses_unsaved_script_selection(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    channel_store = ChannelStore(factory)
    balance_store = BalanceStore(factory)
    script = await balance_store.create_script(
        BalanceScriptInput(
            name="static",
            code='import json; print(json.dumps({"total":"8","balance":"5","currency":"USD"}))',
            timeout_seconds=10,
        )
    )
    site = await channel_store.create_site(
        SiteCreate(
            name="Alpha",
            base_urls=[SiteBaseUrlInput(id="base-1", url="https://example.com")],
            credentials=[
                SiteCredentialInput(
                    id="cred-1",
                    name="one",
                    api_key="sk-1",
                    base_url_id="base-1",
                )
            ],
            protocols=[],
        )
    )

    class _Settings:
        async def get_runtime_settings(self) -> dict[str, str]:
            return {"proxy_url": ""}

    original_store = app_state.balance_store
    original_settings = app_state.settings_repo
    app_state.balance_store = balance_store
    app_state.settings_repo = _Settings()  # type: ignore[assignment]
    try:
        saved = await refresh_credential_balance(
            site.id,
            "cred-1",
            CredentialBalanceRefreshRequest(
                balance_script_id=script.id,
                api_key="sk-draft",
                base_url="https://draft.example",
                balance_params={"access_token": "draft-token"},
            ),
        )
    finally:
        app_state.balance_store = original_store
        app_state.settings_repo = original_settings
    assert saved.status == "ok"
    assert saved.balance == "5"
    stored = await channel_store.list_sites()
    assert stored[0].credentials[0].balance_script_id == ""
    await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_backup_keeps_scripts_but_not_balances(postgres_url: str) -> None:
    engine = create_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    channel_store = ChannelStore(factory)
    balance_store = BalanceStore(factory)
    backup_store = BackupStore(factory)
    script = await balance_store.create_script(
        BalanceScriptInput(
            name="static",
            code="print('ok')",
            timeout_seconds=10,
        )
    )
    await channel_store.create_site(
        SiteCreate(
            name="Alpha",
            base_urls=[SiteBaseUrlInput(id="base-1", url="https://example.com")],
            credentials=[
                SiteCredentialInput(
                    id="cred-1",
                    name="one",
                    api_key="sk-1",
                    base_url_id="base-1",
                    balance_script_id=script.id,
                )
            ],
            protocols=[
                SiteProtocolConfigInput(
                    id="pcfg-1",
                    name="combo",
                    protocols=[ProtocolKind.OPENAI_CHAT],
                    credential_id="cred-1",
                    proxy_mode=ChannelProxyMode.INHERIT,
                    base_url_id="base-1",
                    models=[],
                )
            ],
        )
    )
    await balance_store.save_outcome(
        "cred-1",
        ok=True,
        total="9",
        balance="4",
        currency="USD",
        error_message="",
    )
    dump = await backup_store.export_dump(
        lens_version="test", include_gateway_api_keys=False
    )
    assert dump.balance_scripts[0].id == script.id
    assert dump.sites[0].credentials[0].balance_script_id == script.id
    assert "credential_balances" not in dump.model_dump()

    async with factory() as session:
        await session.execute(text("DELETE FROM credential_balances"))
        await session.execute(text("DELETE FROM balance_scripts"))
        await session.commit()
    await backup_store.import_dump(dump)
    restored = await balance_store.list_scripts()
    site = (await channel_store.list_sites())[0]
    balances = await balance_store.list_balances()
    assert restored[0].code == "print('ok')"
    assert site.credentials[0].balance_script_id == script.id
    assert balances == []
    await engine.dispose()
