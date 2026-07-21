from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.worker.runner import WorkerRunner
from agentos.runtime.run_state import RunStatus
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
    ScriptedStream,
    claimed_execution,
    committed_outcome,
    delivery_target,
)


def _runner(
    *,
    trace: list[str],
    claims: FakeClaims,
    leases: FakeLeases,
    factory: FakeAgentFactory,
    heartbeat_wait=asyncio.sleep,
    queue: FakeQueue | None = None,
    claim_ttl: timedelta = timedelta(minutes=1),
    lease_ttl: timedelta = timedelta(seconds=30),
    heartbeat_interval: timedelta = timedelta(seconds=10),
) -> WorkerRunner:
    return WorkerRunner(
        claims=claims,
        queue=queue if queue is not None else FakeQueue(trace),
        leases=leases,
        agent_factory=factory,
        event_sink=FakeEventSink(trace),
        worker_id="worker_1",
        topic="runs",
        claim_ttl=claim_ttl,
        lease_ttl=lease_ttl,
        heartbeat_interval=heartbeat_interval,
        heartbeat_wait=heartbeat_wait,
    )


@pytest.mark.parametrize("limited_ttl", ["claim", "lease"])
def test_heartbeat_interval_must_be_less_than_each_ttl(limited_ttl: str) -> None:
    trace: list[str] = []
    claimed = claimed_execution()
    claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
    leases = FakeLeases(trace)
    stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
    factory = FakeAgentFactory(trace, FakeAgent(trace, stream))
    claim_ttl = timedelta(seconds=10 if limited_ttl == "claim" else 20)
    lease_ttl = timedelta(seconds=10 if limited_ttl == "lease" else 20)

    with pytest.raises(ValueError, match="heartbeat_interval"):
        _runner(
            trace=trace,
            claims=claims,
            leases=leases,
            factory=factory,
            claim_ttl=claim_ttl,
            lease_ttl=lease_ttl,
            heartbeat_interval=timedelta(seconds=10),
        )


def test_claim_heartbeat_failure_cancels_in_progress_hydration() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        claims.heartbeat_error = DistributedBackendUnavailableError()
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        factory = FakeAgentFactory(trace, FakeAgent(trace, stream))
        factory.hydrate_gate = asyncio.Event()
        heartbeat = HeartbeatGate()
        runner = _runner(
            trace=trace,
            claims=claims,
            leases=leases,
            factory=factory,
            heartbeat_wait=heartbeat,
        )

        task = asyncio.create_task(runner.run_delivery(DELIVERY))
        await factory.hydrate_started.wait()
        await asyncio.wait_for(heartbeat.waiting.wait(), timeout=1)
        heartbeat.release.set()

        with pytest.raises(DistributedBackendUnavailableError):
            await task
        assert factory.hydrate_cancelled.is_set()
        assert "agent.run:True" not in trace
        assert "queue.ack" not in trace
        assert trace.index("lease.renew") < trace.index("postgres.heartbeat")

    asyncio.run(scenario())


def test_unclaimable_delivery_is_acked_after_terminal_truth_refresh() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        target = delivery_target()
        claims = FakeClaims(trace, target=target, claimed=None)
        claims.outcome_after_claim = committed_outcome(RunStatus.COMPLETED)
        leases = FakeLeases(trace)
        queue = FakeQueue(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        factory = FakeAgentFactory(trace, FakeAgent(trace, stream))
        runner = _runner(
            trace=trace,
            claims=claims,
            leases=leases,
            factory=factory,
            queue=queue,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert queue.acked == [DELIVERY]
        assert claims.resolve_calls == 1
        assert claims.resolve_outcome_calls == 2
        assert factory.claimed == []
        assert trace.index("event.ensure_terminal") < trace.index("queue.ack")

    asyncio.run(scenario())
