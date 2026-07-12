import asyncio
from threading import Event as ThreadEvent

import pytest

from agentos.runtime.agent_stream import AgentStream, ExecutionLease
from agentos.runtime.stream_events import TurnStreamCompleted, TurnStreamStarted
from tests.runtime._agent_stream_thread_helpers import _ThreadWorker


def test_created_interrupt_is_lazy_runs_cleanup_on_creation_loop_and_is_not_sticky() -> None:
    async def scenario() -> None:
        starts = 0
        cleanup_loop = None
        cleanup_finished = asyncio.Event()
        interrupt_results: list[bool] = []

        async def events():
            nonlocal starts
            starts += 1
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            nonlocal cleanup_loop
            cleanup_loop = asyncio.get_running_loop()
            cleanup_finished.set()

        lease = ExecutionLease()
        assert lease.interrupt() is False

        creation_loop = asyncio.get_running_loop()
        stream = lease.open_stream(events(), cleanup=cleanup)

        def interrupt() -> None:
            interrupt_results.append(lease.interrupt())

        worker = _ThreadWorker(interrupt)
        worker.start()
        try:
            await worker.wait_async()
            await asyncio.wait_for(cleanup_finished.wait(), timeout=5)
        finally:
            worker.join()
            if not stream.closed:
                await stream.aclose()

        assert interrupt_results == [True]
        assert starts == 0
        assert cleanup_loop is creation_loop
        assert stream.closed
        assert lease.interrupt() is False

        next_stream = lease.open_stream(events(), cleanup=lambda: None)
        assert await anext(next_stream) == TurnStreamCompleted(content="done")
        with pytest.raises(StopAsyncIteration):
            await anext(next_stream)
        assert starts == 1

    asyncio.run(scenario())


def test_running_interrupt_from_another_thread_cancels_consumer() -> None:
    async def scenario() -> None:
        source_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        interrupt_results: list[bool] = []

        async def events():
            source_started.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted(content="done")

        async def cleanup() -> None:
            cleanup_finished.set()

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)
        consumer = asyncio.create_task(anext(stream))
        await source_started.wait()

        def interrupt() -> None:
            interrupt_results.append(lease.interrupt())

        worker = _ThreadWorker(interrupt)
        worker.start()
        try:
            await worker.wait_async()
            await asyncio.wait_for(cleanup_finished.wait(), timeout=5)
        finally:
            worker.join()
            if not consumer.done():
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass

        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert interrupt_results == [True]
        assert stream.closed
        assert lease.interrupt() is False

    asyncio.run(scenario())


def test_interrupt_between_anext_calls_still_finishes_cleanup() -> None:
    async def scenario() -> None:
        between_events = asyncio.Event()
        processing_blocker = asyncio.Event()
        cleanup_finished = asyncio.Event()
        interrupt_results: list[bool] = []

        async def events():
            yield TurnStreamStarted(user_message="first")
            yield TurnStreamCompleted(content="second")

        async def cleanup() -> None:
            cleanup_finished.set()

        lease = ExecutionLease()
        stream = lease.open_stream(events(), cleanup=cleanup)

        async def consume() -> None:
            assert await anext(stream) == TurnStreamStarted(user_message="first")
            between_events.set()
            await processing_blocker.wait()
            await anext(stream)

        consumer = asyncio.create_task(consume())
        await between_events.wait()

        def interrupt() -> None:
            interrupt_results.append(lease.interrupt())

        worker = _ThreadWorker(interrupt)
        worker.start()
        try:
            await worker.wait_async()
            await asyncio.wait_for(cleanup_finished.wait(), timeout=5)
        finally:
            worker.join()
            if not consumer.done():
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass

        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert interrupt_results == [True]
        assert stream.closed

    asyncio.run(scenario())


def test_created_interrupt_recovers_after_creation_loop_is_closed() -> None:
    lease = ExecutionLease()
    holder: list[AgentStream] = []
    starts = 0
    cleanup_calls = 0

    async def events():
        nonlocal starts
        starts += 1
        yield TurnStreamCompleted(content="unreachable")

    async def cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1

    async def create() -> None:
        holder.append(lease.open_stream(events(), cleanup=cleanup))

    asyncio.run(create())
    stream = holder[0]

    with pytest.raises(RuntimeError, match="Event loop is closed"):
        lease.interrupt()

    async def close_and_reuse() -> None:
        await stream.aclose()

        assert stream.closed
        assert starts == 0
        assert cleanup_calls == 1

        replacement = lease.open_stream(events(), cleanup=lambda: None)
        await replacement.aclose()

    asyncio.run(close_and_reuse())


def test_interrupt_cannot_miss_stream_while_open_is_constructing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_entered = ThreadEvent()
    allow_constructor = ThreadEvent()
    stream_ready = ThreadEvent()
    cleanup_finished = ThreadEvent()
    allow_opener_exit = ThreadEvent()
    interrupt_started = ThreadEvent()
    interrupt_results: list[bool] = []
    original_init = AgentStream.__init__

    def blocking_init(self, *args, **kwargs) -> None:
        constructor_entered.set()
        allow_constructor.wait()
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(AgentStream, "__init__", blocking_init)
    lease = ExecutionLease()

    async def open_and_wait_for_cleanup() -> None:
        async def events():
            yield TurnStreamCompleted(content="unreachable")

        async def cleanup() -> None:
            cleanup_finished.set()
            allow_opener_exit.set()

        lease.open_stream(events(), cleanup=cleanup)
        stream_ready.set()
        await asyncio.to_thread(allow_opener_exit.wait)

    def run_opener() -> None:
        asyncio.run(open_and_wait_for_cleanup())

    opener = _ThreadWorker(run_opener)
    opener.start()
    interrupter: _ThreadWorker | None = None
    try:
        assert constructor_entered.wait(timeout=5)

        def interrupt() -> None:
            interrupt_started.set()
            interrupt_results.append(lease.interrupt())

        interrupter = _ThreadWorker(interrupt)
        interrupter.start()
        assert interrupt_started.wait(timeout=5)
        allow_constructor.set()
        interrupter.wait_sync()
        assert stream_ready.wait(timeout=5)

        if interrupt_results == [False]:
            assert lease.interrupt() is True

        assert cleanup_finished.wait(timeout=5)
    finally:
        allow_constructor.set()
        allow_opener_exit.set()
        if interrupter is not None:
            interrupter.join()
        opener.join()

    opener.raise_error()
    assert interrupter is not None
    interrupter.raise_error()

    assert interrupt_results == [True]
