from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest

from agentos.adapters.a2a import A2APushSendGate
from agentos.adapters.a2a import client as client_module
from agentos.adapters.a2a.client import A2APushDeliveryError, A2APushHttpClient
from tests.planning._async import async_test


PUBLIC_IP = "93.184.216.34"
SECOND_PUBLIC_IP = "93.184.216.35"
STREAM_RESPONSE = b'{"statusUpdate":{"taskId":"run_1"}}'


class RecordingGate:
    def __init__(
        self,
        timeline: list[str] | None = None,
        *,
        enter_error: BaseException | None = None,
        exit_error: BaseException | None = None,
    ) -> None:
        self.timeline = timeline if timeline is not None else []
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.enter_count = 0
        self.exit_count = 0
        self.active = False
        self.entered = asyncio.Event()
        self.released = asyncio.Event()

    @asynccontextmanager
    async def authorize_send(self) -> AsyncIterator[None]:
        self.enter_count += 1
        self.timeline.append("gate_enter")
        if self.enter_error is not None:
            raise self.enter_error
        self.active = True
        self.entered.set()
        try:
            yield
        finally:
            self.active = False
            self.exit_count += 1
            self.timeline.append("gate_exit")
            self.released.set()
            if self.exit_error is not None:
                raise self.exit_error


class RecordingWriter:
    def __init__(
        self,
        gate: RecordingGate,
        timeline: list[str],
        *,
        peer_ip: str = PUBLIC_IP,
        block_drain: bool = False,
        drain_error: Exception | None = None,
    ) -> None:
        self.gate = gate
        self.timeline = timeline
        self.peer_ip = peer_ip
        self.block_drain = block_drain
        self.drain_error = drain_error
        self.drain_started = asyncio.Event()
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        assert self.gate.active is True
        self.timeline.append("write")
        self.written.extend(data)

    async def drain(self) -> None:
        assert self.gate.active is True
        self.timeline.append("drain")
        self.drain_started.set()
        if self.drain_error is not None:
            raise self.drain_error
        if self.block_drain:
            await asyncio.Event().wait()

    def get_extra_info(self, name: str) -> object:
        if name == "peername":
            self.timeline.append("peer")
            return (self.peer_ip, 443)
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class RecordingReader:
    def __init__(
        self,
        gate: RecordingGate,
        timeline: list[str],
        *,
        status: int = 204,
        location: str | None = None,
    ) -> None:
        self.gate = gate
        self.timeline = timeline
        self.lines = [f"HTTP/1.1 {status} Test\r\n".encode("ascii")]
        if location is not None:
            self.lines.append(f"Location: {location}\r\n".encode("ascii"))
        self.lines.append(b"\r\n")

    async def readline(self) -> bytes:
        assert self.gate.active is False
        self.timeline.append("response_read")
        return self.lines.pop(0)


def _install_network(
    monkeypatch: pytest.MonkeyPatch,
    *,
    addresses: tuple[str, ...],
    connect: Callable[..., Awaitable[tuple[object, RecordingWriter]]],
) -> None:
    async def resolve(_: str, __: int) -> tuple[str, ...]:
        return addresses

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(client_module, "_open_tls_connection", connect)


@async_test
async def test_send_gate_wraps_only_write_and_bounded_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline: list[str] = []
    gate = RecordingGate(timeline)
    writer = RecordingWriter(gate, timeline)
    reader = RecordingReader(gate, timeline)

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        timeline.append("connect")
        return reader, writer

    _install_network(monkeypatch, addresses=(PUBLIC_IP,), connect=connect)

    await A2APushHttpClient().deliver(
        url="https://push.example.test/hook",
        stream_response=STREAM_RESPONSE,
        send_gate=gate,
    )

    assert timeline == [
        "connect",
        "peer",
        "gate_enter",
        "write",
        "drain",
        "gate_exit",
        "response_read",
        "response_read",
    ]
    assert gate.enter_count == gate.exit_count == 1


@async_test
async def test_send_gate_is_required_before_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = False

    async def resolve(_: str, __: int) -> tuple[str, ...]:
        nonlocal resolved
        resolved = True
        return (PUBLIC_IP,)

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)

    with pytest.raises(TypeError, match="send_gate"):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
        )

    assert resolved is False


@async_test
async def test_peer_validation_happens_before_send_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = RecordingGate()
    writer = RecordingWriter(
        gate,
        gate.timeline,
        peer_ip=SECOND_PUBLIC_IP,
    )

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        return RecordingReader(gate, gate.timeline), writer

    _install_network(monkeypatch, addresses=(PUBLIC_IP,), connect=connect)

    with pytest.raises(client_module.A2APushSecurityError):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=gate,
        )

    assert gate.enter_count == 0
    assert writer.written == b""
    assert writer.closed is True


@async_test
async def test_redirect_reauthorizes_each_http_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = RecordingGate()
    writers = [
        RecordingWriter(gate, gate.timeline),
        RecordingWriter(gate, gate.timeline, peer_ip=SECOND_PUBLIC_IP),
    ]
    readers = [
        RecordingReader(
            gate,
            gate.timeline,
            status=307,
            location="https://other.example/hook",
        ),
        RecordingReader(gate, gate.timeline),
    ]
    connect_count = 0

    async def resolve(hostname: str, _: int) -> tuple[str, ...]:
        return (PUBLIC_IP,) if hostname == "push.example.test" else (SECOND_PUBLIC_IP,)

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        nonlocal connect_count
        index = connect_count
        connect_count += 1
        return readers[index], writers[index]

    monkeypatch.setattr(client_module, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(client_module, "_open_tls_connection", connect)

    await A2APushHttpClient(follow_redirects=True).deliver(
        url="https://push.example.test/hook",
        stream_response=STREAM_RESPONSE,
        send_gate=gate,
    )

    assert connect_count == 2
    assert gate.enter_count == gate.exit_count == 2


@pytest.mark.parametrize("phase", ("enter", "exit"))
@async_test
async def test_send_gate_errors_propagate_unchanged_without_address_retry(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    error = RuntimeError(f"gate-{phase}")
    gate = RecordingGate(
        enter_error=error if phase == "enter" else None,
        exit_error=error if phase == "exit" else None,
    )
    writer = RecordingWriter(gate, gate.timeline)
    connect_count = 0

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        nonlocal connect_count
        connect_count += 1
        return RecordingReader(gate, gate.timeline), writer

    _install_network(
        monkeypatch,
        addresses=(PUBLIC_IP, SECOND_PUBLIC_IP),
        connect=connect,
    )

    with pytest.raises(RuntimeError) as raised:
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=gate,
        )

    assert raised.value is error
    assert connect_count == 1
    assert writer.closed is True
    if phase == "enter":
        assert writer.written == b""


@async_test
async def test_send_gate_drain_timeout_releases_gate_and_closes_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = RecordingGate()
    writer = RecordingWriter(gate, gate.timeline, block_drain=True)

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        return RecordingReader(gate, gate.timeline), writer

    _install_network(monkeypatch, addresses=(PUBLIC_IP,), connect=connect)

    with pytest.raises(A2APushDeliveryError):
        await A2APushHttpClient(send_gate_timeout=0.01).deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=gate,
        )

    assert gate.released.is_set()
    assert gate.active is False
    assert writer.closed is True


@async_test
async def test_drain_error_releases_gate_and_closes_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = RecordingGate()
    writer = RecordingWriter(
        gate,
        gate.timeline,
        drain_error=OSError("drain failed"),
    )

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        return RecordingReader(gate, gate.timeline), writer

    _install_network(monkeypatch, addresses=(PUBLIC_IP,), connect=connect)

    with pytest.raises(A2APushDeliveryError):
        await A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=gate,
        )

    assert gate.released.is_set()
    assert gate.active is False
    assert writer.closed is True


@async_test
async def test_cancellation_during_drain_releases_gate_and_closes_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = RecordingGate()
    writer = RecordingWriter(gate, gate.timeline, block_drain=True)

    async def connect(**_: object) -> tuple[RecordingReader, RecordingWriter]:
        return RecordingReader(gate, gate.timeline), writer

    _install_network(monkeypatch, addresses=(PUBLIC_IP,), connect=connect)
    delivery = asyncio.create_task(
        A2APushHttpClient().deliver(
            url="https://push.example.test/hook",
            stream_response=STREAM_RESPONSE,
            send_gate=gate,
        ),
    )
    await writer.drain_started.wait()

    delivery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await delivery

    assert gate.released.is_set()
    assert gate.active is False
    assert writer.closed is True


@pytest.mark.parametrize("timeout", (0, -1, 5.01, float("inf"), float("nan")))
def test_send_gate_timeout_is_positive_and_cannot_exceed_hard_max(
    timeout: float,
) -> None:
    with pytest.raises(ValueError, match="send_gate_timeout"):
        A2APushHttpClient(send_gate_timeout=timeout)


def test_send_gate_protocol_is_structural() -> None:
    gate: A2APushSendGate = RecordingGate()

    assert callable(gate.authorize_send)
