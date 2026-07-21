from __future__ import annotations

import asyncio

import pytest

from agentos.channels.websocket_buffer import (
    WebSocketBufferClosed,
    WebSocketBufferLimits,
    WebSocketBufferOverflow,
    WebSocketOutboundBuffer,
)
from tests.planning._async import async_test


@async_test
async def test_buffer_preserves_connection_order_and_counts_utf8_bytes() -> None:
    buffer = WebSocketOutboundBuffer(
        WebSocketBufferLimits(
            connection_frames=4,
            connection_bytes=16,
            subscription_frames=2,
            subscription_bytes=8,
        ),
    )

    await buffer.put_control("ok")
    await buffer.put_event(("session_1", "run_1"), "中", cursor="1-0")

    control = await buffer.get()
    event = await buffer.get()
    assert (control.text, control.subscription, control.cursor) == ("ok", None, None)
    assert (event.text, event.subscription, event.cursor) == (
        "中",
        ("session_1", "run_1"),
        "1-0",
    )
    assert buffer.queued_frames == 0
    assert buffer.queued_bytes == 0


@async_test
async def test_subscription_limits_fail_without_mutating_existing_queue() -> None:
    buffer = WebSocketOutboundBuffer(
        WebSocketBufferLimits(
            connection_frames=4,
            connection_bytes=16,
            subscription_frames=1,
            subscription_bytes=4,
        ),
    )
    key = ("session_1", "run_1")
    await buffer.put_event(key, "one", cursor="1-0")

    with pytest.raises(WebSocketBufferOverflow):
        await buffer.put_event(key, "two", cursor="2-0")

    assert buffer.queued_frames == 1
    assert (await buffer.get()).text == "one"


@async_test
async def test_connection_limits_include_control_and_all_subscriptions() -> None:
    buffer = WebSocketOutboundBuffer(
        WebSocketBufferLimits(
            connection_frames=2,
            connection_bytes=6,
            subscription_frames=2,
            subscription_bytes=6,
        ),
    )
    await buffer.put_control("aa")
    await buffer.put_event(("session_1", "run_1"), "bb", cursor="1-0")

    with pytest.raises(WebSocketBufferOverflow):
        await buffer.put_event(("session_2", "run_2"), "cc", cursor="1-0")

    assert buffer.queued_frames == 2
    assert buffer.queued_bytes == 4


@async_test
async def test_drop_removes_only_selected_subscription_frames() -> None:
    buffer = WebSocketOutboundBuffer(WebSocketBufferLimits())
    first = ("session_1", "run_1")
    second = ("session_2", "run_2")
    await buffer.put_event(first, "first", cursor="1-0")
    await buffer.put_control("receipt")
    await buffer.put_event(second, "second", cursor="1-0")

    await buffer.drop(first)

    assert (await buffer.get()).text == "receipt"
    assert (await buffer.get()).text == "second"


@async_test
async def test_close_wakes_waiter_and_rejects_new_frames() -> None:
    buffer = WebSocketOutboundBuffer(WebSocketBufferLimits())
    waiter = asyncio.create_task(buffer.get())
    await asyncio.sleep(0)

    await buffer.aclose()

    with pytest.raises(WebSocketBufferClosed):
        await waiter
    with pytest.raises(WebSocketBufferClosed):
        await buffer.put_control("late")


@pytest.mark.parametrize(
    "values",
    [
        {"connection_frames": 0},
        {"connection_frames": 2049},
        {"connection_bytes": 16 * 1024 * 1024 + 1},
        {"subscription_frames": 513},
        {"subscription_bytes": 2 * 1024 * 1024 + 1},
        {"connection_frames": 1, "subscription_frames": 2},
        {"connection_bytes": 1, "subscription_bytes": 2},
    ],
)
def test_limits_reject_invalid_or_incoherent_values(values: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="WebSocket buffer limits"):
        WebSocketBufferLimits(**values)
