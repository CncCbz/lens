from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from ..converters.chat_to_bedrock import bedrock_eventstream_to_sse
from ..converters.provider_variants import codex_stream_to_responses_stream
from ...models import ProtocolKind


class ProviderEventStream(httpx.AsyncByteStream):
    def __init__(self, response: httpx.Response, protocol: ProtocolKind) -> None:
        self.response = response
        self.protocol = protocol

    async def __aiter__(self) -> AsyncIterator[bytes]:
        source = self.response.aiter_bytes()
        if self.protocol == ProtocolKind.BEDROCK_CONVERSE_STREAM:
            source = bedrock_eventstream_to_sse(source)
        elif self.protocol == ProtocolKind.OPENAI_CODEX_RESPONSES:
            source = codex_stream_to_responses_stream(source)
        async for chunk in source:
            yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()


def normalize_provider_stream(
    response: httpx.Response, protocol: ProtocolKind
) -> httpx.Response:
    media = response.headers.get("content-type", "").lower()
    if not response.is_success:
        return response
    if protocol == ProtocolKind.BEDROCK_CONVERSE_STREAM:
        if "application/vnd.amazon.eventstream" not in media:
            return response
    elif protocol == ProtocolKind.OPENAI_CODEX_RESPONSES:
        if "text/event-stream" not in media:
            return response
    else:
        return response
    headers = dict(response.headers)
    headers["content-type"] = "text/event-stream"
    headers.pop("content-length", None)
    headers.pop("content-encoding", None)
    return httpx.Response(
        response.status_code,
        headers=headers,
        stream=ProviderEventStream(response, protocol),
        request=response.request,
    )
