from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agentos.channels.asgi_app import DistributedAsgiApp
from agentos.channels.auth import ChannelAuthContext
from agentos.channels.service_wiring import (
    AuthenticationRequiredError,
    ChannelServices,
    FixedScopeAuthenticator,
)
from agentos.channels.websocket_endpoint import WebSocketEndpoint
from agentos.distributed.models import RequestScope
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.transports.http.request_types import HttpHeaders
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")


def _services() -> ChannelServices:
    return ChannelServices(
        RunSubmissionService(object()),  # type: ignore[arg-type]
        RunCommandService(object()),  # type: ignore[arg-type]
        RunQueryService(object()),  # type: ignore[arg-type]
        RunEventStream(object(), object()),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


@dataclass
class Authenticator:
    error: BaseException | None = None
    scope: object = SCOPE
    calls: list[tuple[HttpHeaders, ChannelAuthContext]] = field(default_factory=list)

    async def authenticate(
        self,
        headers: HttpHeaders,
        *,
        context: ChannelAuthContext,
    ) -> RequestScope:
        self.calls.append((headers, context))
        if self.error is not None:
            raise self.error
        return self.scope  # type: ignore[return-value]


async def _invoke(
    target: object,
    *,
    path: str = "/v1/ws",
    query_string: bytes = b"",
    subprotocols: object = ("agentos.run.v1",),
    headers: object = ((b"authorization", b"Bearer token"),),
    messages: tuple[dict[str, Any], ...] = (
        {"type": "websocket.connect"},
        {"type": "websocket.disconnect", "code": 1000},
    ),
) -> list[dict[str, Any]]:
    incoming = list(messages)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if not incoming:
            raise AssertionError("WebSocket endpoint read beyond test input")
        return incoming.pop(0)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await target(  # type: ignore[operator]
        {
            "type": "websocket",
            "path": path,
            "query_string": query_string,
            "headers": headers,
            "subprotocols": subprotocols,
        },
        receive,
        send,
    )
    return sent


@async_test
async def test_endpoint_authenticates_before_accept_and_selects_protocol() -> None:
    authenticator = Authenticator()
    endpoint = WebSocketEndpoint(_services(), authenticator)

    sent = await _invoke(endpoint)

    assert sent == [{"type": "websocket.accept", "subprotocol": "agentos.run.v1"}]
    headers, context = authenticator.calls[0]
    assert headers.get_all("authorization") == ("Bearer token",)
    assert context == ChannelAuthContext(
        operation="websocket_connect",
        method="WEBSOCKET",
        path="/v1/ws",
    )


@pytest.mark.parametrize(
    ("authenticator", "subprotocols"),
    [
        (Authenticator(error=AuthenticationRequiredError()), ("agentos.run.v1",)),
        (Authenticator(), ("other",)),
    ],
)
@async_test
async def test_handshake_failure_closes_without_accept(
    authenticator: Authenticator,
    subprotocols: tuple[str, ...],
) -> None:
    endpoint = WebSocketEndpoint(_services(), authenticator)

    sent = await _invoke(endpoint, subprotocols=subprotocols, messages=({"type": "websocket.connect"},))

    assert sent == [{"type": "websocket.close", "code": 1008}]


@async_test
async def test_invalid_authenticator_scope_fails_before_accept() -> None:
    sent = await _invoke(
        WebSocketEndpoint(_services(), Authenticator(scope=object())),
        messages=({"type": "websocket.connect"},),
    )

    assert sent == [{"type": "websocket.close", "code": 1011}]


@async_test
async def test_malformed_headers_are_rejected_before_accept() -> None:
    sent = await _invoke(
        WebSocketEndpoint(_services(), Authenticator()),
        headers=(b"not-a-pair",),
        messages=({"type": "websocket.connect"},),
    )

    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize(
    ("path", "query_string"),
    [("/other", b""), ("/v1/ws", b"tenant=attacker")],
)
@async_test
async def test_endpoint_rejects_unknown_path_and_query(
    path: str,
    query_string: bytes,
) -> None:
    sent = await _invoke(
        WebSocketEndpoint(_services(), Authenticator()),
        path=path,
        query_string=query_string,
        messages=({"type": "websocket.connect"},),
    )

    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize(
    ("message", "close_code"),
    [
        ({"type": "websocket.receive", "bytes": b"binary"}, 1003),
        ({"type": "websocket.receive", "text": "x" * (256 * 1024 + 1)}, 1009),
        ({"type": "http.request"}, 4400),
    ],
)
@async_test
async def test_distributed_asgi_app_routes_websocket_close_semantics(
    message: dict[str, Any],
    close_code: int,
) -> None:
    app = DistributedAsgiApp(
        _services(),
        authenticator=FixedScopeAuthenticator(SCOPE),
    )

    sent = await _invoke(
        app,
        messages=({"type": "websocket.connect"}, message),
    )

    assert sent == [
        {"type": "websocket.accept", "subprotocol": "agentos.run.v1"},
        {"type": "websocket.close", "code": close_code},
    ]
