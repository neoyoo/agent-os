from __future__ import annotations

from collections import deque

import pytest

from agentos.channels.asgi_websocket import (
    AsgiWebSocketConnection,
    WebSocketBinaryFrame,
    WebSocketDisconnected,
    WebSocketFrameTooLarge,
    WebSocketProtocolViolation,
)
from tests.planning._async import async_test


class Harness:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = deque(messages)
        self.sent: list[dict[str, object]] = []

    async def receive(self) -> dict[str, object]:
        return self.messages.popleft()

    async def send(self, message: dict[str, object]) -> None:
        self.sent.append(message)


@async_test
async def test_accept_and_text_send_use_selected_subprotocol() -> None:
    harness = Harness(
        [
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": "hello"},
        ],
    )
    connection = AsgiWebSocketConnection(harness.receive, harness.send)

    await connection.receive_connect()
    await connection.accept("agentos.run.v1")
    assert await connection.receive_text(max_bytes=16) == "hello"
    await connection.send_text("world")
    await connection.close(1000)

    assert harness.sent == [
        {"type": "websocket.accept", "subprotocol": "agentos.run.v1"},
        {"type": "websocket.send", "text": "world"},
        {"type": "websocket.close", "code": 1000},
    ]


@async_test
async def test_pre_accept_close_does_not_send_accept() -> None:
    harness = Harness([{"type": "websocket.connect"}])
    connection = AsgiWebSocketConnection(harness.receive, harness.send)

    await connection.receive_connect()
    await connection.close(1000)
    await connection.close(1001)

    assert harness.sent == [{"type": "websocket.close", "code": 1000}]


@async_test
async def test_binary_oversize_and_disconnect_are_distinct() -> None:
    binary = Harness([{"type": "websocket.receive", "bytes": b"x"}])
    with pytest.raises(WebSocketBinaryFrame):
        await AsgiWebSocketConnection(binary.receive, binary.send).receive_text(
            max_bytes=8,
        )

    oversize = Harness([{"type": "websocket.receive", "text": "中中中"}])
    with pytest.raises(WebSocketFrameTooLarge):
        await AsgiWebSocketConnection(oversize.receive, oversize.send).receive_text(
            max_bytes=8,
        )

    disconnected = Harness([{"type": "websocket.disconnect", "code": 1001}])
    with pytest.raises(WebSocketDisconnected):
        await AsgiWebSocketConnection(
            disconnected.receive,
            disconnected.send,
        ).receive_text(max_bytes=8)


@async_test
async def test_invalid_asgi_message_is_protocol_violation() -> None:
    harness = Harness([{"type": "http.request"}])

    with pytest.raises(WebSocketProtocolViolation):
        await AsgiWebSocketConnection(harness.receive, harness.send).receive_connect()
