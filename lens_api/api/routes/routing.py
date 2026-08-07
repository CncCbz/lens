from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/{path:path}",
        service_module.cors_preflight,
        methods=["OPTIONS"],
        status_code=204,
    )
    app.add_api_route(
        "/api/admin/routes", service_module.router_snapshot, methods=["GET"]
    )
    app.add_api_route(
        "/api/admin/routes/cooldowns",
        service_module.router_cooldowns,
        methods=["GET"],
    )
    app.add_api_route(
        "/api/admin/routes/preview",
        service_module.route_preview,
        methods=["POST"],
        response_model=service_module.RoutePreviewResponse,
    )
    app.add_api_route(
        "/api/admin/routes/{channel_id}/cooldown",
        service_module.clear_router_cooldown,
        methods=["DELETE"],
    )
