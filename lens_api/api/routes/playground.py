from types import ModuleType

from fastapi import FastAPI


def register(app: FastAPI, service_module: ModuleType) -> None:
    app.add_api_route(
        "/api/admin/playground/chat",
        service_module.playground_chat,
        methods=["POST"],
    )
