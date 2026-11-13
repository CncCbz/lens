from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import ModuleType

from fastapi import Depends, FastAPI, Request, Response

from ...models import GatewayApiKey, ProtocolKind


def register(app: FastAPI, service_module: ModuleType) -> None:
    from ...gateway.service.auth import get_current_gateway_key

    def endpoint(
        protocol: ProtocolKind,
        *,
        stream: bool | None = None,
        operation: str | None = None,
    ) -> Callable[..., Awaitable[Response]]:
        async def handle(
            request: Request,
            gateway_key: GatewayApiKey = Depends(get_current_gateway_key),
        ) -> Response:
            return await service_module.proxy_provider_request(
                request,
                gateway_key,
                protocol=protocol,
                stream=stream,
                path_suffix=operation,
            )

        return handle

    routes: list[tuple[str, ProtocolKind, bool | None, str | None]] = [
        (
            "/mistral/v1/chat/completions",
            ProtocolKind.MISTRAL_CONVERSATIONS,
            None,
            None,
        ),
        ("/openai/v1/responses", ProtocolKind.AZURE_OPENAI_RESPONSES, None, None),
        (
            "/openai/deployments/{deployment}/responses",
            ProtocolKind.AZURE_OPENAI_RESPONSES,
            None,
            None,
        ),
        (
            "/backend-api/codex/responses",
            ProtocolKind.OPENAI_CODEX_RESPONSES,
            None,
            None,
        ),
        ("/codex/responses", ProtocolKind.OPENAI_CODEX_RESPONSES, None, None),
        ("/messages", ProtocolKind.PI_MESSAGES, True, None),
        ("/pi/messages", ProtocolKind.PI_MESSAGES, True, None),
        (
            "/openrouter/v1/chat/completions",
            ProtocolKind.OPENROUTER_IMAGES,
            False,
            None,
        ),
        (
            "/model/{model_name:path}/converse",
            ProtocolKind.BEDROCK_CONVERSE_STREAM,
            False,
            None,
        ),
        (
            "/model/{model_name:path}/converse-stream",
            ProtocolKind.BEDROCK_CONVERSE_STREAM,
            True,
            None,
        ),
        ("/systemone", ProtocolKind.TYPESAFE_SYSTEM_ONE, False, None),
        ("/v1/systemone", ProtocolKind.TYPESAFE_SYSTEM_ONE, False, None),
        (
            "/accounts/{account_id}/ai/run",
            ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
            False,
            None,
        ),
        (
            "/client/v4/accounts/{account_id}/ai/run",
            ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
            False,
            None,
        ),
        ("/v1/decisions", ProtocolKind.OPENAI_DECISIONS, False, None),
    ]
    for version in ("v1", "v1beta1"):
        for prefix in (
            f"/{version}/projects/{{project}}/locations/{{location}}/publishers/{{publisher}}",
            f"/{version}/publishers/{{publisher}}",
        ):
            for method, stream in (
                ("generateContent", False),
                ("streamGenerateContent", True),
            ):
                routes.append(
                    (
                        f"{prefix}/models/{{model_name:path}}:{method}",
                        ProtocolKind.GOOGLE_VERTEX,
                        stream,
                        None,
                    )
                )
    for operation in ("tokenize", "apply-template", "completion"):
        for prefix in ("", "/llama/{model_name:path}"):
            routes.append(
                (
                    f"{prefix}/{operation}",
                    ProtocolKind.LLAMA_CPP_CLASSIFY,
                    False,
                    operation,
                )
            )
    for index, (path, protocol, stream, operation) in enumerate(routes):
        app.add_api_route(
            path,
            endpoint(protocol, stream=stream, operation=operation),
            methods=["POST"],
            name=f"proxy_{protocol.value}_{index}",
        )
