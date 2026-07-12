import asyncio

import pytest

from agentos.runtime.agent_stream import ExecutionLease
from agentos.runtime.stream_events import TurnStreamCompleted, TurnStreamStarted


def test_external_close_cancels_consumer_and_waits_for_cleanup() -> None:
    async def scenario() -> None:
        source_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_blocker = asyncio.Event()
        cleanup_calls = 0

        async def events():
            source_started.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            cleanup_started.set()
            await cleanup_blocker.wait()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await source_started.wait()

        closer = asyncio.create_task(stream.aclose())
        await cleanup_started.wait()

        assert consumer.cancelling()
        assert not closer.done()
        assert not stream.closed

        cleanup_blocker.set()
        await closer
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert cleanup_calls == 1
        assert stream.closed

    asyncio.run(scenario())


def test_cancelled_external_closer_does_not_strand_recovered_consumer_cleanup() -> None:
    async def scenario() -> None:
        source_started = asyncio.Event()
        cancel_caught = asyncio.Event()
        allow_return = asyncio.Event()
        cleanup_finished = asyncio.Event()
        cleanup_calls = 0

        async def events():
            source_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancel_caught.set()
                await allow_return.wait()
                yield TurnStreamStarted(user_message="recovered")

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            cleanup_finished.set()

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await source_started.wait()

        closer = asyncio.create_task(stream.aclose())
        await cancel_caught.wait()
        closer.cancel("closer cancelled")
        with pytest.raises(asyncio.CancelledError) as captured:
            await closer
        assert captured.value.args == ("closer cancelled",)
        assert closer.cancelled()

        allow_return.set()
        assert await consumer == TurnStreamStarted(user_message="recovered")
        assert not consumer.cancelled()

        await asyncio.wait_for(cleanup_finished.wait(), timeout=1)
        assert cleanup_calls == 1
        assert stream.closed

        replacement = lease.open_stream(events(), cleanup=lambda: None)
        await replacement.aclose()

    asyncio.run(scenario())


def test_anext_and_multiple_aclose_calls_share_one_cleanup() -> None:
    async def scenario() -> None:
        source_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        cleanup_calls = 0

        async def events():
            source_started.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            cleanup_finished.set()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await source_started.wait()

        closers = [asyncio.create_task(stream.aclose()) for _ in range(3)]
        await cleanup_finished.wait()
        await asyncio.gather(*closers)

        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert cleanup_calls == 1
        assert stream.closed

    asyncio.run(scenario())


def test_cancelled_completion_waiter_does_not_poison_cleanup_owner() -> None:
    async def scenario() -> None:
        first_received = asyncio.Event()
        begin_close = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_allowed = asyncio.Event()

        async def events():
            yield TurnStreamStarted(user_message="first")
            await asyncio.Event().wait()

        async def cleanup() -> None:
            cleanup_started.set()
            await cleanup_allowed.wait()

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)

        async def consume_and_close() -> None:
            assert await anext(stream) == TurnStreamStarted(user_message="first")
            first_received.set()
            await begin_close.wait()
            await stream.aclose()

        owner = asyncio.create_task(consume_and_close())
        await first_received.wait()
        begin_close.set()
        await cleanup_started.wait()

        async def wait_for_next() -> None:
            await anext(stream)

        waiter = asyncio.create_task(wait_for_next())
        waiter_entered = asyncio.Event()
        asyncio.get_running_loop().call_soon(waiter_entered.set)
        await waiter_entered.wait()
        waiter.cancel("waiter cancelled")
        with pytest.raises(asyncio.CancelledError) as captured:
            await waiter
        assert captured.value.args == ("waiter cancelled",)
        assert waiter.cancelled()

        cleanup_allowed.set()
        await owner
        assert stream.closed

        replacement = lease.open_stream(events(), cleanup=lambda: None)
        await replacement.aclose()

    asyncio.run(scenario())


def test_cancelled_body_error_waiter_propagates_cancellation() -> None:
    class BodyError(RuntimeError):
        pass

    async def scenario() -> None:
        body_entered = asyncio.Event()
        raise_body_error = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_allowed = asyncio.Event()

        async def events():
            yield TurnStreamCompleted(content="unreachable")

        async def cleanup() -> None:
            cleanup_started.set()
            await cleanup_allowed.wait()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        async def body_waiter() -> None:
            async with stream:
                body_entered.set()
                await raise_body_error.wait()
                raise BodyError("body failed")

        waiter = asyncio.create_task(body_waiter())
        await body_entered.wait()
        owner = asyncio.create_task(stream.aclose())
        await cleanup_started.wait()

        raise_body_error.set()
        waiter_blocked = asyncio.Event()
        asyncio.get_running_loop().call_soon(waiter_blocked.set)
        await waiter_blocked.wait()
        assert not waiter.done()

        waiter.cancel("waiter cancelled")
        with pytest.raises(asyncio.CancelledError) as captured:
            await waiter
        assert captured.value.args == ("waiter cancelled",)
        assert waiter.cancelled()

        cleanup_allowed.set()
        await owner
        assert stream.closed

    asyncio.run(scenario())


def test_cancelled_iteration_waiter_does_not_cancel_other_closer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        source_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_allowed = asyncio.Event()

        async def events():
            source_started.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted(content="unreachable")

        async def cleanup() -> None:
            cleanup_started.set()
            await cleanup_allowed.wait()

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await source_started.wait()

        loop = asyncio.get_running_loop()
        original_call_soon_threadsafe = loop.call_soon_threadsafe
        held_cancel_callbacks: list[tuple[object, tuple[object, ...]]] = []

        def hold_consumer_cancel(callback, *args, context=None):
            if not held_cancel_callbacks:
                held_cancel_callbacks.append((callback, args))
                return None
            return original_call_soon_threadsafe(callback, *args, context=context)

        monkeypatch.setattr(loop, "call_soon_threadsafe", hold_consumer_cancel)

        first_closer = asyncio.create_task(stream.aclose())
        second_closer = asyncio.create_task(stream.aclose())
        closers_entered = asyncio.Event()
        loop.call_soon(closers_entered.set)
        await closers_entered.wait()

        assert len(held_cancel_callbacks) == 1
        assert not first_closer.done()
        assert not second_closer.done()

        consumer.cancel()
        await cleanup_started.wait()

        first_closer.cancel("closer cancelled")
        with pytest.raises(asyncio.CancelledError) as captured:
            await first_closer
        assert captured.value.args == ("closer cancelled",)

        waiter_turn = asyncio.Event()
        loop.call_soon(waiter_turn.set)
        await waiter_turn.wait()
        assert not second_closer.done()

        cleanup_allowed.set()
        assert await asyncio.gather(second_closer, return_exceptions=True) == [None]
        consumer_result = await asyncio.gather(consumer, return_exceptions=True)

        assert isinstance(consumer_result[0], asyncio.CancelledError)
        assert stream.closed

        replacement = lease.open_stream(events(), cleanup=lambda: None)
        await replacement.aclose()

    asyncio.run(scenario())


def test_direct_consumer_cancellation_cleans_up_exactly_once() -> None:
    async def scenario() -> None:
        source_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        cleanup_calls = 0

        async def events():
            source_started.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            cleanup_finished.set()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await source_started.wait()

        consumer.cancel("consumer cancelled")
        with pytest.raises(asyncio.CancelledError) as captured:
            await consumer
        assert captured.value.args == ("consumer cancelled",)
        await cleanup_finished.wait()

        assert cleanup_calls == 1
        assert stream.closed
        await stream.aclose()
        assert cleanup_calls == 1

    asyncio.run(scenario())
