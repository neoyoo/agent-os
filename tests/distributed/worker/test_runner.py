from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.worker.runner import WorkerRunner
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import RestoreAcceptedTurn, RunExecutionCursor
from agentos.runtime.side_effect_resolution import side_effect_resolution_to_payload
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)
from agentos.runtime.stream_events import (
    StatusUpdate,
    TurnStreamCompleted,
    TurnStreamWaiting,
)

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
    delivery_target,
)


def build_runner(
    *,
    trace: list[str],
    claims: FakeClaims,
    queue: FakeQueue,
    leases: FakeLeases,
    stream: ScriptedStream,
    heartbeat_wait=None,
) -> tuple[WorkerRunner, FakeAgentFactory, FakeEventSink]:
    agent = FakeAgent(trace, stream)
    factory = FakeAgentFactory(trace, agent)
    events = FakeEventSink(trace)
    runner = WorkerRunner(
        claims=claims,
        queue=queue,
        leases=leases,
        agent_factory=factory,
        event_sink=events,
        worker_id="worker_1",
        topic="runs",
        claim_ttl=timedelta(minutes=1),
        lease_ttl=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=10),
        heartbeat_wait=heartbeat_wait or asyncio.sleep,
    )
    return runner, factory, events


def test_runner_waits_for_authoritative_terminal_before_ack() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        terminal_gate = asyncio.Event()
        stream = ScriptedStream(
            trace,
            (
                StatusUpdate("provider", "working"),
                TurnStreamCompleted("done"),
            ),
            terminal_gate=terminal_gate,
        )
        runner, factory, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        task = asyncio.create_task(runner.run_delivery(DELIVERY))
        await events.appended.wait()
        assert queue.acked == []

        terminal_gate.set()
        assert await task is True
        assert queue.acked == [DELIVERY]
        assert factory.claimed == [claimed]
        assert factory.agent.inputs == [claimed.execution]
        assert [event.event_kind for event in events.events] == [
            "status_update",
            "turn_completed",
        ]
        assert trace.index("run_driver.commit") < trace.index("queue.ack")
        assert "postgres.release" not in trace

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled", "waiting"])
def test_terminal_or_waiting_duplicate_is_acked_without_claim(terminal_status: str) -> None:
    async def scenario() -> None:
        from agentos._waiting import WaitReason
        from agentos.runtime.run_state import RunState, RunStatus

        trace: list[str] = []
        status = RunStatus(terminal_status)
        if status is RunStatus.WAITING:
            target = delivery_target()
            target = type(target)(
                scope=target.scope,
                outbox_id=target.outbox_id,
                session_id=target.session_id,
                run=RunState(
                    run_id="run_1",
                    session_id="session_1",
                    status=RunStatus.WAITING,
                    wait_reason=WaitReason("human_input", "wait_1"),
                    aggregate_version=3,
                ),
            )
        else:
            target = delivery_target(status)
        claims = FakeClaims(trace, target=target)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        runner, factory, _ = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert queue.acked == [DELIVERY]
        assert factory.claimed == []
        assert "lease.acquire" not in trace
        assert "postgres.claim" not in trace

    asyncio.run(scenario())


def test_claim_loss_closes_stream_and_does_not_ack_or_release_claim() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        leases.renew_error = DistributedBackendUnavailableError()
        heartbeat = HeartbeatGate()
        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("unreachable"),),
            terminal_gate=asyncio.Event(),
        )
        runner, _, _ = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
            heartbeat_wait=heartbeat,
        )

        task = asyncio.create_task(runner.run_delivery(DELIVERY))
        await stream.started.wait()
        await heartbeat.waiting.wait()
        heartbeat.release.set()

        with pytest.raises(DistributedBackendUnavailableError):
            await task
        assert stream.closed
        assert stream.cleanup_finished.is_set()
        assert queue.acked == []
        assert claims.release_calls == 0

    asyncio.run(scenario())


def test_postgres_resolve_or_claim_failure_and_redis_lease_miss_fail_closed() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        target = delivery_target()
        claims = FakeClaims(trace, target=target)
        claims.resolve_error = DistributedBackendUnavailableError()
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        runner, factory, _ = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        with pytest.raises(DistributedBackendUnavailableError):
            await runner.run_delivery(DELIVERY)
        assert queue.acked == []

        claims.resolve_error = None
        claims.claim_error = DistributedBackendUnavailableError()
        with pytest.raises(DistributedBackendUnavailableError):
            await runner.run_delivery(DELIVERY)
        assert queue.acked == []

        claims.claim_error = None
        leases.available = False
        assert await runner.run_delivery(DELIVERY) is False
        assert queue.acked == []
        assert factory.claimed == []

    asyncio.run(scenario())


def test_unclaimable_nonterminal_delivery_is_not_acked() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        target = delivery_target()
        claims = FakeClaims(trace, target=target, claimed=None)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        runner, factory, _ = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is False
        assert queue.acked == []
        assert factory.claimed == []
        assert claims.resolve_calls == 2

    asyncio.run(scenario())


def test_side_effect_reconciliation_wait_is_published_before_ack() -> None:
    async def scenario() -> None:
        from agentos._waiting import WaitReason

        trace: list[str] = []
        claimed = claimed_execution(
            preparation=RestoreAcceptedTurn(
                RunExecutionCursor("turn_1", "before_provider", 0),
            ),
        )
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(
            trace,
            (
                TurnStreamWaiting(
                    run_id="run_1",
                    reason=WaitReason(
                        "side_effect_reconciliation",
                        "operation_0123456789abcdef0123456789abcdef",
                    ),
                ),
            ),
        )
        runner, _, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert [event.event_kind for event in events.events] == ["turn_waiting"]
        assert trace.index("run_driver.commit") < trace.index("queue.ack")

    asyncio.run(scenario())


def test_side_effect_recovery_is_passed_to_the_standard_agent_unchanged() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        resolution = SideEffectResolution(
            operation_id="operation_0123456789abcdef0123456789abcdef",
            kind=SideEffectResolutionKind.FAIL,
        )
        continuation = AcceptedContinuationInput(
            run_id="run_1",
            command_id="command_1",
            kind="resolve_side_effect",
            payload=side_effect_resolution_to_payload(resolution),
            turn_id="turn_2",
        )
        claimed = claimed_execution(
            preparation=RestoreAcceptedTurn(
                RunExecutionCursor("turn_2", "before_provider", 0),
            ),
            continuation=continuation,
        )
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("resolved"),))
        runner, factory, _ = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert factory.claimed == [claimed]
        assert factory.agent.inputs == [claimed.execution]
        assert queue.acked == [DELIVERY]

    asyncio.run(scenario())
