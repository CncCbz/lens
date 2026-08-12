from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/api/admin/multimodal-relay",
        service_module.get_multimodal_relay_config,
        methods=["GET"],
        response_model=service_module.MultimodalRelayConfig,
    )
    app.add_api_route(
        "/api/admin/multimodal-relay",
        service_module.update_multimodal_relay_config,
        methods=["PUT"],
        response_model=service_module.MultimodalRelayConfig,
    )
