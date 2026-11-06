from __future__ import annotations

import asyncio
from typing import Any

from llmwiki_serve.api import MCP_STREAM_PATH, McpStreamableHttpValidationMiddleware


def test_replayed_body_then_forwards_original_receive() -> None:
    """After replaying the buffered body, later receive() calls must reach the server.

    The MCP SDK's streamable HTTP handler waits for ``http.disconnect`` in a loop;
    returning empty ``http.request`` messages forever busy-loops the event loop
    (issue #67).
    """
    body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    upstream: list[dict[str, Any]] = [
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ]
    seen: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return upstream.pop(0)

    async def send(message: dict[str, Any]) -> None:
        pass

    async def inner(scope: Any, inner_receive: Any, inner_send: Any) -> None:
        seen.append(await inner_receive())
        seen.append(await asyncio.wait_for(inner_receive(), timeout=1))

    scope = {
        "type": "http",
        "method": "POST",
        "path": MCP_STREAM_PATH,
        "headers": [(b"accept", b"application/json, text/event-stream")],
    }
    asyncio.run(McpStreamableHttpValidationMiddleware(inner)(scope, receive, send))

    assert seen[0]["type"] == "http.request"
    assert seen[0]["body"] == body
    assert seen[1] == {"type": "http.disconnect"}
