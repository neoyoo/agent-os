import asyncio
from threading import Event, Thread

import pytest

from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime.errors import (
    AgentBusyError,
    AgentStreamClosedError,
    AgentStreamConsumerError,
)
from agentos.runtime.stream_events import (
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamFailed,
    TurnStreamStarted,
)


def test_execution_lease_wait_until_idle_returns_after_stream_release() -> None:
    async def run() -> None:
        async def events():
            yield TurnStreamStarted("hello")

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=lambda: None)
        waiting = Event()
        returned = Event()

        def wait_until_idle() -> None:
            waiting.set()
            lease.wait_until_idle()
            returned.set()

        waiter = Thread(target=wait_until_idle)
        waiter.start()
        assert waiting.wait(timeout=1)
        assert not returned.is_set()

        await stream.aclose()

        assert returned.wait(timeout=1)
        waiter.join(timeout=1)

    asyncio.run(run())


def test_stream_is_lazy_and_busy_conflicts_are_immediate() -> None:
    async def scenario() -> None:
        started = False
        cleaned = 0

        async def events():
            nonlocal started
            started = True
            yield TurnStreamStarted(user_message="hello")

        async def cleanup() -> None:
            nonlocal cleaned
            cleaned += 1

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)

        assert not started
        assert not stream.closed
        with pytest.raises(AgentBusyError):
            lease.open_stream(events(), cleanup=cleanup)
        assert not started

        await stream.aclose()

        assert not started
        assert stream.closed
        assert cleaned == 1

    asyncio.run(scenario())


def test_unconsumed_close_releases_lease_for_next_stream() -> None:
    async def scenario() -> None:
        starts = 0

        async def events():
            nonlocal starts
            starts += 1
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            return None

        lease = ExecutionLease()
        first = lease.open_stream(events(), cleanup=cleanup)
        await first.aclose()
        second = lease.open_stream(events(), cleanup=cleanup)
        await second.aclose()

        assert starts == 0

    asyncio.run(scenario())


def test_normal_exhaustion_cleans_up_and_releases_lease() -> None:
    async def scenario() -> None:
        cleaned = 0

        async def events():
            yield TurnStreamStarted(user_message="hello")
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            nonlocal cleaned
            cleaned += 1

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)

        assert [event async for event in stream] == [
            TurnStreamStarted(user_message="hello"),
            TurnStreamCompleted(content="done"),
        ]
        assert stream.closed
        assert cleaned == 1

        next_stream = lease.open_stream(events(), cleanup=cleanup)
        await next_stream.aclose()

    asyncio.run(scenario())


def test_aclose_is_idempotent_and_closed_behavior_is_stable() -> None:
    async def scenario() -> None:
        cleaned = 0

        async def events():
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            nonlocal cleaned
            cleaned += 1

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        await asyncio.gather(stream.aclose(), stream.aclose(), stream.aclose())

        assert stream.closed
        assert cleaned == 1
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        with pytest.raises(AgentStreamClosedError):
            await stream.__aenter__()

    asyncio.run(scenario())


def test_second_consumer_task_is_rejected() -> None:
    async def scenario() -> None:
        async def events():
            yield TurnStreamStarted(user_message="hello")
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            return None

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        assert await anext(stream) == TurnStreamStarted(user_message="hello")

        async def consume_from_another_task() -> None:
            with pytest.raises(AgentStreamConsumerError):
                await anext(stream)

        await asyncio.create_task(consume_from_another_task())
        assert await anext(stream) == TurnStreamCompleted(content="done")
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_consumer_can_close_its_own_stream_without_self_cancellation() -> None:
    async def scenario() -> None:
        cleanup_finished = asyncio.Event()

        async def events():
            yield TurnStreamStarted(user_message="hello")
            yield TurnStreamCompleted(content="unreachable")

        async def cleanup() -> None:
            cleanup_finished.set()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        async def consume_and_close() -> TurnStreamStarted:
            first = await anext(stream)
            await stream.aclose()
            assert not asyncio.current_task().cancelled()
            return first  # type: ignore[return-value]

        consumer = asyncio.create_task(consume_and_close())

        assert await consumer == TurnStreamStarted(user_message="hello")
        assert cleanup_finished.is_set()
        assert stream.closed

    asyncio.run(scenario())


def test_source_error_wins_over_cleanup_error_and_cleanup_runs_once() -> None:
    class SourceError(RuntimeError):
        pass

    class CleanupError(RuntimeError):
        pass

    async def scenario() -> None:
        cleanup_calls = 0

        async def events():
            if False:
                yield TurnStreamCompleted(content="unreachable")
            raise SourceError("source failed")

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            raise CleanupError("cleanup failed")

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)

        with pytest.raises(SourceError, match="source failed"):
            await anext(stream)
        await stream.aclose()

        assert cleanup_calls == 1
        assert stream.closed
        next_stream = lease.open_stream(events(), cleanup=lambda: None)
        await next_stream.aclose()

    asyncio.run(scenario())


def test_cleanup_error_propagates_when_there_is_no_source_error() -> None:
    class CleanupError(RuntimeError):
        pass

    async def scenario() -> None:
        async def events():
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            raise CleanupError("cleanup failed")

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        with pytest.raises(CleanupError, match="cleanup failed"):
            await stream.aclose()
        with pytest.raises(CleanupError, match="cleanup failed"):
            await stream.aclose()
        assert stream.closed

    asyncio.run(scenario())


def test_execution_lease_is_reusable_across_separate_event_loops() -> None:
    lease = ExecutionLease()
    cleaned: list[str] = []

    def run_once(content: str) -> None:
        async def scenario() -> None:
            async def events():
                yield TurnStreamCompleted(content=content)

            async def cleanup() -> None:
                cleaned.append(content)

            stream = lease.open_stream(events(), cleanup=cleanup)
            assert [event async for event in stream] == [
                TurnStreamCompleted(content=content),
            ]

        asyncio.run(scenario())

    run_once("first")
    run_once("second")

    assert cleaned == ["first", "second"]


def test_context_body_error_wins_over_cleanup_error() -> None:
    class BodyError(RuntimeError):
        pass

    class CleanupError(RuntimeError):
        pass

    async def scenario() -> None:
        async def events():
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            raise CleanupError("cleanup failed")

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        with pytest.raises(BodyError, match="body failed"):
            async with stream:
                raise BodyError("body failed")
        assert stream.closed
        await stream.aclose()

    asyncio.run(scenario())


def test_closing_stream_waits_for_completion_before_stopping_iteration() -> None:
    async def scenario() -> None:
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        starts = 0

        async def events():
            nonlocal starts
            starts += 1
            yield TurnStreamCompleted(content="unreachable")

        async def cleanup() -> None:
            cleanup_started.set()
            await cleanup_release.wait()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        closer = asyncio.create_task(stream.aclose())
        await cleanup_started.wait()

        next_event = asyncio.create_task(anext(stream))
        next_event_started = asyncio.Event()
        asyncio.get_running_loop().call_soon(next_event_started.set)
        await next_event_started.wait()

        assert not closer.done()
        assert not next_event.done()
        assert starts == 0

        cleanup_release.set()
        await closer
        with pytest.raises(StopAsyncIteration):
            await next_event

    asyncio.run(scenario())


def test_closing_stream_rejects_context_reentry() -> None:
    async def scenario() -> None:
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()

        async def events():
            yield TurnStreamCompleted(content="unreachable")

        async def cleanup() -> None:
            cleanup_started.set()
            await cleanup_release.wait()

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        closer = asyncio.create_task(stream.aclose())
        await cleanup_started.wait()

        with pytest.raises(AgentStreamClosedError):
            await stream.__aenter__()

        cleanup_release.set()
        await closer

    asyncio.run(scenario())


def test_completed_event_finishes_before_it_is_returned() -> None:
    async def scenario() -> None:
        generator_closed = False
        advanced_past_terminal = False
        cleaned = False

        async def events():
            nonlocal generator_closed, advanced_past_terminal
            try:
                yield TurnStreamStarted(user_message="hello")
                yield TurnStreamCompleted(content="done")
                advanced_past_terminal = True
            finally:
                generator_closed = True

        async def cleanup() -> None:
            nonlocal cleaned
            cleaned = True

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)

        assert await anext(stream) == TurnStreamStarted(user_message="hello")
        terminal = await anext(stream)

        assert terminal == TurnStreamCompleted(content="done")
        assert generator_closed
        assert cleaned
        assert stream.closed
        assert not advanced_past_terminal

        next_stream = lease.open_stream(events(), cleanup=lambda: None)
        await next_stream.aclose()

    asyncio.run(scenario())


def test_failed_event_is_returned_once_then_rethrows_same_error() -> None:
    async def scenario() -> None:
        error = RuntimeError("provider failed")
        failed = TurnStreamFailed(error=error)
        generator_closed = False

        async def events():
            nonlocal generator_closed
            try:
                yield failed
                raise AssertionError("source advanced past terminal failure")
            finally:
                generator_closed = True

        stream = ExecutionLease().open_stream(events(), cleanup=lambda: None)

        assert await anext(stream) is failed
        assert generator_closed
        assert stream.closed
        with pytest.raises(RuntimeError) as raised:
            await anext(stream)
        assert raised.value is error
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_cancelled_event_finishes_before_it_is_returned() -> None:
    async def scenario() -> None:
        cleaned = False
        cancelled = TurnStreamCancelled(reason="requested")

        async def events():
            yield cancelled
            raise AssertionError("source advanced past terminal cancellation")

        async def cleanup() -> None:
            nonlocal cleaned
            cleaned = True

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        assert await anext(stream) is cancelled
        assert cleaned
        assert stream.closed
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_normal_source_exhaustion_still_closes_source_before_cleanup() -> None:
    class ExhaustedSource:
        def __init__(self, order: list[str]) -> None:
            self.order = order

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def aclose(self) -> None:
            self.order.append("source_close")

    async def scenario() -> None:
        order: list[str] = []

        async def cleanup() -> None:
            order.append("cleanup")

        stream = ExecutionLease().open_stream(
            ExhaustedSource(order),
            cleanup=cleanup,
        )

        with pytest.raises(StopAsyncIteration):
            await anext(stream)

        assert order == ["source_close", "cleanup"]
        assert stream.closed

    asyncio.run(scenario())
