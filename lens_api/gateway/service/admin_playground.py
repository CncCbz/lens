from __future__ import annotations

from ...models import PlaygroundChatRequest
from .auth import get_current_admin
from .proxy_flow import PLAYGROUND_GATEWAY_KEY_ID, _proxy_protocol
from .runtime_context import (
    UTC,
    AdminUserEntity,
    Depends,
    GatewayApiKey,
    ProtocolKind,
    Request,
    Response,
    StreamingResponse,
    datetime,
)


def _playground_gateway_key() -> GatewayApiKey:
    now = datetime.now(UTC).isoformat()
    return GatewayApiKey(
        id=PLAYGROUND_GATEWAY_KEY_ID,
        api_key="",
        remark="playground",
        enabled=True,
        created_at=now,
        updated_at=now,
    )


async def playground_chat(
    payload: PlaygroundChatRequest,
    request: Request,
    admin: AdminUserEntity = Depends(get_current_admin),
) -> Response:
    del admin
    inbound_headers = {key: value for key, value in request.headers.items()}
    response = await _proxy_protocol(
        ProtocolKind.OPENAI_CHAT,
        dict(payload.request),
        _playground_gateway_key(),
        request.headers.get("user-agent"),
        inbound_headers,
        request_headers=inbound_headers,
        forced_target=payload.target,
    )
    if isinstance(response, StreamingResponse):
        # ``no-transform`` keeps dev proxies (Next.js) from gzip-buffering SSE.
        response.headers["cache-control"] = "no-cache, no-transform"
    return response
