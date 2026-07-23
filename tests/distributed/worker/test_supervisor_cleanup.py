from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import DistributedShutdownTimeoutError
from agentos.runtime.stream_events import TurnStreamCompleted

from tests.distributed.worker._fakes import DELIVERY, FakeQueue, ScriptedStream
from tests.distributed.worker._supervisor_support import build_worker


def test_drain_timeout_closes_stream_and_leaves_claim_to_expire() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, deliveries=(DELIVERY,))
        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("unreachable"),),
            terminal_gate=asyncio.Event(),
        )
        worker, claims, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await stream.started.wait()
        await worker.drain(timeout=0)

        assert stream.closed
        assert stream.cleanup_finished.is_set()
        assert queue.acked == []
        assert claims.release_calls == 0
        assert worker.state.active_claim_count == 0
        await worker.close()

    asyncio.run(scenario())


def test_drain_cleanup_timeout_keeps_worker_draining() -> None:
    class SlowCancelQueue(FakeQueue):
        def __init__(self, trace: list[str]) -> None:
            super().__init__(trace)
            self.release_cancel = asyncio.Event()

        async def receive(self, **kwargs: object):  # type: ignore[no-untyped-def]
            del kwargs
            self.receive_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.release_cancel.wait()
                raise

    async def scenario() -> None:
        trace: list[str] = []
        queue = SlowCancelQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            shutdown_cleanup_timeout=timedelta(milliseconds=10),
        )

        await worker.start()
        await queue.receive_started.wait()
        with pytest.raises(DistributedShutdownTimeoutError):
            await worker.drain(timeout=0)

        assert worker.state.status == "draining"
        assert not worker.state.accepting_claims
        queue.release_cancel.set()
        await asyncio.sleep(0)
        await worker.close()
        assert worker.state.status == "closed"

    asyncio.run(scenario())


def test_cancelled_close_does_not_report_closed_before_queue_cleanup() -> None:
    class BlockingCloseQueue(FakeQueue):
        def __init__(self, trace: list[str]) -> None:
            super().__init__(trace)
            self.close_started = asyncio.Event()
            self.close_release = asyncio.Event()

        async def close(self) -> None:
            self.close_started.set()
            await self.close_release.wait()
            await super().close()

    async def scenario() -> None:
        trace: list[str] = []
        queue = BlockingCloseQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            shutdown_cleanup_timeout=timedelta(milliseconds=10),
        )
        closing = asyncio.create_task(worker.close())
        await queue.close_started.wait()

        closing.cancel("caller stopped")
        with pytest.raises(asyncio.CancelledError) as caught:
            await closing

        assert caught.value.args == ("caller stopped",)
        assert worker.state.status == "draining"
        assert not queue.closed

        queue.close_release.set()
        await worker.close()
        assert worker.state.status == "closed"
        assert queue.closed

    asyncio.run(scenario())


def test_cancelled_close_waits_for_active_stream_cleanup() -> None:
    class GatedCleanupStream(ScriptedStream):
        def __init__(self, trace: list[str]) -> None:
            super().__init__(
                trace,
                (TurnStreamCompleted("unreachable"),),
                terminal_gate=asyncio.Event(),
            )
            self.close_started = asyncio.Event()
            self.close_release = asyncio.Event()

        async def aclose(self) -> None:
            self.close_started.set()
            await self.close_release.wait()
            await super().aclose()

    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, deliveries=(DELIVERY,))
        stream = GatedCleanupStream(trace)
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            shutdown_cleanup_timeout=timedelta(seconds=1),
        )

        await worker.start()
        await stream.started.wait()
        closing = asyncio.create_task(worker.close())
        await stream.close_started.wait()
        closing.cancel("caller stopped")
        await asyncio.sleep(0)

        assert not closing.done()
        assert not queue.closed
        stream.close_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await closing

        assert caught.value.args == ("caller stopped",)
        assert stream.cleanup_finished.is_set()
        await asyncio.sleep(0)
        assert worker.state.active_claim_count == 0
        assert queue.closed
        assert trace.index("stream.aclose") < trace.index("queue.close")

    asyncio.run(scenario())


def test_cancelled_drain_skips_graceful_budget_and_cleans_active_stream() -> None:
    class GatedCleanupStream(ScriptedStream):
        def __init__(self, trace: list[str]) -> None:
            super().__init__(
                trace,
                (TurnStreamCompleted("unreachable"),),
                terminal_gate=asyncio.Event(),
            )
            self.close_started = asyncio.Event()
            self.close_release = asyncio.Event()

        async def aclose(self) -> None:
            self.close_started.set()
            await self.close_release.wait()
            await super().aclose()

    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, deliveries=(DELIVERY,))
        stream = GatedCleanupStream(trace)
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await stream.started.wait()
        draining = asyncio.create_task(worker.drain(timeout=60))
        await asyncio.sleep(0)
        draining.cancel("caller stopped")
        await stream.close_started.wait()

        assert not draining.done()
        stream.close_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(draining, timeout=2)

        assert caught.value.args == ("caller stopped",)
        assert stream.cleanup_finished.is_set()
        await asyncio.sleep(0)
        assert worker.state.active_claim_count == 0
        await worker.close()

    asyncio.run(scenario())


def test_cancelled_drain_waits_for_receiver_cleanup() -> None:
    class GatedReceiverQueue(FakeQueue):
        def __init__(self, trace: list[str]) -> None:
            super().__init__(trace)
            self.cleanup_started = asyncio.Event()
            self.cleanup_release = asyncio.Event()

        async def receive(self, **kwargs: object):  # type: ignore[no-untyped-def]
            del kwargs
            self.receive_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cleanup_started.set()
                await self.cleanup_release.wait()
                raise

    async def scenario() -> None:
        trace: list[str] = []
        queue = GatedReceiverQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await queue.receive_started.wait()
        draining = asyncio.create_task(worker.drain(timeout=60))
        await queue.cleanup_started.wait()
        draining.cancel("caller stopped")
        await asyncio.sleep(0)

        assert not draining.done()
        queue.cleanup_release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(draining, timeout=2)

        assert caught.value.args == ("caller stopped",)
        assert worker.state.status == "draining"
        await worker.close()

    asyncio.run(scenario())


def test_receiver_cancellation_survives_active_cleanup_timeout() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            shutdown_cleanup_timeout=timedelta(milliseconds=10),
        )
        receiver_cancelled = asyncio.Event()
        receiver_release = asyncio.Event()
        active_cancelled = asyncio.Event()
        active_release = asyncio.Event()

        async def receiver() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                receiver_cancelled.set()
                await receiver_release.wait()
                raise

        async def active() -> bool:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                active_cancelled.set()
                await active_release.wait()
                raise

        worker._status = "running"
        worker._receiver = asyncio.create_task(receiver())
        active_task = asyncio.create_task(active())
        worker._active.add(active_task)
        active_task.add_done_callback(worker._delivery_done)

        draining = asyncio.create_task(worker.drain(timeout=60))
        await receiver_cancelled.wait()
        draining.cancel("caller stopped")
        receiver_release.set()
        await active_cancelled.wait()

        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(draining, timeout=1)

        assert caught.value.args == ("caller stopped",)
        assert isinstance(caught.value.__cause__, DistributedShutdownTimeoutError)
        active_release.set()
        await asyncio.gather(active_task, return_exceptions=True)

    asyncio.run(scenario())


def test_queue_close_failure_keeps_worker_draining() -> None:
    class FailingCloseQueue(FakeQueue):
        async def close(self) -> None:
            raise RuntimeError("queue close failed")

    async def scenario() -> None:
        trace: list[str] = []
        queue = FailingCloseQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        with pytest.raises(RuntimeError, match="queue close failed"):
            await worker.close()

        assert worker.state.status == "draining"
        assert not queue.closed

    asyncio.run(scenario())


def test_queue_cleanup_timeout_supersedes_earlier_drain_failure() -> None:
    class BlockingCloseQueue(FakeQueue):
        def __init__(self, trace: list[str]) -> None:
            super().__init__(trace)
            self.close_release = asyncio.Event()

        async def close(self) -> None:
            await self.close_release.wait()
            await super().close()

    async def scenario() -> None:
        trace: list[str] = []
        queue = BlockingCloseQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            shutdown_cleanup_timeout=timedelta(milliseconds=10),
        )
        worker._failure = RuntimeError("receiver failed")

        with pytest.raises(DistributedShutdownTimeoutError):
            await worker.close()

        assert worker.state.status == "draining"
        assert not queue.closed

        queue.close_release.set()
        with pytest.raises(RuntimeError, match="receiver failed"):
            await worker.close()
        assert worker.state.status == "closed"
        assert queue.closed

    asyncio.run(scenario())
