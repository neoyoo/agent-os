from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import ssl

import pytest

from agentos.adapters.a2a import client as client_module
from agentos.adapters.a2a.client import (
    A2APushAcknowledgementError,
    A2APushClientClosedError,
    A2APushDeliveryError,
    A2APushHttpClient,
    A2APushResponseTooLargeError,
    A2APushSecurityError,
)
from tests.planning._async import async_test


PUBLIC_IP = "93.184.216.34"
STREAM_RESPONSE = b'{"statusUpdate":{"taskId":"run_1"}}'


class NoopSendGate:
    @asynccontextmanager
    async def authorize_send(self) -> AsyncIterator[None]:
        yield


SEND_GATE = NoopSendGate()


class FakeWriter:
    def __init__(self, peer_ip: str = PUBLIC_IP) -> None:
        self.peer_ip = peer_ip
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str) -> object:
        if name == "peername":
            return (self.peer_ip, 443)
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class BlockingReader:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def readline(self) -> bytes:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class SlowHeaderReader:
    def __init__(self) -> None:
        self.lines = [b"HTTP/1.1 204 Test\r\n", b"X-Test: value\r\n", b"\r\n"]

    async def readline(self) -> bytes:
        await asyncio.sleep(0.03)
        return self.lines.pop(0)


def _response(
    status: int = 204,
    *,
    headers: tuple[tuple[str, str], ...] = (),
    body: bytes = b"",
) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    wire_headers = list(headers)
    if body and not any(name.lower() == "content-length" for name, _ in headers):
        wire_headers.append(("Content-Length", str(len(body))))
    payload = [f"HTTP/1.1 {status} Test\r\n".encode("ascii")]
    payload.extend(f"{name}: {value}\r\n".encode("ascii") for name, value in wire_headers)
    payload.append(b"\r\n")
    payload.append(body)
    reader.feed_data(b"".join(payload))
    reader.feed_eof()
    return reader


def _install_network(
    monkeypatch: pytest.MonkeyPatch,
    *,
    addresses: dict[str, tuple[str, ...]],
    connect: Callable[..., Awaitable[tuple[object, FakeWriter]]],
) -> list[tuple[str, int]]:
    resolutions: list[tuple[str, int]] = []

    async def resolve(hostname: str, port: int) -> tuple[str, ...]:
        resolutions.append((hostname, port))
        return addresses[hostname]

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(client_module, "_open_tls_connection", connect)
    return resolutions


@async_test
async def test_delivery_pins_ip_and_preserves_tls_host_and_protojson(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()
    connections: list[tuple[str, int, str, ssl.SSLContext, float]] = []

    async def connect(**kwargs: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        connections.append(
            (
                str(kwargs["address"]),
                int(kwargs["port"]),
                str(kwargs["server_hostname"]),
                kwargs["ssl_context"],  # type: ignore[arg-type]
                float(kwargs["timeout"]),
            ),
        )
        return _response(), writer

    resolutions = _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )
    client = A2APushHttpClient()

    await client.deliver(
        url="https://push.example.test/hooks/task?q=1",
        stream_response=STREAM_RESPONSE,
        send_gate=SEND_GATE,
        authentication_scheme="Bearer",
        credentials="credential-1",
        token="notification-token-1",
    )

    assert resolutions == [("push.example.test", 443)]
    assert connections[0][:3] == (PUBLIC_IP, 443, "push.example.test")
    request = bytes(writer.written)
    assert request.startswith(b"POST /hooks/task?q=1 HTTP/1.1\r\n")
    assert b"Host: push.example.test\r\n" in request
    assert b"Content-Type: application/a2a+json\r\n" in request
    assert b"Authorization: Bearer credential-1\r\n" in request
    assert b"X-A2A-Notification-Token: notification-token-1\r\n" in request
    assert request.endswith(b"\r\n\r\n" + STREAM_RESPONSE)
    assert b'"jsonrpc"' not in request


@pytest.mark.parametrize(
    "address",
    (
        "0.0.0.0",
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff00::1",
    ),
)
@async_test
async def test_delivery_rejects_any_non_public_dns_answer(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    connected = False

    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        nonlocal connected
        connected = True
        return _response(), FakeWriter()

    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP, address)},
        connect=connect,
    )

    with pytest.raises(A2APushSecurityError):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
    assert connected is False


@async_test
async def test_dns_resolution_timeout_fails_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = False

    async def resolve(_: str, __: int) -> tuple[str, ...]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        nonlocal connected
        connected = True
        return _response(), FakeWriter()

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(client_module, "_open_tls_connection", connect)

    with pytest.raises(A2APushDeliveryError):
        await A2APushHttpClient(connect_timeout=0.01).deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
    assert connected is False


@async_test
async def test_delivery_rejects_peer_ip_mismatch_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter("93.184.216.35")

    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        return _response(), writer

    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )

    with pytest.raises(A2APushSecurityError):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
    assert writer.closed is True
    assert writer.written == b""


@async_test
async def test_only_2xx_acknowledges_and_errors_redact_url_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()

    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        return _response(500, body=b"do not expose this response"), writer

    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )
    secret_url = "https://push.example.test/hook?secret=url-secret"

    with pytest.raises(A2APushAcknowledgementError) as raised:
        await A2APushHttpClient().deliver(
            url=secret_url,
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
            authentication_scheme="Bearer",
            credentials="credential-secret",
        )

    rendered = f"{raised.value!s} {raised.value!r}"
    assert "url-secret" not in rendered
    assert "credential-secret" not in rendered
    assert "do not expose" not in rendered


@async_test
async def test_redirects_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        return (
            _response(307, headers=(("Location", "https://other.example/hook"),)),
            FakeWriter(),
        )

    resolutions = _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )

    with pytest.raises(A2APushAcknowledgementError):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
    assert resolutions == [("push.example.test", 443)]


@async_test
async def test_redirect_revalidates_dns_and_drops_cross_origin_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writers = [FakeWriter(PUBLIC_IP), FakeWriter("93.184.216.35")]
    responses = [
        _response(307, headers=(("Location", "https://other.example/hook"),)),
        _response(204),
    ]
    connections: list[tuple[str, str]] = []

    async def connect(**kwargs: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        connections.append((str(kwargs["address"]), str(kwargs["server_hostname"])))
        return responses.pop(0), writers[len(connections) - 1]

    resolutions = _install_network(
        monkeypatch,
        addresses={
            "push.example.test": (PUBLIC_IP,),
            "other.example": ("93.184.216.35",),
        },
        connect=connect,
    )
    client = A2APushHttpClient(follow_redirects=True)

    await client.deliver(
        url="https://push.example.test/hook",
        stream_response=STREAM_RESPONSE,
        send_gate=SEND_GATE,
        authentication_scheme="Bearer",
        credentials="credential-secret",
        token="notification-secret",
    )

    assert resolutions == [
        ("push.example.test", 443),
        ("other.example", 443),
    ]
    assert connections == [
        (PUBLIC_IP, "push.example.test"),
        ("93.184.216.35", "other.example"),
    ]
    assert b"Authorization: Bearer credential-secret\r\n" in writers[0].written
    assert b"X-A2A-Notification-Token: notification-secret\r\n" in writers[0].written
    assert b"Authorization:" not in writers[1].written
    assert b"X-A2A-Notification-Token:" not in writers[1].written
    assert bytes(writers[1].written).endswith(b"\r\n\r\n" + STREAM_RESPONSE)


@async_test
async def test_response_body_limit_is_enforced_and_connection_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()

    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        return (
            _response(200, headers=(("Content-Length", "5"),), body=b"12345"),
            writer,
        )

    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )

    with pytest.raises(A2APushResponseTooLargeError):
        await A2APushHttpClient(max_response_bytes=4).deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
    assert writer.closed is True


@async_test
async def test_read_timeout_bounds_the_complete_response_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeWriter()

    async def connect(**_: object) -> tuple[SlowHeaderReader, FakeWriter]:
        return SlowHeaderReader(), writer

    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )

    with pytest.raises(A2APushDeliveryError):
        await A2APushHttpClient(read_timeout=0.05).deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
    assert writer.closed is True


@async_test
async def test_concurrency_limit_queues_before_dns_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_connected = asyncio.Event()
    release_first = asyncio.Event()
    connect_count = 0

    async def connect(**_: object) -> tuple[asyncio.StreamReader, FakeWriter]:
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            first_connected.set()
            await release_first.wait()
        return _response(), FakeWriter()

    resolutions = _install_network(
        monkeypatch,
        addresses={
            "first.example": (PUBLIC_IP,),
            "second.example": (PUBLIC_IP,),
        },
        connect=connect,
    )
    client = A2APushHttpClient(max_concurrency=1)
    first = asyncio.create_task(
        client.deliver(
            url="https://first.example/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        ),
    )
    await first_connected.wait()
    second = asyncio.create_task(
        client.deliver(
            url="https://second.example/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        ),
    )
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()

    assert resolutions == [("first.example", 443)]
    release_first.set()
    await asyncio.gather(first, second)
    assert resolutions == [("first.example", 443), ("second.example", 443)]


@async_test
async def test_close_cancels_active_delivery_and_rejects_new_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = BlockingReader()
    writer = FakeWriter()

    async def connect(**_: object) -> tuple[BlockingReader, FakeWriter]:
        return reader, writer

    _install_network(
        monkeypatch,
        addresses={"push.example.test": (PUBLIC_IP,)},
        connect=connect,
    )
    client = A2APushHttpClient()
    delivery = asyncio.create_task(
        client.deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        ),
    )
    await reader.started.wait()

    await client.close()

    with pytest.raises(asyncio.CancelledError):
        await delivery
    assert writer.closed is True
    with pytest.raises(A2APushClientClosedError):
        await client.deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=SEND_GATE,
        )
