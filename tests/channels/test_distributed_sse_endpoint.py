from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agentos.channels.service_wiring import ChannelServices, FixedScopeAuthenticator
from agentos.channels.sse_endpoint import RunSseEndpoint, RunSseResponse
from agentos.distributed.models import (
    LiveContentDelta,
    LiveTurnCompleted,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    RunReadModel,
    StreamGap,
)
from agentos.distributed.services import (
    ArtifactService,
    RunCommandService,
    RunEventStream,
    RunQueryService,
    RunSubmissionService,
)
from agentos.runtime.run_state import RunStatus
from agentos.transports.http.request_types import HttpHeaders
from agentos.transports.sse.cursors import encode_cursor
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
NOW = datetime(2026, 7, 21, tzinfo=UTC)


@dataclass
class Subscription:
    items: list[ReplayItem | StreamGap] = field(default_factory=list)
    block: bool = False
    close_count: int = 0
    waiting: asyncio.Event = field(default_factory=asyncio.Event)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> ReplayItem | StreamGap:
        if self.items:
            return self.items.pop(0)
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.close_count += 1


class CancellationErrorSubscription(Subscription):
    async def __anext__(self) -> ReplayItem | StreamGap:
        self.waiting.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("read cancellation failed") from None
        raise AssertionError("unreachable")


@dataclass
class ReplayPort:
    subscription: Subscription

    def follow(self, **values: object) -> Subscription:
        del values
        return self.subscription


class QueryPort:
    async def get_run(self, **values: object) -> RunReadModel:
        return RunReadModel(
            values["scope"].tenant_id,  # type: ignore[union-attr]
            str(values["session_id"]),
            str(values["run_id"]),
            RunStatus.RUNNING,
            None,
            1,
            None,
        )


def _services(subscription: Subscription) -> ChannelServices:
    query = QueryPort()
    return ChannelServices(
        RunSubmissionService(object()),  # type: ignore[arg-type]
        RunCommandService(object()),  # type: ignore[arg-type]
        RunQueryService(query),  # type: ignore[arg-type]
        RunEventStream(query, ReplayPort(subscription)),  # type: ignore[arg-type]
        ArtifactService(object()),  # type: ignore[arg-type]
    )


def _item(event: object, *, cursor: str = "1-0") -> ReplayItem:
    return ReplayItem(
        cursor,
        RunEventEnvelope(
            "tenant_1",
            "session_1",
            "run_1",
            "turn_1",
            1,
            1,
            event,  # type: ignore[arg-type]
            NOW,
        ),
    )


async def _open(
    subscription: Subscription,
    headers: HttpHeaders | None = None,
) -> RunSseResponse:
    endpoint = RunSseEndpoint(
        _services(subscription),
        FixedScopeAuthenticator(SCOPE),
        heartbeat_interval=0.001,
    )
    response = await endpoint.open(
        session_id="session_1",
        run_id="run_1",
        headers=HttpHeaders(()) if headers is None else headers,
        request_id="trace_1",
    )
    assert type(response) is RunSseResponse
    return response


@async_test
async def test_sse_terminal_gap_and_exhaustion_close_once() -> None:
    terminal = Subscription([_item(LiveTurnCompleted())])
    terminal_response = await _open(terminal)
    assert b"event: turn_completed" in await terminal_response.next_frame()
    assert terminal.close_count == 1

    gap = Subscription(
        [StreamGap("tenant_1", "session_1", "run_1", "1-0", None, "unavailable")],
    )
    cursor = encode_cursor(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        position="1-0",
    )
    gap_response = await _open(gap, HttpHeaders((("Last-Event-ID", cursor),)))
    assert b"event: stream_gap" in await gap_response.next_frame()
    assert gap.close_count == 1

    exhausted = Subscription()
    exhausted_response = await _open(exhausted)
    with pytest.raises(StopAsyncIteration):
        await exhausted_response.next_frame()
    assert exhausted.close_count == 1


@async_test
async def test_sse_disconnect_cancel_encode_error_and_heartbeat_close_once() -> None:
    disconnected = Subscription(block=True)
    disconnected_response = await _open(disconnected)
    await disconnected_response.aclose()
    assert disconnected.close_count == 1

    cancelled = Subscription(block=True)
    cancelled_response = await _open(cancelled)
    pending = asyncio.create_task(cancelled_response.next_frame())
    await cancelled.waiting.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cancelled.close_count == 1

    invalid = Subscription([_item(LiveContentDelta(0, "x"), cursor="bad")])
    invalid_response = await _open(invalid)
    with pytest.raises(ValueError):
        await invalid_response.next_frame()
    assert invalid.close_count == 1

    heartbeat = Subscription(block=True)
    heartbeat_response = await _open(heartbeat)
    assert await heartbeat_response.next_frame() == b": heartbeat\n\n"
    assert heartbeat.close_count == 0
    await heartbeat_response.aclose()
    assert heartbeat.close_count == 1


@pytest.mark.parametrize(
    "value",
    [True, float("nan"), float("inf"), float("-inf")],
)
def test_run_sse_response_rejects_invalid_heartbeat_interval(value: float) -> None:
    with pytest.raises(ValueError, match="heartbeat_interval"):
        RunSseResponse(Subscription(), heartbeat_interval=value)


@async_test
async def test_run_sse_close_releases_subscription_when_read_cancel_fails() -> None:
    subscription = CancellationErrorSubscription()
    response = RunSseResponse(subscription, heartbeat_interval=60.0)
    pending = asyncio.create_task(response.next_frame())
    await subscription.waiting.wait()

    with pytest.raises(RuntimeError, match="read cancellation failed"):
        await response.aclose()

    with pytest.raises(RuntimeError, match="read cancellation failed"):
        await pending
    assert subscription.close_count == 1
