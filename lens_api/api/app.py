from collections.abc import Awaitable, Callable
from types import ModuleType

from fastapi import FastAPI, Request, Response

from .routes import include_routes


async def _normalize_duplicate_v1_prefix(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Some clients (e.g. pi-agent's Anthropic SDK) append /v1/messages to the
    # configured base URL, so a base_url already ending in /v1 produces a
    # duplicated /v1/v1/... path. Rewrite it so both conventions work.
    path = request.scope.get("path", "")
    if request.method == "POST" and path.startswith("/v1/v1/"):
        normalized = "/v1" + path[len("/v1/v1") :]
        request.scope["path"] = normalized
        request.scope["raw_path"] = normalized.encode()
    return await call_next(request)


def create_app(service_module: ModuleType) -> FastAPI:
    app = FastAPI(
        title=service_module.settings.app_name, lifespan=service_module.lifespan
    )
    app.middleware("http")(service_module.dynamic_cors_middleware)
    app.middleware("http")(_normalize_duplicate_v1_prefix)
    service_module.register_exception_handlers(app)
    include_routes(app, service_module)
    return app
