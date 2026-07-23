from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from agentos.distributed.worker.runner import WorkerRunner
from agentos.distributed.worker.supervisor import DistributedWorker

from tests.distributed.worker._fakes import (
    FakeAgent,
    FakeAgentFactory,
    FakeClaims,
    FakeEventSink,
    FakeLeases,
    FakeQueue,
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
    heartbeat_interval: timedelta = timedelta(seconds=10),
    clock: Callable[[], datetime] = lambda: NOW,
    shutdown_cleanup_timeout: timedelta = timedelta(seconds=5),
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
        heartbeat_interval=heartbeat_interval,
        heartbeat_wait=heartbeat_wait,
    )
    return (
        DistributedWorker(
            runner=runner,
            queue=queue,
            worker_id="worker_1",
            topic="runs",
            max_concurrency=1,
            clock=clock,
            shutdown_cleanup_timeout=shutdown_cleanup_timeout,
        ),
        claims,
        leases,
    )
