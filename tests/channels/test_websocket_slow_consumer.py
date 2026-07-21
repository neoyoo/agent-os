from __future__ import annotations

import asyncio
import json

import pytest

from agentos.channels.websocket_buffer import WebSocketBufferLimits
from agentos.channels.websocket_session import WebSocketSession
from agentos.distributed.models import LiveContentDelta, ReplayItem, StreamGap
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.websocket import SubscribeRunFrame
from tests.channels._websocket_session_fakes import (
    SCOPE,
    Authenticator,
    Connection,
    RunPort,
    Subscription,
    multi_services,
    replay_item,
    services,
)
from tests.planning._async import async_test


class BlockingEventConnection(Connection):
    def __init__(self) -> None:
        super().__init__()
        self.event_send_started = asyncio.Event()

    async def send_text(self, text: str) -> None:
        if json.loads(text)["type"] == "event":
            self.event_send_started.set()
            await asyncio.Event().wait()
        await super().send_text(text)

    async def close(self, code: int, reason: str = "") -> None:
        await super().close(code, reason)
        async with self.changed:
            self.changed.notify_all()


class FirstEventThenBlockingConnection(Connection):
    def __init__(self) -> None:
        super().__init__()
        self.first_event_sent = asyncio.Event()
        self.second_event_started = asyncio.Event()
        self._event_count = 0

    async def send_text(self, text: str) -> None:
        if json.loads(text)["type"] == "event":
            self._event_count += 1
            if self._event_count == 1:
                await super().send_text(text)
                self.first_event_sent.set()
                return
            self.second_event_started.set()
            await asyncio.Event().wait()
        await super().send_text(text)


class FailingSlowConsumerErrorConnection(Connection):
    error_send_attempted: int = 0

    async def send_text(self, text: str) -> None:
        if json.loads(text)["type"] == "error":
            self.error_send_attempted += 1
            raise RuntimeError("send failed")
        await super().send_text(text)


class GatedSubscription(Subscription):
    def __init__(
        self,
        items: list[ReplayItem | StreamGap],
        gate_after_first: asyncio.Event,
    ) -> None:
        super().__init__(items)
        self._gate_after_first = gate_after_first
        self._yielded = 0

    async def __anext__(self) -> ReplayItem | StreamGap:
        if self._yielded == 1:
            await self._gate_after_first.wait()
        item = await super().__anext__()
        self._yielded += 1
        return item


class BlockingRunPort(RunPort):
    def __init__(self) -> None:
        super().__init__()
        self.submit_started = asyncio.Event()
        self.submit_cancelled = asyncio.Event()
        self.allow_submit = asyncio.Event()

    async def submit(self, **values: object):  # type: ignore[no-untyped-def]
        self.submit_started.set()
        try:
            await self.allow_submit.wait()
        except asyncio.CancelledError:
            self.submit_cancelled.set()
            raise
        return await super().submit(**values)  # type: ignore[arg-type]


def _limits() -> WebSocketBufferLimits:
    return WebSocketBufferLimits(
        connection_frames=1,
        connection_bytes=1024 * 1024,
        subscription_frames=1,
        subscription_bytes=1024 * 1024,
    )


@async_test
async def test_slow_consumer_interrupts_blocked_submit_and_releases_subscription() -> None:
    connection = Connection()
    subscription = Subscription([])
    run = BlockingRunPort()
    session = WebSocketSession(
        services=services(run, subscription),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    await session._subscribe(  # noqa: SLF001 - deterministic fatal race setup
        SubscribeRunFrame("req_sub", "session_1", "run_1", None),
    )
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"submit_run","request_id":"req_submit",'
        '"session_id":"session_1","content":"hello","artifact_handles":[]}',
    )
    await run.submit_started.wait()

    session._signal_fatal("slow")  # noqa: SLF001 - subscription overflow signal
    try:
        await asyncio.wait_for(connection.wait_closed(), timeout=0.2)
    finally:
        run.allow_submit.set()
        await task

    assert connection.closed == [4408]
    assert subscription.close_count == 1
    assert run.submit_cancelled.is_set()
    assert run.submissions == []


@async_test
async def test_session_cancel_interrupts_blocked_submit_and_releases_subscription() -> None:
    connection = Connection()
    subscription = Subscription([])
    run = BlockingRunPort()
    session = WebSocketSession(
        services=services(run, subscription),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    await session._subscribe(  # noqa: SLF001 - deterministic cancellation setup
        SubscribeRunFrame("req_sub", "session_1", "run_1", None),
    )
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"submit_run","request_id":"req_submit",'
        '"session_id":"session_1","content":"hello","artifact_handles":[]}',
    )
    await run.submit_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert run.submit_cancelled.is_set()
    assert run.submissions == []
    assert subscription.close_count == 1
    assert connection.closed == [1001]


@async_test
async def test_slow_consumer_excludes_dequeued_and_queued_cursors() -> None:
    connection = BlockingEventConnection()
    subscription = GatedSubscription(
        [
            replay_item(
                LiveContentDelta(index, str(index)),
                index + 1,
                f"{index + 1}-0",
            )
            for index in range(3)
        ],
        connection.event_send_started,
    )
    session = WebSocketSession(
        services=services(RunPort(), subscription),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,
        buffer_limits=_limits(),
    )
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"subscribe_run","request_id":"req_1","session_id":"session_1",'
        '"run_id":"run_1","cursor":null}',
    )

    await connection.wait_closed()
    await task

    assert connection.event_send_started.is_set()
    assert connection.closed == [4408]
    assert [json.loads(text)["type"] for text in connection.sent] == [
        "receipt",
        "error",
    ]
    error = json.loads(connection.sent[-1])
    assert error["resume_cursors"] == [
        {"session_id": "session_1", "run_id": "run_1", "cursor": None},
    ]
    assert subscription.close_count == 1


@async_test
async def test_slow_consumer_reports_last_successful_cursor_only() -> None:
    connection = FirstEventThenBlockingConnection()
    subscription = GatedSubscription(
        [
            replay_item(
                LiveContentDelta(index, str(index)),
                index + 1,
                f"{index + 1}-0",
            )
            for index in range(4)
        ],
        connection.first_event_sent,
    )
    session = WebSocketSession(
        services=services(RunPort(), subscription),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,
        buffer_limits=_limits(),
    )
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"subscribe_run","request_id":"req_1","session_id":"session_1",'
        '"run_id":"run_1","cursor":null}',
    )

    await connection.wait_closed()
    await task

    frames = [json.loads(text) for text in connection.sent]
    assert connection.second_event_started.is_set()
    assert frames[-1]["resume_cursors"] == [
        {
            "session_id": "session_1",
            "run_id": "run_1",
            "cursor": frames[1]["cursor"],
        },
    ]
    assert subscription.close_count == 1


@async_test
async def test_slow_consumer_orders_multiple_subscription_cursors_lexically() -> None:
    connection = Connection()
    subscriptions = {
        "run_z": Subscription([]),
        "run_a": Subscription([]),
        "run_b": Subscription([]),
    }
    session = WebSocketSession(
        services=multi_services(RunPort(), subscriptions),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    for request_id, run_id in (
        ("req_z", "run_z"),
        ("req_a", "run_a"),
        ("req_b", "run_b"),
    ):
        await session._subscribe(  # noqa: SLF001 - deterministic fatal-close setup
            SubscribeRunFrame(request_id, "session_1", run_id, None),
        )
    states = session._subscriptions  # noqa: SLF001
    states[("session_1", "run_z")].last_sent_cursor = "3-0"
    states[("session_1", "run_a")].last_sent_cursor = None
    states[("session_1", "run_b")].last_sent_cursor = "2-0"

    await session._close_fatal("slow")  # noqa: SLF001

    error = json.loads(connection.sent[-1])
    assert error["resume_cursors"] == [
        {"session_id": "session_1", "run_id": "run_a", "cursor": None},
        {"session_id": "session_1", "run_id": "run_b", "cursor": "2-0"},
        {"session_id": "session_1", "run_id": "run_z", "cursor": "3-0"},
    ]
    assert [item.close_count for item in subscriptions.values()] == [1, 1, 1]
    assert connection.closed == [4408]


@async_test
async def test_slow_consumer_error_send_failure_still_releases_once() -> None:
    connection = FailingSlowConsumerErrorConnection()
    subscriptions = {"run_1": Subscription([]), "run_2": Subscription([])}
    session = WebSocketSession(
        services=multi_services(RunPort(), subscriptions),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    for request_id, run_id in (("req_1", "run_1"), ("req_2", "run_2")):
        await session._subscribe(  # noqa: SLF001 - deterministic fatal-close setup
            SubscribeRunFrame(request_id, "session_1", run_id, None),
        )

    await session._close_fatal("slow")  # noqa: SLF001
    await session.aclose()

    assert connection.error_send_attempted == 1
    assert connection.closed == [4408]
    assert [item.close_count for item in subscriptions.values()] == [1, 1]
