from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.distributed.errors import (
    DistributedBackendUnavailableError,
)
from agentos.distributed.run_event_limits import MAX_RUN_EVENT_JSON_BYTES
from agentos.distributed.worker.runner import WorkerRunner
from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import RestoreAcceptedTurn, RunExecutionCursor
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_resolution import side_effect_resolution_to_payload
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)
from agentos.runtime.stream_events import (
    FinalResult,
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
    committed_outcome,
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
    if stream.events and type(stream.events[-1]) is TurnStreamWaiting:
        waiting = stream.events[-1]
        pending = claims.pending_outcome
        if pending is not None:
            claims.pending_outcome = committed_outcome(
                RunStatus.WAITING,
                execution_attempt=pending.execution_attempt,
                committed_version=pending.committed_version,
                wait_reason=waiting.reason,
                turn_id=pending.turn_id,
            )
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


def test_terminal_commit_wins_over_claim_heartbeat_cleanup_race() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        claims.heartbeat_error = DistributedBackendUnavailableError()
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        terminal_committed = asyncio.Event()

        async def heartbeat_wait(delay: float) -> None:
            del delay
            await terminal_committed.wait()

        def commit_terminal() -> None:
            claims.outcome = committed_outcome(RunStatus.COMPLETED)
            claims.target = claims.outcome.target
            terminal_committed.set()

        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("done"),),
            terminal_return_gate=asyncio.Event(),
            on_terminal_commit=commit_terminal,
        )
        runner, _, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
            heartbeat_wait=heartbeat_wait,
        )

        task = asyncio.create_task(runner.run_delivery(DELIVERY))
        await asyncio.wait_for(claims.terminal_rechecked.wait(), timeout=1)
        assert await task is True
        assert stream.closed
        assert [event.event_kind for event in events.events] == ["turn_completed"]
        assert queue.acked == [DELIVERY]
        assert trace.index("run_driver.commit") < trace.index("postgres.heartbeat")
        assert trace.index("event.ensure_terminal") < trace.index("queue.ack")

    from agentos.runtime.run_state import RunStatus

    asyncio.run(scenario())


def test_terminal_recovery_does_not_ack_when_stream_cleanup_fails() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        claims.heartbeat_error = DistributedBackendUnavailableError()
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        terminal_committed = asyncio.Event()

        async def heartbeat_wait(delay: float) -> None:
            del delay
            await terminal_committed.wait()

        def commit_terminal() -> None:
            claims.outcome = committed_outcome(RunStatus.COMPLETED)
            claims.target = claims.outcome.target
            terminal_committed.set()

        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("done"),),
            terminal_return_gate=asyncio.Event(),
            on_terminal_commit=commit_terminal,
            close_error=RuntimeError("stream cleanup failed"),
        )
        runner, _, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
            heartbeat_wait=heartbeat_wait,
        )

        with pytest.raises(RuntimeError, match="stream cleanup failed"):
            await runner.run_delivery(DELIVERY)

        assert stream.cleanup_finished.is_set()
        assert events.events == []
        assert queue.acked == []
        assert "event.ensure_terminal" not in trace

    asyncio.run(scenario())


def test_terminal_recovery_observes_real_agent_stream_cleanup_failure() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        claims.heartbeat_error = DistributedBackendUnavailableError()
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        terminal_committed = asyncio.Event()

        async def heartbeat_wait(delay: float) -> None:
            del delay
            await terminal_committed.wait()

        async def events():
            claims.outcome = committed_outcome(RunStatus.COMPLETED)
            claims.target = claims.outcome.target
            terminal_committed.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted("unreachable")

        async def cleanup() -> None:
            trace.append("stream.cleanup")
            raise RuntimeError("stream cleanup failed")

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        factory = FakeAgentFactory(trace, FakeAgent(trace, stream))  # type: ignore[arg-type]
        event_sink = FakeEventSink(trace)
        runner = WorkerRunner(
            claims=claims,
            queue=queue,
            leases=leases,
            agent_factory=factory,
            event_sink=event_sink,
            worker_id="worker_1",
            topic="runs",
            claim_ttl=timedelta(minutes=1),
            lease_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
            heartbeat_wait=heartbeat_wait,
        )

        with pytest.raises(RuntimeError, match="stream cleanup failed"):
            await runner.run_delivery(DELIVERY)

        assert stream.closed
        assert event_sink.events == []
        assert queue.acked == []
        assert "event.ensure_terminal" not in trace

    asyncio.run(scenario())


def test_terminal_recovery_observes_agent_stream_cleanup_cancellation() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        claims.heartbeat_error = DistributedBackendUnavailableError()
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        terminal_committed = asyncio.Event()

        async def heartbeat_wait(delay: float) -> None:
            del delay
            await terminal_committed.wait()

        async def events():
            claims.outcome = committed_outcome(RunStatus.COMPLETED)
            claims.target = claims.outcome.target
            terminal_committed.set()
            await asyncio.Event().wait()
            yield TurnStreamCompleted("unreachable")

        async def cleanup() -> None:
            trace.append("stream.cleanup")
            raise asyncio.CancelledError("cleanup did not finish")

        stream = ExecutionLease().open_stream(events(), cleanup=cleanup)
        factory = FakeAgentFactory(trace, FakeAgent(trace, stream))  # type: ignore[arg-type]
        event_sink = FakeEventSink(trace)
        runner = WorkerRunner(
            claims=claims,
            queue=queue,
            leases=leases,
            agent_factory=factory,
            event_sink=event_sink,
            worker_id="worker_1",
            topic="runs",
            claim_ttl=timedelta(minutes=1),
            lease_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
            heartbeat_wait=heartbeat_wait,
        )

        with pytest.raises(RuntimeError, match="agent stream cleanup was cancelled"):
            await runner.run_delivery(DELIVERY)

        assert stream.closed
        assert event_sink.events == []
        assert queue.acked == []
        assert "event.ensure_terminal" not in trace

    asyncio.run(scenario())


def test_oversized_final_result_is_omitted_after_completed_commit() -> None:
    async def scenario() -> None:
        content = "x" * MAX_RUN_EVENT_JSON_BYTES
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(
            trace,
            (FinalResult(content), TurnStreamCompleted(content)),
        )
        runner, _, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert [event.event_kind for event in events.events] == ["turn_completed"]
        assert queue.acked == [DELIVERY]

    asyncio.run(scenario())


def test_oversized_preterminal_event_fails_with_protocol_error() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(
            trace,
            (
                StatusUpdate("provider", "x" * MAX_RUN_EVENT_JSON_BYTES),
                TurnStreamCompleted("unused"),
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
        assert [event.event_kind for event in events.events] == ["turn_failed"]
        assert queue.acked == [DELIVERY]
        assert trace.index("run_driver.commit_failed") < trace.index(
            "event.ensure_terminal",
        )
        assert trace.index("event.ensure_terminal") < trace.index("queue.ack")

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled", "waiting"])
def test_terminal_or_waiting_duplicate_is_acked_without_claim(terminal_status: str) -> None:
    async def scenario() -> None:
        from agentos._waiting import WaitReason
        from agentos.runtime.run_state import RunStatus

        trace: list[str] = []
        status = RunStatus(terminal_status)
        outcome = committed_outcome(
            status,
            wait_reason=(
                WaitReason("human_input", "wait_1")
                if status is RunStatus.WAITING
                else None
            ),
        )
        claims = FakeClaims(trace, target=outcome.target)
        claims.outcome = outcome
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        runner, factory, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert queue.acked == [DELIVERY]
        assert factory.claimed == []
        assert "lease.acquire" in trace
        assert "lease.ensure_owned" in trace
        assert "postgres.claim" not in trace
        assert [event.event_kind for event in events.events] == [
            f"turn_{terminal_status}" if status is not RunStatus.COMPLETED else "turn_completed"
        ]
        assert events.events[0].event_sequence == 9_007_199_254_740_991
        assert trace.index("event.ensure_terminal") < trace.index("queue.ack")

    asyncio.run(scenario())


def test_terminal_recovery_failure_is_not_acked_and_retry_is_idempotent() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        outcome = committed_outcome()
        claims = FakeClaims(trace, target=outcome.target)
        claims.outcome = outcome
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        runner, factory, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )
        events.ensure_error = DistributedBackendUnavailableError()

        with pytest.raises(DistributedBackendUnavailableError):
            await runner.run_delivery(DELIVERY)
        assert queue.acked == []
        assert factory.claimed == []

        events.ensure_error = None
        assert await runner.run_delivery(DELIVERY) is True
        assert [event.event_kind for event in events.events] == ["turn_completed"]
        assert queue.acked == [DELIVERY]
        assert factory.claimed == []

    asyncio.run(scenario())


def test_superseded_delivery_is_acked_without_claiming_new_input() -> None:
    async def scenario() -> None:
        trace: list[str] = []
        outcome = committed_outcome(
            RunStatus.COMPLETED,
            committed_version=4,
            current_version=5,
        )
        current = delivery_target(RunStatus.RUNNING, aggregate_version=5)
        outcome = type(outcome)(
            target=current,
            turn_id=outcome.turn_id,
            execution_attempt=outcome.execution_attempt,
            committed_version=outcome.committed_version,
            committed_at=outcome.committed_at,
        )
        claims = FakeClaims(trace, target=current, claimed=claimed_execution())
        claims.outcome = outcome
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        stream = ScriptedStream(trace, (TurnStreamCompleted("unused"),))
        runner, factory, events = build_runner(
            trace=trace,
            claims=claims,
            queue=queue,
            leases=leases,
            stream=stream,
        )

        assert await runner.run_delivery(DELIVERY) is True
        assert queue.acked == [DELIVERY]
        assert factory.claimed == []
        assert events.events == []
        assert "lease.acquire" not in trace
        assert "postgres.claim" not in trace

    from agentos.runtime.run_state import RunStatus

    asyncio.run(scenario())


def test_external_cancel_closes_old_attempt_and_publishes_new_fence() -> None:
    async def scenario() -> None:
        from agentos.runtime.run_state import RunStatus

        trace: list[str] = []
        claimed = claimed_execution()
        claims = FakeClaims(trace, target=claimed.target, claimed=claimed)
        queue = FakeQueue(trace)
        leases = FakeLeases(trace)
        leases.renew_error = DistributedBackendUnavailableError()
        heartbeat = HeartbeatGate()
        terminal_gate = asyncio.Event()
        stream = ScriptedStream(
            trace,
            (TurnStreamCompleted("stale"),),
            terminal_gate=terminal_gate,
        )
        runner, factory, events = build_runner(
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
        claims.outcome = committed_outcome(
            RunStatus.CANCELLED,
            execution_attempt=8,
        )
        claims.target = claims.outcome.target
        heartbeat.release.set()

        assert await asyncio.wait_for(task, timeout=1) is True
        assert stream.closed
        assert factory.claimed == [claimed]
        assert [event.event_kind for event in events.events] == ["turn_cancelled"]
        assert events.events[0].execution_attempt == 8
        assert events.events[0].event_sequence == 9_007_199_254_740_991
        assert queue.acked == [DELIVERY]
        assert "run_driver.commit" not in trace

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
        assert claims.resolve_outcome_calls == 2

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
