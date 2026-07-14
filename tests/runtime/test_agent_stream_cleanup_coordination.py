import asyncio
from threading import Event as ThreadEvent

import pytest

from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.errors import AgentBusyError
from agentos.runtime.stream_events import (
    TurnStreamCompleted,
    TurnStreamFailed,
    TurnStreamStarted,
)
from tests.runtime._agent_stream_thread_helpers import _ThreadWorker


@pytest.mark.parametrize("cancel_stage", ["source", "pending", "cleanup"])
def test_cancelled_body_error_cleanup_owner_finishes_all_stages(
    cancel_stage: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BodyError(RuntimeError):
        pass

    async def scenario() -> None:
        order: list[str] = []
        stage_started = asyncio.Event()

        async def run_stage(name: str) -> None:
            order.append(name)
            if cancel_stage == name:
                stage_started.set()
                await asyncio.Event().wait()

        class Source:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self) -> None:
                await run_stage("source")

        class PendingWork:
            async def wait_until_idle(self) -> None:
                await run_stage("pending")

        async def cleanup() -> None:
            await run_stage("cleanup")

        lease = ExecutionLease()
        original_release = lease._release

        def record_release(stream: AgentStream) -> None:
            order.append("release")
            original_release(stream)

        monkeypatch.setattr(lease, "_release", record_release)
        stream = lease.open_stream(
            Source(),
            cleanup=cleanup,
            pending_sync_work=PendingWork(),
        )

        async def body_owner() -> None:
            async with stream:
                raise BodyError("body failed")

        owner = asyncio.create_task(body_owner())
        await stage_started.wait()
        completion_waiter = asyncio.create_task(stream.aclose())
        waiter_blocked = asyncio.Event()
        asyncio.get_running_loop().call_soon(waiter_blocked.set)
        await waiter_blocked.wait()
        assert not completion_waiter.done()

        owner.cancel("owner cancelled")
        with pytest.raises(asyncio.CancelledError) as captured:
            await owner
        await completion_waiter

        assert captured.value.args == ("owner cancelled",)
        assert owner.cancelled()
        assert order == ["source", "pending", "cleanup", "release"]
        assert stream.closed

        async def replacement_events():
            yield TurnStreamCompleted(content="done")

        replacement = lease.open_stream(replacement_events(), cleanup=lambda: None)
        await replacement.aclose()

    asyncio.run(scenario())


def test_pending_sync_work_finishes_before_cleanup_and_lease_release() -> None:
    class PendingWorkRecorder:
        def __init__(self, order: list[str]) -> None:
            self.order = order
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def wait_until_idle(self) -> None:
            self.order.append("pending_idle")
            self.started.set()
            await self.release.wait()

    async def scenario() -> None:
        order: list[str] = []
        first_received = asyncio.Event()
        close_consumer = asyncio.Event()

        async def events():
            try:
                yield TurnStreamStarted(user_message="first")
                await asyncio.Event().wait()
            finally:
                order.append("generator_close")

        async def cleanup() -> None:
            order.append("cleanup")

        lease = ExecutionLease()
        pending = PendingWorkRecorder(order)
        stream = lease.open_stream(
            events(),
            cleanup=cleanup,
            pending_sync_work=pending,
        )

        async def consume_and_close() -> None:
            assert await anext(stream) == TurnStreamStarted(user_message="first")
            first_received.set()
            await close_consumer.wait()
            await stream.aclose()

        consumer = asyncio.create_task(consume_and_close())
        await first_received.wait()
        close_consumer.set()
        await pending.started.wait()

        assert order == ["generator_close", "pending_idle"]
        with pytest.raises(AgentBusyError):
            lease.open_stream(events(), cleanup=cleanup)

        pending.release.set()
        await consumer

        assert order == ["generator_close", "pending_idle", "cleanup"]
        next_stream = lease.open_stream(events(), cleanup=cleanup)
        await next_stream.aclose()

    asyncio.run(scenario())


def test_created_aclose_from_another_loop_uses_closer_loop() -> None:
    async def scenario() -> None:
        starts = 0
        cleanup_loop = None
        closer_loop = None
        cleanup_finished = asyncio.Event()

        async def events():
            nonlocal starts
            starts += 1
            yield TurnStreamCompleted(content="done")

        creation_loop = asyncio.get_running_loop()

        async def cleanup() -> None:
            nonlocal cleanup_loop
            cleanup_loop = asyncio.get_running_loop()
            creation_loop.call_soon_threadsafe(cleanup_finished.set)

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)

        def close_from_worker_loop() -> None:
            nonlocal closer_loop

            async def close() -> None:
                nonlocal closer_loop
                closer_loop = asyncio.get_running_loop()
                await stream.aclose()

            asyncio.run(close())

        worker = _ThreadWorker(close_from_worker_loop)
        worker.start()
        try:
            await worker.wait_async()
            await asyncio.wait_for(cleanup_finished.wait(), timeout=5)
        finally:
            worker.join()
            if not stream.closed:
                await stream.aclose()

        assert cleanup_loop is closer_loop
        assert cleanup_loop is not creation_loop
        assert starts == 0
        assert stream.closed

    asyncio.run(scenario())


def test_created_aclose_succeeds_after_creation_loop_is_closed() -> None:
    lease = ExecutionLease()
    holder: list[AgentStream] = []
    starts = 0
    cleanup_loop = None

    async def events():
        nonlocal starts
        starts += 1
        yield TurnStreamCompleted(content="unreachable")

    async def cleanup() -> None:
        nonlocal cleanup_loop
        cleanup_loop = asyncio.get_running_loop()

    async def create() -> None:
        holder.append(lease.open_stream(events(), cleanup=cleanup))

    asyncio.run(create())
    stream = holder[0]

    async def close_and_reuse() -> None:
        await stream.aclose()

        assert cleanup_loop is asyncio.get_running_loop()
        assert stream.closed
        assert starts == 0

        next_stream = lease.open_stream(events(), cleanup=lambda: None)
        await next_stream.aclose()

    asyncio.run(close_and_reuse())


def test_finish_runs_all_cleanup_stages_after_earlier_stage_errors() -> None:
    class SourceCloseError(RuntimeError):
        pass

    class PendingError(RuntimeError):
        pass

    class CleanupError(RuntimeError):
        pass

    class FailingPendingWork:
        async def wait_until_idle(self) -> None:
            order.append("pending_idle")
            raise PendingError("pending failed")

    class FailingSource:
        def __init__(self) -> None:
            self.sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return TurnStreamStarted(user_message="hello")

        async def aclose(self) -> None:
            order.append("source_close")
            raise SourceCloseError("source close failed")

    async def scenario() -> None:
        async def cleanup() -> None:
            order.append("cleanup")
            raise CleanupError("cleanup failed")

        stream = ExecutionLease().open_stream(
            FailingSource(),
            cleanup=cleanup,
            pending_sync_work=FailingPendingWork(),
        )
        assert await anext(stream) == TurnStreamStarted(user_message="hello")

        with pytest.raises(SourceCloseError, match="source close failed"):
            await stream.aclose()

        assert order == ["source_close", "pending_idle", "cleanup"]
        assert stream.closed

    order: list[str] = []
    asyncio.run(scenario())


def test_cleanup_owner_reentrant_close_does_not_wait_for_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        source_close_calls = 0
        cleanup_calls = 0
        release_calls = 0
        stream_holder: list[AgentStream] = []

        async def events():
            nonlocal source_close_calls
            try:
                yield TurnStreamStarted(user_message="hello")
            finally:
                source_close_calls += 1

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            await stream_holder[0].aclose()

        lease = ExecutionLease()
        original_release = lease._release

        def record_release(stream: AgentStream) -> None:
            nonlocal release_calls
            release_calls += 1
            original_release(stream)

        monkeypatch.setattr(lease, "_release", record_release)
        stream = lease.open_stream(events(), cleanup=cleanup)
        stream_holder.append(stream)

        assert await anext(stream) == TurnStreamStarted(user_message="hello")
        await stream.aclose()

        assert stream.closed
        assert source_close_calls == 1
        assert cleanup_calls == 1
        assert release_calls == 1

        replacement = lease.open_stream(events(), cleanup=lambda: None)
        await replacement.aclose()

    asyncio.run(scenario())


def test_cleanup_owner_reentrant_anext_does_not_consume_pending_failure() -> None:
    class SourceError(RuntimeError):
        pass

    async def scenario() -> None:
        error = SourceError("source failed")
        cleanup_reentry: list[str | BaseException] = []
        stream_holder: list[AgentStream] = []

        async def events():
            yield TurnStreamFailed(error=error)

        async def cleanup() -> None:
            try:
                await anext(stream_holder[0])
            except StopAsyncIteration:
                cleanup_reentry.append("stopped")
            except BaseException as cleanup_error:
                cleanup_reentry.append(cleanup_error)

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        stream_holder.append(stream)

        assert await anext(stream) == TurnStreamFailed(error=error)
        assert cleanup_reentry == ["stopped"]

        with pytest.raises(SourceError) as captured:
            await anext(stream)
        assert captured.value is error

        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_iteration_waits_for_release_and_shared_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_started = ThreadEvent()
    release_allowed = ThreadEvent()

    async def scenario() -> None:
        async def events():
            yield TurnStreamCompleted(content="unreachable")

        lease = ExecutionLease()
        original_release = lease._release

        def blocking_release(stream: AgentStream) -> None:
            release_started.set()
            release_allowed.wait()
            original_release(stream)

        monkeypatch.setattr(lease, "_release", blocking_release)
        stream = lease.open_stream(events(), cleanup=lambda: None)

        def close_from_worker() -> None:
            asyncio.run(stream.aclose())

        closer = _ThreadWorker(close_from_worker)
        closer.start()
        next_event = None
        try:
            assert await asyncio.to_thread(release_started.wait, 5)

            assert not stream.closed
            next_event = asyncio.create_task(anext(stream))
            next_event_started = asyncio.Event()
            asyncio.get_running_loop().call_soon(next_event_started.set)
            await next_event_started.wait()

            waited_for_completion = not next_event.done()
        finally:
            release_allowed.set()
            closer.join()

        closer.raise_error()
        assert next_event is not None
        with pytest.raises(StopAsyncIteration):
            await next_event
        assert waited_for_completion
        assert stream.closed

    asyncio.run(scenario())
