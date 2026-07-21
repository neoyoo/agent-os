from __future__ import annotations

import asyncio
import json

import pytest

from agentos.channels.websocket_buffer import WebSocketBufferLimits
from agentos.channels.websocket_session import WebSocketSession
from agentos.distributed.models import (
    LiveContentDelta,
    LiveTurnCompleted,
    RequestScope,
    RunSubmission,
    StreamGap,
)
from agentos.transports.http.request_types import HttpHeaders
from agentos.channels.asgi_websocket import WebSocketDisconnected
from agentos.transports.run_stream import encode_cursor
from agentos.transports.websocket import SubscribeRunFrame
from tests.channels._websocket_session_fakes import (
    SCOPE,
    Authenticator,
    Connection,
    RunPort,
    Subscription,
    multi_services,
    replay_item,
    session as make_session,
)
from tests.planning._async import async_test


class GatedCloseSubscription(Subscription):
    def __init__(self) -> None:
        super().__init__([])
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.close_completed = False

    async def aclose(self) -> None:
        self.close_count += 1
        self.close_started.set()
        await self.allow_close.wait()
        self.close_completed = True


@async_test
async def test_submit_and_command_use_request_id_and_resource_auth() -> None:
    connection = Connection()
    run = RunPort()
    auth = Authenticator()
    target = make_session(connection, run, Subscription([]), auth)
    task = asyncio.create_task(target.run())
    await connection.incoming.put(
        '{"type":"submit_run","request_id":"req_1","session_id":"session_1",'
        '"content":"hello","artifact_handles":[]}',
    )
    await connection.incoming.put(
        '{"type":"submit_command","request_id":"req_2","session_id":"session_1",'
        '"run_id":"run_1","kind":"cancel","payload":{}}',
    )
    await connection.wait_sent(2)
    await connection.incoming.put(WebSocketDisconnected())
    await task

    assert run.submissions == [RunSubmission("session_1", "req_1", "hello")]
    assert run.commands[0][1].command_id == "req_2"
    assert [call.operation for call in auth.calls] == ["submit_run", "submit_command"]
    assert [json.loads(text)["operation"] for text in connection.sent] == [
        "submit_run",
        "submit_command",
    ]


@async_test
async def test_subscribe_receipt_precedes_events_and_terminal_closes_once() -> None:
    connection = Connection()
    subscription = Subscription(
        [
            replay_item(LiveContentDelta(0, "hello"), 1, "1-0"),
            replay_item(LiveTurnCompleted(), 2, "2-0"),
        ],
    )
    session = make_session(connection, RunPort(), subscription)
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"subscribe_run","request_id":"req_1","session_id":"session_1",'
        '"run_id":"run_1","cursor":null}',
    )

    await connection.wait_sent(3)
    await subscription.close_finished.wait()
    frames = [json.loads(text) for text in connection.sent]
    assert [frame["type"] for frame in frames] == ["receipt", "event", "event"]
    assert frames[1]["event_kind"] == "content_delta"
    assert frames[2]["event_kind"] == "turn_completed"
    assert subscription.close_count == 1
    await connection.incoming.put(WebSocketDisconnected())
    await task
    assert subscription.close_count == 1


@async_test
async def test_unsubscribe_and_disconnect_release_without_cancel_command() -> None:
    connection = Connection()
    subscription = Subscription([])
    run = RunPort()
    session = make_session(connection, run, subscription)
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"subscribe_run","request_id":"req_1","session_id":"session_1",'
        '"run_id":"run_1","cursor":null}',
    )
    await connection.wait_sent(1)
    await connection.incoming.put(
        '{"type":"unsubscribe_run","request_id":"req_2","session_id":"session_1",'
        '"run_id":"run_1"}',
    )
    await connection.wait_sent(2)
    await connection.incoming.put(WebSocketDisconnected())
    await task

    assert subscription.close_count == 1
    assert run.commands == []
    assert json.loads(connection.sent[1])["operation"] == "unsubscribe_run"


@async_test
async def test_multi_run_duplicate_subscribe_and_idempotent_unsubscribe() -> None:
    connection = Connection()
    subscriptions = {"run_1": Subscription([]), "run_2": Subscription([])}
    session = WebSocketSession(
        services=multi_services(RunPort(), subscriptions),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    task = asyncio.create_task(session.run())
    for request_id, run_id in (("req_1", "run_1"), ("req_2", "run_2")):
        await connection.incoming.put(
            '{"type":"subscribe_run","request_id":"'
            + request_id
            + '","session_id":"session_1","run_id":"'
            + run_id
            + '","cursor":null}',
        )
    await connection.wait_sent(2)
    await connection.incoming.put(
        '{"type":"subscribe_run","request_id":"req_3","session_id":"session_1",'
        '"run_id":"run_1","cursor":null}',
    )
    await connection.incoming.put(
        '{"type":"unsubscribe_run","request_id":"req_4","session_id":"session_1",'
        '"run_id":"run_missing"}',
    )
    await connection.wait_sent(4)
    await connection.incoming.put(WebSocketDisconnected())
    await task

    frames = [json.loads(text) for text in connection.sent]
    assert [frame["type"] for frame in frames] == [
        "receipt",
        "receipt",
        "error",
        "receipt",
    ]
    assert frames[2]["code"] == "subscription_exists"
    assert frames[3]["operation"] == "unsubscribe_run"
    assert all(subscription.close_count == 1 for subscription in subscriptions.values())


@async_test
async def test_gap_closes_only_its_subscription_and_connection_continues() -> None:
    connection = Connection()
    subscriptions = {
        "run_1": Subscription(
            [StreamGap("tenant_1", "session_1", "run_1", "1-0", "2-0", "trimmed")],
        ),
        "run_2": Subscription([]),
    }
    session = WebSocketSession(
        services=multi_services(RunPort(), subscriptions),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    task = asyncio.create_task(session.run())
    cursor = encode_cursor(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        position="1-0",
    )
    for request_id, run_id, requested_cursor in (
        ("req_1", "run_1", cursor),
        ("req_2", "run_2", None),
    ):
        await connection.incoming.put(
            json.dumps(
                {
                    "type": "subscribe_run",
                    "request_id": request_id,
                    "session_id": "session_1",
                    "run_id": run_id,
                    "cursor": requested_cursor,
                },
            ),
        )
    await connection.wait_sent(3)
    await connection.incoming.put(
        '{"type":"unsubscribe_run","request_id":"req_3","session_id":"session_1",'
        '"run_id":"run_2"}',
    )
    await connection.wait_sent(4)
    await connection.incoming.put(WebSocketDisconnected())
    await task

    frames = [json.loads(text) for text in connection.sent]
    gap = next(frame for frame in frames if frame["type"] == "stream_gap")
    assert gap == {
        "type": "stream_gap",
        "session_id": "session_1",
        "run_id": "run_1",
        "reason": "trimmed",
    }
    assert frames[-1]["operation"] == "unsubscribe_run"
    assert all(subscription.close_count == 1 for subscription in subscriptions.values())


@async_test
async def test_cleanup_failure_does_not_skip_other_subscriptions_or_close() -> None:
    connection = Connection()
    subscriptions = {
        "run_1": Subscription([], close_error=RuntimeError("close failed")),
        "run_2": Subscription([]),
    }
    session = WebSocketSession(
        services=multi_services(RunPort(), subscriptions),
        authenticator=Authenticator(),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,  # type: ignore[arg-type]
        buffer_limits=WebSocketBufferLimits(),
    )
    for request_id, run_id in (("req_1", "run_1"), ("req_2", "run_2")):
        await session._subscribe(  # noqa: SLF001 - exercise deterministic teardown race
            SubscribeRunFrame(request_id, "session_1", run_id, None),
        )

    await session._close_fatal("internal")  # noqa: SLF001

    assert [subscription.close_count for subscription in subscriptions.values()] == [1, 1]
    assert connection.closed == [1011]


@async_test
async def test_session_close_completes_inflight_subscription_close_once() -> None:
    connection = Connection()
    subscription = GatedCloseSubscription()
    session = make_session(connection, RunPort(), subscription)
    frame = SubscribeRunFrame("req_1", "session_1", "run_1", None)
    await session._subscribe(frame)  # noqa: SLF001 - deterministic teardown race
    key = (frame.session_id, frame.run_id)
    state = session._subscriptions[key]  # noqa: SLF001
    sender_cancelled = asyncio.Event()

    async def close_from_sender() -> None:
        try:
            await session._close_subscription(key, state, drop=False)  # noqa: SLF001
        except asyncio.CancelledError:
            sender_cancelled.set()
            raise

    session._sender = asyncio.create_task(close_from_sender())  # noqa: SLF001
    await subscription.close_started.wait()
    session_close = asyncio.create_task(session.aclose())
    await sender_cancelled.wait()
    assert session._subscriptions[key] is state  # noqa: SLF001
    assert state.pump_stop_task is not None and not state.pump_stop_task.done()
    subscription.allow_close.set()

    await session_close
    await session.aclose()

    assert subscription.close_completed
    assert subscription.close_count == 1


@async_test
async def test_unsubscribe_cleanup_failure_still_drops_buffered_events() -> None:
    connection = Connection()
    subscription = Subscription([], close_error=RuntimeError("close failed"))
    session = make_session(connection, RunPort(), subscription)
    frame = SubscribeRunFrame("req_1", "session_1", "run_1", None)
    await session._subscribe(frame)  # noqa: SLF001 - exercise deterministic teardown race
    key = (frame.session_id, frame.run_id)
    await session._buffer.put_event(key, "event", cursor=None)  # noqa: SLF001
    state = session._subscriptions[key]  # noqa: SLF001

    with pytest.raises(RuntimeError, match="close failed"):
        await session._close_subscription(key, state, drop=True)  # noqa: SLF001

    assert session._buffer.queued_frames == 1  # noqa: SLF001
    await session.aclose()


@async_test
async def test_scope_drift_fails_closed_before_service_call() -> None:
    connection = Connection()
    run = RunPort()
    auth = Authenticator(scope=RequestScope("other", "principal_1"))
    session = make_session(connection, run, Subscription([]), auth)
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"submit_run","request_id":"req_1","session_id":"session_1",'
        '"content":"hello","artifact_handles":[]}',
    )
    await connection.wait_sent(1)
    await connection.incoming.put(WebSocketDisconnected())
    await task

    assert run.submissions == []
    assert json.loads(connection.sent[0]) == {
        "code": "permission_denied",
        "message": "permission denied",
        "request_id": "req_1",
        "type": "error",
    }


@async_test
async def test_unknown_service_failure_closes_internal_connection() -> None:
    connection = Connection()
    run = RunPort(submit_error=RuntimeError("secret backend detail"))
    session = make_session(connection, run, Subscription([]))
    task = asyncio.create_task(session.run())
    await connection.incoming.put(
        '{"type":"submit_run","request_id":"req_1","session_id":"session_1",'
        '"content":"hello","artifact_handles":[]}',
    )

    await task

    assert connection.sent == []
    assert connection.closed == [1011]
