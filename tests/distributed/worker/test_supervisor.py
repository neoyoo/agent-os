from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import DeliveryUnavailableError
from agentos.distributed.worker.runner import WorkerRunner
from agentos.distributed.worker.supervisor import DistributedWorker
from agentos.runtime.stream_events import TurnStreamCompleted

from tests.distributed.worker._fakes import (
    DELIVERY,
    FakeAgent,
    FakeAgentFactory,
    FakeClaims,
    FakeEventSink,
    FakeLeases,
    FakeQueue,
    HeartbeatGate,
    NOW,
    ScriptedStream,
    claimed_execution,
)


def build_worker(
    *,
    trace: list[str],
    queue: FakeQueue,
    stream: ScriptedStream,
    heartbeat_wait=asyncio.sleep,
) -> tuple[DistributedWorker, FakeClaims, FakeLeases]:
    claimed = claimed_execution()
    claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
    leases = FakeLeases(trace)
    runner = WorkerRunner(
        claims=claims,
        queue=queue,
        leases=leases,
        agent_factory=FakeAgentFactory(trace, FakeAgent(trace, stream)),
        event_sink=FakeEventSink(trace),
        worker_id="worker_1",
        topic="runs",
        claim_ttl=timedelta(minutes=1),
        lease_ttl=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=10),
        heartbeat_wait=heartbeat_wait,
    )
    return (
        DistributedWorker(
            runner=runner,
            queue=queue,
            worker_id="worker_1",
            topic="runs",
            max_concurrency=1,
            clock=lambda: NOW,
        ),
        claims,
        leases,
    )


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
            "event.append",
            "stream.aclose",
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


def test_drain_timeout_closes_stream_and_leaves_claim_to_expire() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        queue = FakeQueue(trace, (DELIVERY,))
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
