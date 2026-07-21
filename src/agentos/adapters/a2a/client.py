from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
import ipaddress
import math
import re
import ssl
from typing import Protocol, cast
from urllib.parse import quote, urljoin, urlsplit

from agentos.adapters.a2a._client_io import (
    _Response,
    _SendGateError,
    _Writer,
    _close_writer,
    _open_tls_connection,
    _read_response,
    _request_bytes,
    _resolve_public_addresses,
    _verify_peer,
    _write_through_gate,
)


_MAX_STREAM_RESPONSE_BYTES = 1024 * 1024
_MAX_URL_BYTES = 2048
_MAX_AUTHORIZATION_BYTES = 8192
_MAX_NOTIFICATION_TOKEN_BYTES = 8192
_MAX_SEND_GATE_TIMEOUT = 5.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AUTH_SCHEME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")


class A2APushClientError(RuntimeError):
    """Outbound A2A Push 的安全错误基类。"""


class A2APushSecurityError(A2APushClientError):
    """URL、DNS 或实际 peer 未通过 SSRF 门禁。"""

    def __init__(self) -> None:
        super().__init__("a2a push destination is not allowed")


class A2APushDeliveryError(A2APushClientError):
    """连接或 HTTP 响应无法安全完成。"""

    def __init__(self) -> None:
        super().__init__("a2a push delivery failed")


class A2APushAcknowledgementError(A2APushClientError):
    """Webhook 未使用 2xx 确认接收。"""

    def __init__(self) -> None:
        super().__init__("a2a push receiver did not acknowledge delivery")


class A2APushResponseTooLargeError(A2APushClientError):
    """Webhook 声明的响应超过接收上限。"""

    def __init__(self) -> None:
        super().__init__("a2a push response exceeds maximum size")


class A2APushClientClosedError(A2APushClientError):
    """HTTP client 已开始或完成关闭。"""

    def __init__(self) -> None:
        super().__init__("a2a push client is closed")


class A2APushSendGate(Protocol):
    """为单个 HTTP hop 授权不可分割的 write/drain 窗口。"""

    def authorize_send(self) -> AbstractAsyncContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class _Target:
    hostname: str
    port: int
    request_target: str
    host_header: str

    @property
    def origin(self) -> tuple[str, int]:
        return self.hostname, self.port


@dataclass(slots=True)
class _Lifecycle:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    active: set[asyncio.Task[object]] = field(default_factory=set)
    closing: bool = False


class A2APushHttpClient:
    """发送单次 ProtoJSON StreamResponse，并在每跳执行 SSRF 防护。"""

    def __init__(
        self,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 20.0,
        send_gate_timeout: float = 2.0,
        max_response_bytes: int = 64 * 1024,
        max_concurrency: int = 16,
        follow_redirects: bool = False,
        max_redirects: int = 3,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        for value, name in (
            (connect_timeout, "connect_timeout"),
            (read_timeout, "read_timeout"),
            (send_gate_timeout, "send_gate_timeout"),
        ):
            if (
                type(value) not in {int, float}
                or value <= 0
                or (type(value) is float and not math.isfinite(value))
            ):
                raise ValueError(f"{name} must be positive")
        if send_gate_timeout > _MAX_SEND_GATE_TIMEOUT:
            raise ValueError("send_gate_timeout cannot exceed 5 seconds")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        if type(follow_redirects) is not bool:
            raise TypeError("follow_redirects must be bool")
        if type(max_redirects) is not int or not 0 <= max_redirects <= 3:
            raise ValueError("max_redirects must be between 0 and 3")
        context = ssl.create_default_context() if ssl_context is None else ssl_context
        if type(context) is not ssl.SSLContext:
            raise TypeError("ssl_context must be SSLContext or None")
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("ssl_context must verify hostnames and certificates")
        self._connect_timeout = float(connect_timeout)
        self._read_timeout = float(read_timeout)
        self._send_gate_timeout = float(send_gate_timeout)
        self._max_response_bytes = max_response_bytes
        self._follow_redirects = follow_redirects
        self._max_redirects = max_redirects
        self._ssl_context = context
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._lifecycle = _Lifecycle()

    async def deliver(
        self,
        *,
        url: str,
        stream_response: bytes,
        send_gate: A2APushSendGate | None = None,
        authentication_scheme: str | None = None,
        credentials: str | None = None,
        token: str | None = None,
    ) -> None:
        """POST 一个 direct StreamResponse；仅 2xx 表示 ACK。"""

        body = _require_stream_response(stream_response)
        authorization = _authorization(authentication_scheme, credentials)
        notification_token = _notification_token(token)
        target = _parse_target(url)
        gate = _require_send_gate(send_gate)
        task = await self._register()
        try:
            async with self._semaphore:
                await self._deliver(
                    target=target,
                    body=body,
                    send_gate=gate,
                    authorization=authorization,
                    notification_token=notification_token,
                )
        finally:
            await self._unregister(task)

    async def close(self) -> None:
        """停止新请求，取消并回收当前 delivery。"""

        lifecycle = self._lifecycle
        current = asyncio.current_task()
        async with lifecycle.lock:
            if lifecycle.closed.is_set():
                return
            if current in lifecycle.active:
                raise RuntimeError("active delivery cannot close its own client")
            first_closer = not lifecycle.closing
            lifecycle.closing = True
            active = tuple(lifecycle.active) if first_closer else ()
            if first_closer and not active:
                lifecycle.closed.set()
        if first_closer:
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
        await lifecycle.closed.wait()

    async def _deliver(
        self,
        *,
        target: _Target,
        body: bytes,
        send_gate: A2APushSendGate,
        authorization: str | None,
        notification_token: str | None,
    ) -> None:
        current = target
        send_authorization = authorization
        send_notification_token = notification_token
        for redirect_count in range(self._max_redirects + 1):
            response = await self._send_once(
                current,
                body,
                send_gate,
                send_authorization,
                send_notification_token,
            )
            if 200 <= response.status < 300:
                return
            if (
                not self._follow_redirects
                or response.status not in _REDIRECT_STATUSES
                or response.location is None
                or redirect_count == self._max_redirects
            ):
                raise A2APushAcknowledgementError()
            next_target = _parse_target(urljoin(_target_url(current), response.location))
            if next_target.origin != current.origin:
                send_authorization = None
                send_notification_token = None
            current = next_target
        raise A2APushAcknowledgementError()

    async def _send_once(
        self,
        target: _Target,
        body: bytes,
        send_gate: A2APushSendGate,
        authorization: str | None,
        notification_token: str | None,
    ) -> _Response:
        try:
            resolved = await asyncio.wait_for(
                _resolve_public_addresses(target.hostname, target.port),
                timeout=self._connect_timeout,
            )
        except A2APushSecurityError:
            raise
        except Exception as error:
            raise A2APushDeliveryError() from error
        addresses = _validated_public_addresses(resolved)
        for address in addresses:
            writer: _Writer | None = None
            try:
                reader, writer = await _open_tls_connection(
                    address=address,
                    port=target.port,
                    server_hostname=target.hostname,
                    ssl_context=self._ssl_context,
                    timeout=self._connect_timeout,
                )
                _verify_peer(writer, address, A2APushSecurityError)
                request = _request_bytes(
                    request_target=target.request_target,
                    host_header=target.host_header,
                    body=body,
                    authorization=authorization,
                    notification_token=notification_token,
                )
                await _write_through_gate(
                    writer=writer,
                    request=request,
                    send_gate=send_gate,
                    timeout=self._send_gate_timeout,
                )
                return await _read_response(
                    reader,
                    timeout=self._read_timeout,
                    max_body_bytes=self._max_response_bytes,
                    delivery_error=A2APushDeliveryError,
                    response_too_large_error=A2APushResponseTooLargeError,
                )
            except (A2APushSecurityError, A2APushResponseTooLargeError):
                raise
            except _SendGateError as failure:
                raise failure.error from None
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            finally:
                if writer is not None:
                    await _close_writer(writer, self._connect_timeout)
        raise A2APushDeliveryError()

    async def _register(self) -> asyncio.Task[object]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("delivery requires an asyncio task")
        async with self._lifecycle.lock:
            if self._lifecycle.closing:
                raise A2APushClientClosedError()
            self._lifecycle.active.add(task)
        return task

    async def _unregister(self, task: asyncio.Task[object]) -> None:
        async with self._lifecycle.lock:
            self._lifecycle.active.discard(task)
            if self._lifecycle.closing and not self._lifecycle.active:
                self._lifecycle.closed.set()


def _require_stream_response(value: object) -> bytes:
    if type(value) is not bytes or not value or len(value) > _MAX_STREAM_RESPONSE_BYTES:
        raise ValueError("StreamResponse must be non-empty bytes within 1 MiB")
    return value


def _authorization(scheme: object, credentials: object) -> str | None:
    if scheme is None:
        if credentials is not None:
            raise ValueError("authentication credentials require a scheme")
        return None
    if type(scheme) is not str or _AUTH_SCHEME.fullmatch(scheme) is None:
        raise ValueError("authentication scheme is invalid")
    if credentials is None:
        return None
    if not _is_visible_ascii(credentials, allow_space=True):
        raise ValueError("authentication credentials are invalid")
    value = f"{scheme} {credentials}"
    if len(value.encode("ascii")) > _MAX_AUTHORIZATION_BYTES:
        raise ValueError("authentication header exceeds maximum size")
    return value


def _notification_token(value: object) -> str | None:
    if value is None:
        return None
    if not _is_visible_ascii(value, allow_space=False):
        raise ValueError("notification token is invalid")
    if len(value.encode("ascii")) > _MAX_NOTIFICATION_TOKEN_BYTES:
        raise ValueError("notification token exceeds maximum size")
    return cast(str, value)


def _is_visible_ascii(value: object, *, allow_space: bool) -> bool:
    minimum = 0x20 if allow_space else 0x21
    return (
        type(value) is str
        and bool(value)
        and all(minimum <= ord(character) <= 0x7E for character in value)
    )


def _require_send_gate(value: object) -> A2APushSendGate:
    if value is None or not callable(getattr(value, "authorize_send", None)):
        raise TypeError("send_gate must provide authorize_send()")
    return cast(A2APushSendGate, value)


def _parse_target(url: object) -> _Target:
    if type(url) is not str or not url:
        raise A2APushSecurityError()
    try:
        encoded_url = url.encode("utf-8")
    except UnicodeEncodeError as error:
        raise A2APushSecurityError() from error
    if len(encoded_url) > _MAX_URL_BYTES:
        raise A2APushSecurityError()
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        raise A2APushSecurityError()
    try:
        parsed = urlsplit(url)
        port = 443 if parsed.port is None else parsed.port
        raw_hostname = parsed.hostname
    except (UnicodeError, ValueError) as error:
        raise A2APushSecurityError() from error
    if (
        parsed.scheme.lower() != "https"
        or raw_hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise A2APushSecurityError()
    try:
        hostname = raw_hostname.rstrip(".").encode("idna").decode("ascii").lower()
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    except UnicodeError as error:
        raise A2APushSecurityError() from error
    else:
        raise A2APushSecurityError()
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        raise A2APushSecurityError()
    path = quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%")
    query = quote(parsed.query, safe="/?:@!$&'()*+,;=-._~%")
    request_target = path if not query else f"{path}?{query}"
    host_header = hostname if port == 443 else f"{hostname}:{port}"
    return _Target(hostname, port, request_target, host_header)


def _target_url(target: _Target) -> str:
    return f"https://{target.host_header}{target.request_target}"


def _validated_public_addresses(values: object) -> tuple[str, ...]:
    if type(values) is not tuple or not values:
        raise A2APushSecurityError()
    normalized: list[str] = []
    for value in values:
        try:
            address = ipaddress.ip_address(value)
        except (TypeError, ValueError) as error:
            raise A2APushSecurityError() from error
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise A2APushSecurityError()
        text = str(address)
        if text not in normalized:
            normalized.append(text)
    return tuple(normalized)


__all__ = [
    "A2APushAcknowledgementError",
    "A2APushClientClosedError",
    "A2APushClientError",
    "A2APushDeliveryError",
    "A2APushHttpClient",
    "A2APushResponseTooLargeError",
    "A2APushSendGate",
    "A2APushSecurityError",
]
