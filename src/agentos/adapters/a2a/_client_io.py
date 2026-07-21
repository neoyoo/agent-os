from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
import ipaddress
import re
import socket
import ssl
from typing import Protocol


_MAX_RESPONSE_HEADER_BYTES = 64 * 1024
_HEADER_TOKEN = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")


class _Reader(Protocol):
    async def readline(self) -> bytes: ...


class _Writer(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...
    def get_extra_info(self, name: str) -> object: ...
    def close(self) -> None: ...
    async def wait_closed(self) -> None: ...


class _SendGate(Protocol):
    def authorize_send(self) -> AbstractAsyncContextManager[None]: ...


class _SendGateError(Exception):
    def __init__(self, error: Exception) -> None:
        self.error = error


class _SendFailure(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    location: str | None


async def _resolve_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    rows = await asyncio.get_running_loop().getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(row[4][0]) for row in rows)


async def _open_tls_connection(
    *,
    address: str,
    port: int,
    server_hostname: str,
    ssl_context: ssl.SSLContext,
    timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
    return await asyncio.wait_for(
        asyncio.open_connection(
            host=address,
            port=port,
            family=family,
            ssl=ssl_context,
            server_hostname=server_hostname,
            ssl_handshake_timeout=timeout,
        ),
        timeout=timeout,
    )


def _verify_peer(writer: _Writer, expected: str, security_error: type[Exception]) -> None:
    peer = writer.get_extra_info("peername")
    if not isinstance(peer, tuple) or not peer or type(peer[0]) is not str:
        raise security_error()
    try:
        actual = str(ipaddress.ip_address(peer[0].split("%", 1)[0]))
    except ValueError as error:
        raise security_error() from error
    if actual != expected:
        raise security_error()


def _request_bytes(
    *,
    request_target: str,
    host_header: str,
    body: bytes,
    authorization: str | None,
    notification_token: str | None,
) -> bytes:
    headers = [
        f"POST {request_target} HTTP/1.1",
        f"Host: {host_header}",
        "Content-Type: application/a2a+json",
        "Accept: application/a2a+json",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    if authorization is not None:
        headers.append(f"Authorization: {authorization}")
    if notification_token is not None:
        headers.append(f"X-A2A-Notification-Token: {notification_token}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


async def _write_through_gate(
    *,
    writer: _Writer,
    request: bytes,
    send_gate: _SendGate,
    timeout: float,
) -> None:
    try:
        context = send_gate.authorize_send()
        await context.__aenter__()
    except Exception as error:
        raise _SendGateError(error) from error
    try:
        writer.write(request)
        await asyncio.wait_for(writer.drain(), timeout=timeout)
    except BaseException as error:
        try:
            await context.__aexit__(type(error), error, error.__traceback__)
        except Exception as gate_error:
            raise _SendGateError(gate_error) from gate_error
        if not isinstance(error, Exception):
            raise
        raise _SendFailure() from error
    try:
        await context.__aexit__(None, None, None)
    except Exception as error:
        raise _SendGateError(error) from error


async def _read_response(
    reader: _Reader,
    *,
    timeout: float,
    max_body_bytes: int,
    delivery_error: type[Exception],
    response_too_large_error: type[Exception],
) -> _Response:
    deadline = asyncio.get_running_loop().time() + timeout
    remaining = _MAX_RESPONSE_HEADER_BYTES
    status_line, remaining = await _read_header_line(
        reader,
        deadline,
        remaining,
        delivery_error,
    )
    match = re.fullmatch(
        rb"HTTP/1\.[01] ([1-5][0-9]{2})(?: [^\r\n]*)?\r\n",
        status_line,
    )
    if match is None:
        raise delivery_error()
    headers: dict[str, str] = {}
    while True:
        line, remaining = await _read_header_line(
            reader,
            deadline,
            remaining,
            delivery_error,
        )
        if line == b"\r\n":
            break
        name, separator, raw_value = line[:-2].partition(b":")
        if not separator or _HEADER_TOKEN.fullmatch(name) is None:
            raise delivery_error()
        key = name.decode("ascii").lower()
        if key in headers:
            raise delivery_error()
        try:
            value = raw_value.strip().decode("ascii")
        except UnicodeDecodeError as error:
            raise delivery_error() from error
        headers[key] = value
    content_length = headers.get("content-length")
    if content_length is not None:
        if not content_length.isdigit():
            raise delivery_error()
        if int(content_length) > max_body_bytes:
            raise response_too_large_error()
    return _Response(int(match.group(1)), headers.get("location"))


async def _read_header_line(
    reader: _Reader,
    deadline: float,
    remaining: int,
    delivery_error: type[Exception],
) -> tuple[bytes, int]:
    timeout = deadline - asyncio.get_running_loop().time()
    if timeout <= 0:
        raise delivery_error()
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    except asyncio.TimeoutError as error:
        raise delivery_error() from error
    if not line or not line.endswith(b"\r\n") or len(line) > remaining:
        raise delivery_error()
    return line, remaining - len(line)


async def _close_writer(writer: _Writer, timeout: float) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=timeout)
    except Exception:
        pass
