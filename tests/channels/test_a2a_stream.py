from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agentos.channels.a2a_stream import A2ASseResponse
from agentos.distributed.models import (
    LiveContentDelta,
    LiveTurnCompleted,
    LiveTurnWaiting,
    ReplayItem,
    RunEventEnvelope,
    StreamGap,
)
from agentos.transports.a2a import (
    A2AMessage,
    A2APart,
    A2ARole,
    A2AStreamResponse,
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
    decode_stream_operation_response,
)
from agentos.transports.sse import decode_cursor
from tests.planning._async import async_test


NOW = datetime(2026, 7, 21, 8, 30, tzinfo=UTC)


@dataclass
class Subscription:
    items: list[ReplayItem | StreamGap] = field(default_factory=list)
    block: bool = False
    close_calls: int = 0
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
        self.close_calls += 1


def _task(state: A2ATaskState = A2ATaskState.TASK_STATE_WORKING) -> A2AStreamResponse:
    return A2AStreamResponse(
        task=A2ATask(
            id="run_1",
            context_id="session_1",
            status=A2ATaskStatus(state),
        ),
    )


def _item(event: object, *, cursor: str = "1-0") -> ReplayItem:
    return ReplayItem(
        cursor,
        RunEventEnvelope(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            execution_attempt=1,
            event_sequence=2,
            event=event,  # type: ignore[arg-type]
            occurred_at=NOW,
        ),
    )


def _response(
    subscription: Subscription | None,
    *,
    initial: A2AStreamResponse | None = None,
    heartbeat_interval: float = 0.001,
) -> A2ASseResponse:
    return A2ASseResponse(
        subscription,
        request_id="rpc_1",
        opaque_request_id="opaque_1",
        initial_response=_task() if initial is None else initial,
        heartbeat_interval=heartbeat_interval,
    )


@async_test
async def test_a2a_stream_sends_snapshot_then_scoped_replay_event() -> None:
    subscription = Subscription([_item(LiveContentDelta(0, "hello"), cursor="7-2")])
    response = _response(subscription)

    assert dict(response.headers)["Content-Type"] == "text/event-stream"
    initial = await response.next_frame()
    assert initial.startswith(b"data: ")
    assert b"id:" not in initial
    assert decode_stream_operation_response(initial.removeprefix(b"data: ").strip()).result == _task()

    event = await response.next_frame()
    first_line = event.splitlines()[0]
    assert first_line.startswith(b"id: ")
    cursor = first_line.removeprefix(b"id: ").decode("ascii")
    assert decode_cursor(
        cursor,
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
    ) == "7-2"
    assert b'"statusUpdate"' in event
    assert subscription.close_calls == 0

    await response.aclose()
    assert subscription.close_calls == 1


@async_test
async def test_a2a_stream_heartbeat_does_not_advance_cursor() -> None:
    subscription = Subscription(block=True)
    response = _response(subscription)
    await response.next_frame()

    assert await response.next_frame() == b": heartbeat\n\n"
    assert subscription.close_calls == 0

    await response.aclose()
    assert subscription.close_calls == 1


@async_test
async def test_a2a_stream_gap_terminal_and_exhaustion_close_once() -> None:
    gap_subscription = Subscription(
        [StreamGap("tenant_1", "session_1", "run_1", "1-0", "2-0", "trimmed")],
    )
    gap_response = _response(gap_subscription)
    await gap_response.next_frame()
    gap = await gap_response.next_frame()
    assert gap.startswith(b"data: ")
    assert b"id:" not in gap
    assert b'"code":-32603' in gap
    assert b'"reason":"STREAM_GAP"' in gap
    assert b"trimmed" not in gap
    assert gap_subscription.close_calls == 1

    terminal_subscription = Subscription([_item(LiveTurnCompleted())])
    terminal_response = _response(terminal_subscription)
    await terminal_response.next_frame()
    terminal = await terminal_response.next_frame()
    assert b'"state":"TASK_STATE_COMPLETED"' in terminal
    assert terminal_subscription.close_calls == 1

    waiting_subscription = Subscription(
        [_item(LiveTurnWaiting("human_input", "approval_1"))],
    )
    waiting_response = _response(waiting_subscription)
    await waiting_response.next_frame()
    waiting = await waiting_response.next_frame()
    assert b'"state":"TASK_STATE_INPUT_REQUIRED"' in waiting
    assert waiting_subscription.close_calls == 1

    exhausted_subscription = Subscription()
    exhausted_response = _response(exhausted_subscription)
    await exhausted_response.next_frame()
    with pytest.raises(StopAsyncIteration):
        await exhausted_response.next_frame()
    assert exhausted_subscription.close_calls == 1


@async_test
async def test_a2a_stream_initial_only_results_need_no_subscription() -> None:
    terminal = _response(
        None,
        initial=_task(A2ATaskState.TASK_STATE_COMPLETED),
    )
    assert b'"state":"TASK_STATE_COMPLETED"' in await terminal.next_frame()
    with pytest.raises(StopAsyncIteration):
        await terminal.next_frame()
    await terminal.aclose()

    message = _response(
        None,
        initial=A2AStreamResponse(
            message=A2AMessage(
                message_id="message_1",
                role=A2ARole.ROLE_AGENT,
                parts=(A2APart(text="done"),),
            ),
        ),
    )
    assert b'"message"' in await message.next_frame()
    with pytest.raises(StopAsyncIteration):
        await message.next_frame()


def test_a2a_stream_requires_subscription_for_nonterminal_task() -> None:
    with pytest.raises(ValueError, match="subscription"):
        _response(None)
    with pytest.raises(ValueError, match="must not have a subscription"):
        _response(
            Subscription(),
            initial=_task(A2ATaskState.TASK_STATE_COMPLETED),
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a2a_stream_rejects_non_finite_heartbeat_interval(value: float) -> None:
    with pytest.raises(ValueError, match="heartbeat_interval"):
        _response(Subscription(), heartbeat_interval=value)


@async_test
async def test_a2a_stream_cancel_encode_error_and_explicit_close_release_once() -> None:
    cancelled_subscription = Subscription(block=True)
    cancelled_response = _response(cancelled_subscription)
    await cancelled_response.next_frame()
    pending = asyncio.create_task(cancelled_response.next_frame())
    await cancelled_subscription.waiting.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cancelled_subscription.close_calls == 1

    invalid_subscription = Subscription(
        [_item(LiveContentDelta(0, "hello"), cursor="invalid")],
    )
    invalid_response = _response(invalid_subscription)
    await invalid_response.next_frame()
    with pytest.raises(ValueError):
        await invalid_response.next_frame()
    assert invalid_subscription.close_calls == 1

    closed_subscription = Subscription(block=True)
    closed_response = _response(closed_subscription, heartbeat_interval=60.0)
    await closed_response.next_frame()
    pending = asyncio.create_task(closed_response.next_frame())
    await closed_subscription.waiting.wait()
    await closed_response.aclose()
    await closed_response.aclose()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed_subscription.close_calls == 1
