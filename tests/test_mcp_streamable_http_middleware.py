from __future__ import annotations

import asyncio
from typing import Any

import pytest

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


def test_chunked_body_is_replayed_once() -> None:
    body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    upstream = [
        {"type": "http.request", "body": body[:12], "more_body": True},
        {"type": "http.request", "body": body[12:], "more_body": False},
        {"type": "http.disconnect"},
    ]
    seen: list[dict[str, Any]] = []

    async def receive() -> Any:
        return upstream.pop(0)

    async def send(message: Any) -> None:
        pytest.fail("Unexpected response from middleware")

    async def inner(scope: Any, inner_receive: Any, inner_send: Any) -> None:
        seen.append(await inner_receive())
        seen.append(await inner_receive())

    scope = {
        "type": "http",
        "method": "POST",
        "path": MCP_STREAM_PATH,
        "headers": [(b"accept", b"application/json, text/event-stream")],
    }
    asyncio.run(McpStreamableHttpValidationMiddleware(inner)(scope, receive, send))

    assert seen == [
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ]
    assert upstream == []


def test_receive_after_replay_blocks_and_propagates_cancellation() -> None:
    async def scenario() -> None:
        body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
        calls = 0
        cancelled = asyncio.Event()

        async def receive() -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"type": "http.request", "body": body, "more_body": False}
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def send(message: Any) -> None:
            pytest.fail("Unexpected response from middleware")

        async def inner(scope: Any, inner_receive: Any, inner_send: Any) -> None:
            assert (await inner_receive())["body"] == body
            pending = asyncio.create_task(inner_receive())
            try:
                await asyncio.sleep(0)
                assert calls == 2
                assert not pending.done()
            finally:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert cancelled.is_set()

        scope = {
            "type": "http",
            "method": "POST",
            "path": MCP_STREAM_PATH,
            "headers": [(b"accept", b"application/json, text/event-stream")],
        }
        await McpStreamableHttpValidationMiddleware(inner)(scope, receive, send)

    asyncio.run(scenario())


@pytest.mark.parametrize("path", [MCP_STREAM_PATH, "/stream"])
@pytest.mark.parametrize("partial_body", [False, True])
def test_disconnect_while_buffering_stops_receiving(path: str, partial_body: bool) -> None:
    upstream: list[dict[str, Any]] = []
    if partial_body:
        upstream.append({"type": "http.request", "body": b'{"jsonrpc":', "more_body": True})
    upstream.append({"type": "http.disconnect"})

    async def receive() -> Any:
        assert upstream, "receive called again after http.disconnect"
        return upstream.pop(0)

    async def send(message: Any) -> None:
        pytest.fail("A disconnected client must not receive a response")

    async def inner(scope: Any, inner_receive: Any, inner_send: Any) -> None:
        pytest.fail("An incomplete request must not reach the downstream app")

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"accept", b"application/json, text/event-stream")],
    }
    asyncio.run(McpStreamableHttpValidationMiddleware(inner)(scope, receive, send))
    assert upstream == []
