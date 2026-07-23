from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from agentos.channels.asgi_websocket import WebSocketDisconnected
from agentos.channels.service_wiring import FixedScopeAuthenticator
from agentos.channels.sse_endpoint import RunSseEndpoint, RunSseResponse
from agentos.channels.websocket_buffer import WebSocketBufferLimits
from agentos.channels.websocket_session import WebSocketSession
from agentos.distributed.models import (
    ReplayItem,
)
from agentos.transports.http.request_types import HttpHeaders
from tests.integration._backend_restart_support import cleanup_redis
from tests.integration._run_stream_live_support import (
    Connection,
    SCOPE,
    TailObservedReplay,
    TrackedReplayPort,
    append_event,
    public_cursor,
    redis_url,
    services,
    trimmed_stream,
)


pytestmark = pytest.mark.integration


def test_sse_live_redis_trim_gap_and_reconnect() -> None:
    asyncio.run(_sse_scenario())


def test_websocket_live_redis_trim_gap_and_reconnect() -> None:
    asyncio.run(_websocket_scenario())


async def _sse_scenario() -> None:
    url = redis_url()
    prefix = f"agentos_stream_live_{uuid4().hex}"
    replay = TailObservedReplay(
        url,
        key_prefix=prefix,
        max_events=2,
        block_ms=50,
    )
    tracked = TrackedReplayPort(replay)
    endpoint = RunSseEndpoint(
        services(tracked),
        FixedScopeAuthenticator(SCOPE),
        heartbeat_interval=1,
    )
    try:
        first, second, third = await trimmed_stream(replay)
        gap_response = await endpoint.open(
            session_id="session_1",
            run_id="run_1",
            headers=_cursor_headers(first),
            request_id="request_gap",
        )
        assert type(gap_response) is RunSseResponse
        assert await gap_response.next_frame() == (
            b"event: stream_gap\ndata: {\"reason\":\"trimmed\"}\n\n"
        )
        assert tracked.subscriptions[0].close_count == 1

        resumed = await endpoint.open(
            session_id="session_1",
            run_id="run_1",
            headers=_cursor_headers(second),
            request_id="request_resume",
        )
        assert type(resumed) is RunSseResponse
        frame = (await resumed.next_frame()).decode("utf-8")
        assert frame.startswith(f"id: {public_cursor(third)}\nevent: content_delta\n")
        assert '"event":{"index":3,"text":"content-3"}' in frame
        assert '"event_sequence":3' in frame
        assert "content-1" not in frame and "content-2" not in frame

        tail = asyncio.create_task(resumed.next_frame())
        await asyncio.wait_for(replay.tail_waiting.wait(), timeout=2)
        fourth = await append_event(replay, 4)
        tail_frame = (await asyncio.wait_for(tail, timeout=2)).decode("utf-8")
        assert tail_frame.startswith(
            f"id: {public_cursor(fourth)}\nevent: content_delta\n",
        )
        assert '"event":{"index":4,"text":"content-4"}' in tail_frame
        assert '"event_sequence":4' in tail_frame
        await resumed.aclose()
        assert tracked.subscriptions[1].close_count == 1
    finally:
        try:
            await replay.close()
        finally:
            await cleanup_redis(url, prefix)


async def _websocket_scenario() -> None:
    url = redis_url()
    prefix = f"agentos_stream_live_{uuid4().hex}"
    replay = TailObservedReplay(
        url,
        key_prefix=prefix,
        max_events=2,
        block_ms=50,
    )
    tracked = TrackedReplayPort(replay)
    connection = Connection()
    session = WebSocketSession(
        services=services(tracked),
        authenticator=FixedScopeAuthenticator(SCOPE),
        handshake_scope=SCOPE,
        headers=HttpHeaders(()),
        connection=connection,
        buffer_limits=WebSocketBufferLimits(),
    )
    task = asyncio.create_task(session.run())
    try:
        first, second, third = await trimmed_stream(replay)
        await connection.incoming.put(_subscribe_frame("request_gap", first))
        await connection.wait_sent(2)
        await asyncio.wait_for(tracked.subscriptions[0].closed.wait(), timeout=2)

        frames = [json.loads(text) for text in connection.sent]
        assert frames[:2] == [
            {
                "data": {"run_id": "run_1", "session_id": "session_1"},
                "operation": "subscribe_run",
                "request_id": "request_gap",
                "type": "receipt",
            },
            {
                "reason": "trimmed",
                "run_id": "run_1",
                "session_id": "session_1",
                "type": "stream_gap",
            },
        ]
        assert "cursor" not in frames[1]
        assert tracked.subscriptions[0].close_count == 1

        await connection.incoming.put(_subscribe_frame("request_resume", second))
        await connection.wait_sent(4)
        receipt = json.loads(connection.sent[2])
        assert receipt == {
            "data": {"run_id": "run_1", "session_id": "session_1"},
            "operation": "subscribe_run",
            "request_id": "request_resume",
            "type": "receipt",
        }
        resumed = json.loads(connection.sent[3])
        _assert_content_event(resumed, third, 3)

        await asyncio.wait_for(replay.tail_waiting.wait(), timeout=2)
        fourth = await append_event(replay, 4)
        await connection.wait_sent(5)
        _assert_content_event(json.loads(connection.sent[4]), fourth, 4)

        await connection.incoming.put(
            json.dumps(
                {
                    "type": "unsubscribe_run",
                    "request_id": "request_stop",
                    "session_id": "session_1",
                    "run_id": "run_1",
                },
            ),
        )
        await connection.wait_sent(6)
        assert json.loads(connection.sent[5]) == {
            "data": {"run_id": "run_1", "session_id": "session_1"},
            "operation": "unsubscribe_run",
            "request_id": "request_stop",
            "type": "receipt",
        }
        assert tracked.subscriptions[1].close_count == 1
        await connection.incoming.put(WebSocketDisconnected())
        await asyncio.wait_for(task, timeout=2)
        assert [item.close_count for item in tracked.subscriptions] == [1, 1]
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        try:
            await replay.close()
        finally:
            await cleanup_redis(url, prefix)


def _cursor_headers(item: ReplayItem) -> HttpHeaders:
    return HttpHeaders((("Last-Event-ID", public_cursor(item)),))


def _subscribe_frame(request_id: str, item: ReplayItem) -> str:
    return json.dumps(
        {
            "type": "subscribe_run",
            "request_id": request_id,
            "session_id": "session_1",
            "run_id": "run_1",
            "cursor": public_cursor(item),
        },
    )


def _assert_content_event(
    frame: dict[str, object],
    item: ReplayItem,
    sequence: int,
) -> None:
    assert frame["type"] == "event"
    assert frame["cursor"] == public_cursor(item)
    assert frame["event_kind"] == "content_delta"
    data = frame["data"]
    assert isinstance(data, dict)
    assert data["event"] == {
        "index": sequence,
        "text": f"content-{sequence}",
    }
    assert data["event_sequence"] == sequence
