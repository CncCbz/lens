from __future__ import annotations

from ...core.protocol_reachability import CHAT_PROTOCOLS
from ...models import PlaygroundChatRequest
from ..converters import convert_request, convert_response, convert_stream_iterator
from ..converters.chat_to_bedrock import bedrock_eventstream_to_sse
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
    app_state,
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
    protocol = ProtocolKind.OPENAI_CHAT
    model = payload.request.get("model")
    group = (
        await app_state.group_repo.find_group_by_name(model)
        if isinstance(model, str)
        else None
    )
    if group is not None:
        protocols = [item for item in group.protocols if item in CHAT_PROTOCOLS]
        if not protocols:
            raise ValueError("Playground chat requires a chat protocol")
        if protocol not in protocols:
            protocol = protocols[0]
    body = convert_request(ProtocolKind.OPENAI_CHAT, protocol, dict(payload.request))
    response = await _proxy_protocol(
        protocol,
        body,
        _playground_gateway_key(),
        request.headers.get("user-agent"),
        inbound_headers,
        request_headers=inbound_headers,
        forced_target=payload.target,
    )
    if isinstance(response, StreamingResponse):
        if protocol != ProtocolKind.OPENAI_CHAT:
            iterator = response.body_iterator
            if (
                protocol == ProtocolKind.BEDROCK_CONVERSE_STREAM
                and "eventstream" in response.headers.get("content-type", "")
            ):
                iterator = bedrock_eventstream_to_sse(iterator)
            response.body_iterator = convert_stream_iterator(
                ProtocolKind.OPENAI_CHAT, protocol, iterator
            )
            response.headers["content-type"] = "text/event-stream; charset=utf-8"
        # ``no-transform`` keeps dev proxies (Next.js) from gzip-buffering SSE.
        response.headers["cache-control"] = "no-cache, no-transform"
    elif protocol != ProtocolKind.OPENAI_CHAT and response.status_code < 400:
        response.body = convert_response(
            ProtocolKind.OPENAI_CHAT, protocol, response.body
        )
        response.headers["content-length"] = str(len(response.body))
        response.headers["content-type"] = "application/json"
    return response
