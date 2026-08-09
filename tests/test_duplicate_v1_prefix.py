from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lens_api.api.app import _normalize_duplicate_v1_prefix


def _build_app() -> FastAPI:
    app = FastAPI()

    async def messages() -> dict[str, str]:
        return {"ok": "messages"}

    async def chat() -> dict[str, str]:
        return {"ok": "chat"}

    async def ui_entry(path: str = "") -> dict[str, str]:
        return {"spa": path}

    app.add_api_route("/v1/chat/completions", chat, methods=["POST"])
    app.add_api_route("/v1/messages", messages, methods=["POST"])
    app.middleware("http")(_normalize_duplicate_v1_prefix)
    app.add_api_route("/", ui_entry, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route(
        "/{path:path}", ui_entry, methods=["GET", "HEAD"], include_in_schema=False
    )
    return app


def test_duplicate_v1_prefix_routes_to_canonical_endpoint() -> None:
    client = TestClient(_build_app())

    # base_url WITHOUT /v1 -> POST /v1/messages (unchanged behavior)
    assert client.post("/v1/messages").status_code == 200

    # base_url WITH /v1 -> Anthropic SDK appends /v1/messages -> /v1/v1/messages
    assert client.post("/v1/v1/messages").status_code == 200
    assert client.post("/v1/v1/chat/completions").status_code == 200


def test_duplicate_v1_prefix_keeps_get_spa_behavior() -> None:
    client = TestClient(_build_app())

    # GET paths are not rewritten; they keep hitting the UI catch-all.
    assert client.get("/v1/v1/messages").status_code == 200
    assert client.get("/v1/v1/messages").json() == {"spa": "v1/v1/messages"}
