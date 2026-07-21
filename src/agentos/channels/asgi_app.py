from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import parse_qsl
from uuid import uuid4

from agentos.channels.a2a_endpoint import A2AEndpoint
from agentos.channels.artifact_endpoint import ArtifactEndpoint
from agentos.channels.asgi_router import AsgiRouter, RouteMatch
from agentos.channels.run_endpoint import RunEndpoint
from agentos.channels.service_wiring import (
    ChannelAuthenticator,
    ChannelServices,
    RejectAllChannelAuthenticator,
)
from agentos.channels.sse_endpoint import RunSseEndpoint
from agentos.channels.stream_response import CloseableSseResponse
from agentos.transports.a2a import MAX_A2A_JSON_BYTES
from agentos.transports.http.errors import HttpValidationError, RequestTooLargeError
from agentos.transports.http.request_decoder import (
    MAX_JSON_BODY_BYTES,
    MAX_MULTIPART_BODY_BYTES,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.http.response_encoder import encode_error_response
from agentos.transports.http.response_types import HttpResponse


AsgiMessage = dict[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


class _ClientDisconnected(Exception):
    pass


class DistributedAsgiApp:
    """Thin ASGI adapter over stateless distributed Channel endpoints."""

    def __init__(
        self,
        services: ChannelServices,
        *,
        authenticator: ChannelAuthenticator | None = None,
        max_request_bytes: int = MAX_MULTIPART_BODY_BYTES,
        heartbeat_interval: float = 15.0,
        a2a_interface_tenant: str | None = None,
    ) -> None:
        if type(services) is not ChannelServices:
            raise TypeError("services must be ChannelServices")
        if (
            type(max_request_bytes) is not int
            or not 1 <= max_request_bytes <= MAX_MULTIPART_BODY_BYTES
        ):
            raise ValueError("max_request_bytes is invalid")
        selected_authenticator = authenticator or RejectAllChannelAuthenticator()
        self._router = AsgiRouter()
        self._run = RunEndpoint(services, selected_authenticator)
        self._artifacts = ArtifactEndpoint(services, selected_authenticator)
        self._a2a = A2AEndpoint(
            services,
            selected_authenticator,
            heartbeat_interval=heartbeat_interval,
            interface_tenant=a2a_interface_tenant,
        )
        self._sse = RunSseEndpoint(
            services,
            selected_authenticator,
            heartbeat_interval,
        )
        self._max_request_bytes = max_request_bytes

    async def __call__(
        self,
        scope: Mapping[str, Any],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await _lifespan(receive, send)
            return
        if scope_type != "http":
            return
        fallback_request_id = uuid4().hex
        request_id = fallback_request_id
        try:
            headers = _headers(scope.get("headers"))
            request_id = _request_id(headers, fallback_request_id)
            route = self._router.match(scope.get("method"), scope.get("path"))
            if route is None:
                await _send_response(send, _route_not_found(request_id))
                return
            body_limit = _body_limit(route.operation, self._max_request_bytes)
            body = await _read_body(receive, body_limit)
            response = await self._dispatch(
                route,
                headers,
                body,
                _query(scope.get("query_string"), route.operation),
                request_id,
            )
        except _ClientDisconnected:
            return
        except Exception as error:
            response = encode_error_response(error, request_id=request_id)
        if isinstance(response, CloseableSseResponse):
            await _send_stream(send, receive, response)
        else:
            await _send_response(send, response)

    async def _dispatch(
        self,
        route: RouteMatch,
        headers: HttpHeaders,
        body: bytes,
        query: Mapping[str, str],
        request_id: str,
    ) -> HttpResponse | CloseableSseResponse:
        values = route.parameters
        if route.operation == "a2a":
            return await self._a2a.handle(
                headers=headers,
                body=body,
                request_id=request_id,
            )
        if route.operation == "submit_run":
            return await self._run.submit(
                session_id=values["session_id"],
                headers=headers,
                body=body,
                request_id=request_id,
            )
        if route.operation == "submit_command":
            return await self._run.command(
                session_id=values["session_id"],
                run_id=values["run_id"],
                headers=headers,
                body=body,
                request_id=request_id,
            )
        if route.operation == "query_run":
            return await self._run.get(
                session_id=values["session_id"],
                run_id=values["run_id"],
                headers=headers,
                request_id=request_id,
            )
        if route.operation == "subscribe_run":
            return await self._sse.open(
                session_id=values["session_id"],
                run_id=values["run_id"],
                headers=headers,
                request_id=request_id,
            )
        if route.operation == "upload_artifact":
            return await self._artifacts.upload(
                session_id=values["session_id"],
                headers=headers,
                body=body,
                request_id=request_id,
            )
        if route.operation == "list_artifacts":
            return await self._artifacts.list(
                session_id=values["session_id"],
                headers=headers,
                cursor=query.get("cursor"),
                limit=query.get("limit"),
                request_id=request_id,
            )
        if route.operation == "read_artifact":
            return await self._artifacts.read(
                session_id=values["session_id"],
                artifact_id=values["artifact_id"],
                headers=headers,
                request_id=request_id,
            )
        if route.operation == "delete_artifact":
            return await self._artifacts.delete(
                session_id=values["session_id"],
                artifact_id=values["artifact_id"],
                headers=headers,
                request_id=request_id,
            )
        raise RuntimeError("router returned an unknown operation")


def _headers(value: object) -> HttpHeaders:
    if not isinstance(value, (list, tuple)):
        raise HttpValidationError()
    try:
        items = tuple(
            (name.decode("ascii"), header_value.decode("utf-8"))
            for name, header_value in value
        )
    except (AttributeError, UnicodeDecodeError, ValueError):
        raise HttpValidationError() from None
    return HttpHeaders(items)


def _request_id(headers: HttpHeaders, fallback: str) -> str:
    values = headers.get_all("x-request-id")
    if not values:
        return fallback
    value = values[0]
    if not value or value.strip() != value or any(character.isspace() for character in value):
        raise HttpValidationError()
    return value


def _body_limit(operation: str, configured_limit: int) -> int:
    if operation == "upload_artifact":
        return configured_limit
    hard_limit = (
        MAX_A2A_JSON_BYTES if operation == "a2a" else MAX_JSON_BODY_BYTES
    )
    return min(configured_limit, hard_limit)


async def _read_body(receive: AsgiReceive, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            raise _ClientDisconnected()
        if message.get("type") != "http.request":
            raise HttpValidationError()
        chunk = message.get("body", b"")
        if type(chunk) is not bytes:
            raise HttpValidationError()
        size += len(chunk)
        if size > limit:
            raise RequestTooLargeError()
        chunks.append(chunk)
        if message.get("more_body") is not True:
            return b"".join(chunks)


def _query(value: object, operation: str) -> Mapping[str, str]:
    if type(value) is not bytes:
        raise HttpValidationError()
    if not value:
        return {}
    if operation != "list_artifacts":
        raise HttpValidationError()
    try:
        pairs = parse_qsl(
            value.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
        )
    except (UnicodeDecodeError, ValueError):
        raise HttpValidationError() from None
    result: dict[str, str] = {}
    for name, item in pairs:
        if name not in {"cursor", "limit"} or name in result:
            raise HttpValidationError()
        result[name] = item
    return result


async def _send_response(send: AsgiSend, response: HttpResponse) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [
                (name.lower().encode("ascii"), value.encode("latin-1"))
                for name, value in response.headers
            ],
        },
    )
    await send({"type": "http.response.body", "body": response.body})


async def _send_stream(
    send: AsgiSend,
    receive: AsgiReceive,
    response: CloseableSseResponse,
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [
                (name.lower().encode("ascii"), value.encode("latin-1"))
                for name, value in response.headers
            ],
        },
    )
    disconnect = asyncio.create_task(receive())
    frame: asyncio.Task[bytes] | None = None
    try:
        while True:
            frame = asyncio.create_task(response.next_frame())
            done, _ = await asyncio.wait(
                (frame, disconnect),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect in done:
                if disconnect.result().get("type") == "http.disconnect":
                    frame.cancel()
                    try:
                        await frame
                    except asyncio.CancelledError:
                        pass
                    return
                raise HttpValidationError()
            try:
                payload = frame.result()
            except StopAsyncIteration:
                await send({"type": "http.response.body", "body": b""})
                return
            await send(
                {"type": "http.response.body", "body": payload, "more_body": True},
            )
            frame = None
    finally:
        if frame is not None and not frame.done():
            frame.cancel()
            try:
                await frame
            except asyncio.CancelledError:
                pass
        disconnect.cancel()
        try:
            await disconnect
        except asyncio.CancelledError:
            pass
        await response.aclose()


async def _lifespan(receive: AsgiReceive, send: AsgiSend) -> None:
    while True:
        message = await receive()
        if message.get("type") == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message.get("type") == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def _route_not_found(request_id: str) -> HttpResponse:
    body = json.dumps(
        {
            "code": "route_not_found",
            "message": "route not found",
            "request_id": request_id,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return HttpResponse(
        404,
        (
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ),
        body,
    )


__all__ = ["DistributedAsgiApp"]
