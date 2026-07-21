from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias


AsgiWebSocketMessage: TypeAlias = dict[str, Any]
AsgiWebSocketReceive: TypeAlias = Callable[[], Awaitable[AsgiWebSocketMessage]]
AsgiWebSocketSend: TypeAlias = Callable[[AsgiWebSocketMessage], Awaitable[None]]


class WebSocketDisconnected(ConnectionError):
    """The peer disconnected without requesting Run cancellation."""


class WebSocketBinaryFrame(ValueError):
    """The protocol accepts text frames only."""


class WebSocketFrameTooLarge(ValueError):
    """An inbound text frame exceeds the hard byte limit."""


class WebSocketProtocolViolation(ValueError):
    """The ASGI server produced an invalid WebSocket message sequence."""


class AsgiWebSocketConnection:
    """Small stateful adapter over ASGI WebSocket receive/send messages."""

    def __init__(self, receive: AsgiWebSocketReceive, send: AsgiWebSocketSend) -> None:
        self._receive = receive
        self._send = send
        self._connected = False
        self._accepted = False
        self._closed = False

    async def receive_connect(self) -> None:
        if self._connected or self._closed:
            raise WebSocketProtocolViolation()
        message = await self._receive()
        if message.get("type") != "websocket.connect":
            raise WebSocketProtocolViolation()
        self._connected = True

    async def accept(self, subprotocol: str) -> None:
        if not self._connected or self._accepted or self._closed:
            raise WebSocketProtocolViolation()
        if type(subprotocol) is not str or not subprotocol:
            raise ValueError("subprotocol must be a non-empty string")
        await self._send({"type": "websocket.accept", "subprotocol": subprotocol})
        self._accepted = True

    async def receive_text(self, *, max_bytes: int) -> str:
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self._closed:
            raise WebSocketDisconnected()
        message = await self._receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            self._closed = True
            raise WebSocketDisconnected()
        if message_type != "websocket.receive":
            raise WebSocketProtocolViolation()
        if type(message.get("bytes")) is bytes:
            raise WebSocketBinaryFrame()
        text = message.get("text")
        if type(text) is not str:
            raise WebSocketProtocolViolation()
        if len(text.encode("utf-8")) > max_bytes:
            raise WebSocketFrameTooLarge()
        return text

    async def send_text(self, text: str) -> None:
        if not self._accepted or self._closed:
            raise WebSocketDisconnected()
        if type(text) is not str:
            raise TypeError("WebSocket frame text must be str")
        await self._send({"type": "websocket.send", "text": text})

    async def close(self, code: int, reason: str = "") -> None:
        if self._closed:
            return
        if type(code) is not int or not 1000 <= code <= 4999:
            raise ValueError("WebSocket close code is invalid")
        if type(reason) is not str:
            raise TypeError("WebSocket close reason must be str")
        message: AsgiWebSocketMessage = {"type": "websocket.close", "code": code}
        if reason:
            message["reason"] = reason
        self._closed = True
        await self._send(message)


__all__ = [
    "AsgiWebSocketConnection",
    "AsgiWebSocketMessage",
    "AsgiWebSocketReceive",
    "AsgiWebSocketSend",
    "WebSocketBinaryFrame",
    "WebSocketDisconnected",
    "WebSocketFrameTooLarge",
    "WebSocketProtocolViolation",
]
