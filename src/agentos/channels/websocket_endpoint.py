from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from agentos.channels.asgi_headers import headers_from_asgi_scope
from agentos.channels.asgi_websocket import (
    AsgiWebSocketConnection,
    AsgiWebSocketReceive,
    AsgiWebSocketSend,
    WebSocketProtocolViolation,
)
from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import ChannelAuthenticator, ChannelServices
from agentos.channels.websocket_buffer import WebSocketBufferLimits
from agentos.channels.websocket_session import WebSocketSession
from agentos.distributed.models import RequestScope
from agentos.transports.http import (
    AuthenticationRequiredError,
    HttpValidationError,
    PermissionDeniedError,
)


WEBSOCKET_PATH = "/v1/ws"
WEBSOCKET_SUBPROTOCOL = "agentos.run.v1"


class WebSocketEndpoint:
    """Authenticates the ASGI handshake before opening a Run session."""

    def __init__(
        self,
        services: ChannelServices,
        authenticator: ChannelAuthenticator,
        *,
        buffer_limits: WebSocketBufferLimits | None = None,
        max_subscriptions: int = 32,
    ) -> None:
        if type(services) is not ChannelServices:
            raise TypeError("services must be ChannelServices")
        selected_limits = buffer_limits or WebSocketBufferLimits()
        if type(selected_limits) is not WebSocketBufferLimits:
            raise TypeError("buffer_limits must be WebSocketBufferLimits")
        if type(max_subscriptions) is not int or not 1 <= max_subscriptions <= 128:
            raise ValueError("max_subscriptions is invalid")
        self._services = services
        self._authenticator = authenticator
        self._buffer_limits = selected_limits
        self._max_subscriptions = max_subscriptions

    async def __call__(
        self,
        scope: Mapping[str, Any],
        receive: AsgiWebSocketReceive,
        send: AsgiWebSocketSend,
    ) -> None:
        connection = AsgiWebSocketConnection(receive, send)
        try:
            await connection.receive_connect()
            headers = self._validate_handshake(scope)
            handshake_scope = await self._authenticator.authenticate(
                headers,
                context=ChannelAuthContext(
                    operation="websocket_connect",
                    method="WEBSOCKET",
                    path=WEBSOCKET_PATH,
                ),
            )
            if type(handshake_scope) is not RequestScope:
                raise TypeError("authenticator returned an invalid scope")
            await connection.accept(WEBSOCKET_SUBPROTOCOL)
        except (
            AuthenticationRequiredError,
            HttpValidationError,
            PermissionDeniedError,
            WebSocketProtocolViolation,
        ):
            await connection.close(1008)
            return
        except asyncio.CancelledError:
            await connection.close(1001)
            raise
        except BaseException:
            await connection.close(1011)
            return
        await WebSocketSession(
            services=self._services,
            authenticator=self._authenticator,
            handshake_scope=handshake_scope,
            headers=headers,
            connection=connection,
            buffer_limits=self._buffer_limits,
            max_subscriptions=self._max_subscriptions,
        ).run()

    def _validate_handshake(self, scope: Mapping[str, Any]):
        if scope.get("path") != WEBSOCKET_PATH:
            raise HttpValidationError()
        query_string = scope.get("query_string")
        if type(query_string) is not bytes or query_string:
            raise HttpValidationError()
        subprotocols = scope.get("subprotocols")
        if not isinstance(subprotocols, (list, tuple)) or any(
            type(value) is not str for value in subprotocols
        ):
            raise HttpValidationError()
        if WEBSOCKET_SUBPROTOCOL not in subprotocols:
            raise HttpValidationError()
        return headers_from_asgi_scope(scope.get("headers"))


__all__ = ["WEBSOCKET_PATH", "WEBSOCKET_SUBPROTOCOL", "WebSocketEndpoint"]
