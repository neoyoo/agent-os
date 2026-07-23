from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import get_type_hints

import pytest

from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
)
from agentos.distributed.models import QueueDelivery
from agentos.distributed.worker.supervisor import DeliveryRunner, DistributedWorker
from agentos.runtime.stream_events import TurnStreamCompleted

from tests.distributed.worker._fakes import (
    DELIVERY,
    FakeQueue,
    HeartbeatGate,
    NOW,
    ScriptedStream,
)
from tests.distributed.worker._supervisor_support import build_worker


def test_worker_depends_on_delivery_runner_protocol() -> None:
    assert DeliveryRunner._is_protocol
    assert get_type_hints(DistributedWorker.__init__)["runner"] is DeliveryRunner


def test_close_before_start_allows_wait_to_observe_terminal_state() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.close()
        await asyncio.wait_for(worker.wait(), timeout=0.1)

        assert worker.state.status == "closed"
        assert queue.closed

    asyncio.run(scenario())


def test_drain_before_start_allows_wait_to_observe_stopped_receiver() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.drain(timeout=0)
        await asyncio.wait_for(worker.wait(), timeout=0.1)

        assert worker.state.status == "draining"
        await worker.close()

    asyncio.run(scenario())


def test_worker_runs_full_receive_to_ack_path_and_drains_receive() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, (DELIVERY,))
        stream = ScriptedStream(trace, (TurnStreamCompleted("done"),))
        worker, claims, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await queue.acknowledged.wait()
        while trace.count("queue.receive") < 2:
            await asyncio.sleep(0)
        await worker.drain(timeout=1)

        expected = [
            "queue.receive",
            "postgres.resolve",
            "lease.acquire",
            "postgres.claim",
            "lease.ensure_owned",
            "agent.hydrate",
            "agent.run:True",
            "run_driver.commit",
            "stream.aclose",
            "event.ensure_terminal",
            "queue.ack",
            "lease.release",
        ]
        positions = [trace.index(item) for item in expected]
        assert positions == sorted(positions)
        assert claims.release_calls == 0
        assert queue.receive_cancelled.is_set()
        assert worker.state.status == "draining"
        assert worker.state.active_claim_count == 0

        await worker.close()
        assert worker.state.status == "closed"
        assert queue.closed

    asyncio.run(scenario())


def test_drain_keeps_active_heartbeat_until_execution_finishes() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, (DELIVERY,))
        terminal_gate = asyncio.Event()
        heartbeat = HeartbeatGate()
        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("done"),),
            terminal_gate=terminal_gate,
        )
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            heartbeat_wait=heartbeat,
        )

        await worker.start()
        await stream.started.wait()
        await heartbeat.waiting.wait()
        draining = asyncio.create_task(worker.drain(timeout=1))
        await asyncio.sleep(0)
        assert worker.state.status == "draining"
        assert not draining.done()

        heartbeat.release.set()
        while "postgres.heartbeat" not in trace:
            await asyncio.sleep(0)
        terminal_gate.set()
        await draining

        assert trace.index("postgres.heartbeat") < trace.index("queue.ack")
        assert worker.state.status == "draining"
        await worker.close()

    asyncio.run(scenario())


def test_full_capacity_keeps_readiness_heartbeat_fresh() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, (DELIVERY,))
        terminal_gate = asyncio.Event()
        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("done"),),
            terminal_gate=terminal_gate,
        )
        current = [NOW]
        worker, _, _ = build_worker(
            trace=trace,
            queue=queue,
            stream=stream,
            heartbeat_interval=timedelta(milliseconds=10),
            clock=lambda: current[0],
        )

        await worker.start()
        await stream.started.wait()
        assert worker.state.active_claim_count == 1
        current[0] = NOW + timedelta(seconds=1)

        await asyncio.wait_for(
            _wait_until_heartbeat(worker, current[0]),
            timeout=1,
        )

        assert worker.state.accepting_claims
        assert worker.state.active_claim_count == 1
        terminal_gate.set()
        await queue.acknowledged.wait()
        await worker.drain(timeout=1)
        await worker.close()

    asyncio.run(scenario())


def test_receive_failure_stops_accepting_claims_and_surfaces_on_drain() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace)
        queue.receive_error = DeliveryUnavailableError()
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await queue.receive_started.wait()
        while worker.state.accepting_claims:
            await asyncio.sleep(0)

        assert worker.state.status == "running"
        assert worker.state.active_claim_count == 0
        assert queue.acked == []
        with pytest.raises(DeliveryUnavailableError):
            await worker.close()
        assert worker.state.status == "closed"
        assert queue.closed

    asyncio.run(scenario())


def test_wait_surfaces_the_original_receiver_failure() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        failure = DeliveryUnavailableError()
        queue = FakeQueue(trace)
        queue.receive_error = failure
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()

        with pytest.raises(DeliveryUnavailableError) as raised:
            await worker.wait()

        assert raised.value is failure
        with pytest.raises(DeliveryUnavailableError):
            await worker.close()

    asyncio.run(scenario())


def test_wait_reports_an_unexpected_normal_receiver_stop() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        async def stop_normally() -> None:
            return None

        worker._receive_loop = stop_normally  # type: ignore[method-assign]
        await worker.start()

        with pytest.raises(
            RuntimeError,
            match="worker receive loop stopped unexpectedly",
        ):
            await worker.wait()
        with pytest.raises(RuntimeError):
            await worker.close()

    asyncio.run(scenario())


def test_cancelling_wait_observer_does_not_cancel_receiver() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await queue.receive_started.wait()
        observer = asyncio.create_task(worker.wait())
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer

        assert worker.state.accepting_claims
        await worker.drain(timeout=0)
        await worker.close()

    asyncio.run(scenario())


def test_worker_processes_reclaimed_pending_delivery_before_new_receive() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, reclaimed=(DELIVERY,))
        stream = ScriptedStream(trace, (TurnStreamCompleted("done"),))
        worker, _, _ = build_worker(trace=trace, queue=queue, stream=stream)

        await worker.start()
        await asyncio.wait_for(queue.acknowledged.wait(), timeout=1)
        await worker.drain(timeout=1)

        assert trace.index("queue.reclaim") < trace.index("postgres.resolve")
        assert queue.acked == [DELIVERY]
        await worker.close()

    asyncio.run(scenario())


def test_delivery_failure_stops_receive_and_fails_readiness() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, deliveries=(DELIVERY, DELIVERY))
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        worker, claims, _ = build_worker(trace=trace, queue=queue, stream=stream)
        claims.claim_error = DistributedBackendUnavailableError()

        await worker.start()
        await asyncio.wait_for(_wait_until_not_accepting(worker), timeout=1)

        assert queue.acked == []
        assert len(queue.deliveries) == 1
        with pytest.raises(DistributedBackendUnavailableError):
            await worker.close()
        assert worker.state.status == "closed"
        assert queue.closed

    asyncio.run(scenario())


def test_delivery_failure_does_not_start_delivery_returned_by_racing_receive() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        first = QueueDelivery("1-0", "outbox_1", 1)
        second = QueueDelivery("2-0", "outbox_2", 1)

        class RacingQueue(FakeQueue):
            def __init__(self) -> None:
                super().__init__(trace)
                self.receive_calls = 0
                self.second_receive_started = asyncio.Event()
                self.release_second_receive = asyncio.Event()

            async def receive(
                self,
                *,
                topic: str,
                consumer_id: str,
                limit: int,
            ) -> tuple[QueueDelivery, ...]:
                self.receive_calls += 1
                if self.receive_calls == 1:
                    return (first,)
                if self.receive_calls == 2:
                    self.second_receive_started.set()
                    await self.release_second_receive.wait()
                    return (second,)
                await asyncio.Event().wait()
                return ()

        class RacingRunner:
            claim_ttl = timedelta(minutes=1)

            def __init__(self, queue: RacingQueue) -> None:
                self.queue = queue
                self.started: list[QueueDelivery] = []

            async def run_delivery(self, delivery: QueueDelivery) -> bool:
                self.started.append(delivery)
                if delivery == first:
                    await self.queue.second_receive_started.wait()
                    self.queue.release_second_receive.set()
                    raise DistributedBackendUnavailableError()
                await asyncio.Event().wait()
                return False

        queue = RacingQueue()
        runner = RacingRunner(queue)
        worker = DistributedWorker(
            runner=runner,  # type: ignore[arg-type]
            queue=queue,
            worker_id="worker_1",
            topic="runs",
            max_concurrency=2,
            clock=lambda: NOW,
        )

        await worker.start()
        await asyncio.wait_for(_wait_until_not_accepting(worker), timeout=1)
        await asyncio.sleep(0)

        assert runner.started == [first]
        assert worker.state.active_claim_count == 0
        with pytest.raises(DistributedBackendUnavailableError):
            await worker.close()
        assert worker.state.status == "closed"

    asyncio.run(scenario())


async def _wait_until_not_accepting(worker: DistributedWorker) -> None:
    while worker.state.accepting_claims:
        await asyncio.sleep(0)


async def _wait_until_heartbeat(
    worker: DistributedWorker,
    expected: datetime,
) -> None:
    while worker.state.last_heartbeat_at != expected:
        await asyncio.sleep(0)
