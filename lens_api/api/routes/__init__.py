from types import ModuleType

from fastapi import FastAPI

from . import (
    admin_auth,
    balance_scripts,
    backups,
    cronjobs,
    gateway_api_keys,
    model_groups,
    model_prices,
    model_verifications,
    models_config,
    multimodal_relay,
    overview,
    playground,
    proxy,
    provider_proxy,
    public,
    request_logs,
    routing,
    settings,
    sites,
    ui_static,
)


def include_routes(app: FastAPI, service_module: ModuleType) -> None:
    for module in (
        public,
        admin_auth,
        sites,
        balance_scripts,
        routing,
        overview,
        request_logs,
        model_groups,
        model_prices,
        model_verifications,
        models_config,
        multimodal_relay,
        cronjobs,
        gateway_api_keys,
        backups,
        settings,
        playground,
        proxy,
        provider_proxy,
        ui_static,
    ):
        module.register(app, service_module)
