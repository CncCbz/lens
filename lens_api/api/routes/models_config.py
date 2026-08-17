from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/api/admin/models/config",
        service_module.export_models_config,
        methods=["GET"],
        response_model=service_module.PiConfigExportResponse,
    )
    app.add_api_route(
        "/api/admin/model-groups/{group_id}/pi-config",
        service_module.generate_group_pi_config,
        methods=["GET"],
        response_model=service_module.PiConfigGenerateResponse,
    )
