from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from agentos.distributed.models import (
    ClaimedExecution,
    ExecutionClaim,
    OutboxClaim,
    OutboxRecord,
    QueueDelivery,
    ReplayItem,
    RequestScope,
    RunDeliveryTarget,
    RunEventEnvelope,
    SessionLease,
)
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.execution import AcceptedStartInput, AcceptedTurnExecution
from agentos.runtime.run import UserTurnInput
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.stream_events import TurnStreamEvent


NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
SCOPE = RequestScope("tenant_1", "principal_1")
DELIVERY = QueueDelivery("1-0", "outbox_1", 1)


def delivery_target(status: RunStatus = RunStatus.QUEUED) -> RunDeliveryTarget:
    return RunDeliveryTarget(
        scope=SCOPE,
        outbox_id=DELIVERY.outbox_id,
        session_id="session_1",
        run=RunState(
            run_id="run_1",
            session_id="session_1",
            status=status,
            aggregate_version=3,
        ),
    )


def claimed_execution(
    *,
    mode: str = "start",
    continuation: AcceptedContinuationInput | None = None,
) -> ClaimedExecution:
    target = delivery_target(
        RunStatus.QUEUED if mode == "start" else RunStatus.RUNNING,
    )
    claim = ExecutionClaim(
        tenant_id=SCOPE.tenant_id,
        session_id=target.session_id,
        run_id=target.run.run_id,
        owner_id="worker_1",
        claim_id="claim_1",
        fencing_token=7,
        expires_at=NOW + timedelta(minutes=1),
    )
    accepted = continuation or AcceptedStartInput(
        run_id=target.run.run_id,
        submission_id="submission_1",
        input=UserTurnInput("hello"),
        turn_id="turn_1",
        user_message_id="message_1",
    )
    execution = AcceptedTurnExecution(
        input=accepted,
        guard=RunWriteGuard(
            expected_version=target.run.aggregate_version,
            claim_id=claim.claim_id,
            fencing_token=claim.fencing_token,
        ),
        mode=mode,  # type: ignore[arg-type]
    )
    return ClaimedExecution(target, claim, execution)


class FakeQueue:
    def __init__(
        self,
        trace: list[str],
        deliveries: tuple[QueueDelivery, ...] = (),
        reclaimed: tuple[QueueDelivery, ...] = (),
    ) -> None:
        self.trace = trace
        self.deliveries = list(deliveries)
        self.reclaimed = list(reclaimed)
        self.acked: list[QueueDelivery] = []
        self.acknowledged = asyncio.Event()
        self.receive_started = asyncio.Event()
        self.receive_cancelled = asyncio.Event()
        self.receive_error: BaseException | None = None
        self.closed = False
        self.published: list[OutboxRecord] = []
        self.publish_error: BaseException | None = None

    async def publish(self, *, record: OutboxRecord) -> str:
        self.trace.append("queue.publish")
        self.published.append(record)
        if self.publish_error is not None:
            raise self.publish_error
        return f"entry-{len(self.published)}"

    async def receive(
        self,
        *,
        topic: str,
        consumer_id: str,
        limit: int,
    ) -> tuple[QueueDelivery, ...]:
        self.trace.append("queue.receive")
        self.receive_started.set()
        if self.receive_error is not None:
            raise self.receive_error
        if self.deliveries:
            batch = tuple(self.deliveries[:limit])
            del self.deliveries[:limit]
            return batch
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.receive_cancelled.set()
            raise
        return ()

    async def reclaim(
        self,
        *,
        topic: str,
        consumer_id: str,
        min_idle: timedelta,
        limit: int,
    ) -> tuple[QueueDelivery, ...]:
        self.trace.append("queue.reclaim")
        batch = tuple(self.reclaimed[:limit])
        del self.reclaimed[:limit]
        return batch

    async def ack(
        self,
        *,
        topic: str,
        delivery: QueueDelivery,
    ) -> None:
        self.trace.append("queue.ack")
        self.acked.append(delivery)
        self.acknowledged.set()

    async def close(self) -> None:
        self.trace.append("queue.close")
        self.closed = True


class FakeClaims:
    def __init__(
        self,
        trace: list[str],
        *,
        target: RunDeliveryTarget | None,
        claimed: ClaimedExecution | None = None,
    ) -> None:
        self.trace = trace
        self.target = target
        self.claimed = claimed
        self.target_after_claim: RunDeliveryTarget | None = None
        self.resolve_error: BaseException | None = None
        self.resolve_calls = 0
        self.claim_error: BaseException | None = None
        self.heartbeat_error: BaseException | None = None
        self.release_calls = 0

    async def resolve_delivery(self, *, outbox_id: str) -> RunDeliveryTarget | None:
        self.trace.append("postgres.resolve")
        self.resolve_calls += 1
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.target

    async def claim_pending_turn(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> ClaimedExecution | None:
        self.trace.append("postgres.claim")
        if self.claim_error is not None:
            raise self.claim_error
        if self.target_after_claim is not None:
            self.target = self.target_after_claim
        return self.claimed

    async def heartbeat(
        self,
        *,
        scope: RequestScope,
        claim: ExecutionClaim,
        ttl: timedelta,
    ) -> ExecutionClaim:
        self.trace.append("postgres.heartbeat")
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        return claim

    async def release(
        self,
        *,
        scope: RequestScope,
        claim: ExecutionClaim,
    ) -> None:
        self.trace.append("postgres.release")
        self.release_calls += 1

    async def recover_expired(
        self,
        *,
        scope: RequestScope,
        limit: int,
    ) -> tuple[RunDeliveryTarget, ...]:
        return ()


class FakeLeases:
    def __init__(self, trace: list[str], *, available: bool = True) -> None:
        self.trace = trace
        self.available = available
        self.renew_error: BaseException | None = None
        self.release_calls = 0

    async def acquire(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> SessionLease | None:
        self.trace.append("lease.acquire")
        if not self.available:
            return None
        return SessionLease(
            scope=scope,
            session_id=session_id,
            owner_id=owner_id,
            lease_id="lease_1",
            expires_at=NOW + ttl,
        )

    async def renew(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
        ttl: timedelta,
    ) -> SessionLease:
        self.trace.append("lease.renew")
        if self.renew_error is not None:
            raise self.renew_error
        return lease

    async def ensure_owned(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None:
        self.trace.append("lease.ensure_owned")

    async def release(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None:
        self.trace.append("lease.release")
        self.release_calls += 1


class FakeEventSink:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.events: list[RunEventEnvelope] = []
        self.appended = asyncio.Event()

    async def append(
        self,
        *,
        scope: RequestScope,
        event: RunEventEnvelope,
    ) -> ReplayItem:
        self.trace.append("event.append")
        self.events.append(event)
        self.appended.set()
        return ReplayItem(f"cursor_{len(self.events)}", event)


class ScriptedStream:
    def __init__(
        self,
        trace: list[str],
        events: tuple[TurnStreamEvent, ...],
        *,
        terminal_gate: asyncio.Event | None = None,
    ) -> None:
        self.trace = trace
        self.events = events
        self.terminal_gate = terminal_gate
        self.started = asyncio.Event()
        self.cleanup_finished = asyncio.Event()
        self.closed = False
        self._index = 0

    def __aiter__(self) -> ScriptedStream:
        return self

    async def __anext__(self) -> TurnStreamEvent:
        self.started.set()
        if self._index >= len(self.events):
            raise StopAsyncIteration
        event = self.events[self._index]
        if self._index == len(self.events) - 1:
            if self.terminal_gate is not None:
                await self.terminal_gate.wait()
            self.trace.append("run_driver.commit")
        self._index += 1
        return event

    async def __aenter__(self) -> ScriptedStream:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self.closed:
            self.trace.append("stream.aclose")
            self.closed = True
            self.cleanup_finished.set()


class FakeAgent:
    def __init__(self, trace: list[str], stream: ScriptedStream) -> None:
        self.trace = trace
        self.stream = stream
        self.inputs: list[AcceptedTurnExecution] = []

    async def run(
        self,
        input: AcceptedTurnExecution,
        *,
        stream: bool,
    ) -> ScriptedStream:
        self.trace.append(f"agent.run:{stream}")
        self.inputs.append(input)
        return self.stream


class FakeAgentFactory:
    def __init__(self, trace: list[str], agent: FakeAgent) -> None:
        self.trace = trace
        self.agent = agent
        self.claimed: list[ClaimedExecution] = []
        self.hydrate_started = asyncio.Event()
        self.hydrate_cancelled = asyncio.Event()
        self.hydrate_gate: asyncio.Event | None = None

    async def hydrate(self, *, claimed: ClaimedExecution) -> FakeAgent:
        self.trace.append("agent.hydrate")
        self.claimed.append(claimed)
        self.hydrate_started.set()
        if self.hydrate_gate is not None:
            try:
                await self.hydrate_gate.wait()
            except asyncio.CancelledError:
                self.hydrate_cancelled.set()
                raise
        return self.agent


class HeartbeatGate:
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        self.waiting.set()
        await self.release.wait()
        self.release.clear()


class FakeOutbox:
    def __init__(self, trace: list[str], claim: OutboxClaim) -> None:
        self.trace = trace
        self.claim = claim
        self.mark_error: BaseException | None = None
        self.claim_calls = 0
        self.marked: list[tuple[OutboxClaim, str]] = []
        self.released: list[OutboxClaim] = []

    async def claim_batch(
        self,
        *,
        owner_id: str,
        limit: int,
        ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]:
        self.trace.append("outbox.claim")
        self.claim_calls += 1
        return (self.claim,) if not self.marked else ()

    async def mark_published(
        self,
        *,
        claim: OutboxClaim,
        queue_entry_id: str,
    ) -> None:
        self.trace.append("outbox.mark")
        if self.mark_error is not None:
            error, self.mark_error = self.mark_error, None
            raise error
        self.marked.append((claim, queue_entry_id))

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        self.trace.append("outbox.release")
        self.released.append(claim)


def outbox_claim() -> OutboxClaim:
    record = OutboxRecord(
        scope=SCOPE,
        outbox_id=DELIVERY.outbox_id,
        topic="runs",
        payload={"version": 1},
        created_at=NOW,
    )
    return OutboxClaim(
        record=record,
        owner_id="relay_1",
        claim_id="relay_claim_1",
        expires_at=NOW + timedelta(minutes=1),
    )
