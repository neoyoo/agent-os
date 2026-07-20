from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from agentos.distributed.models import (
    ClaimedExecution,
    QueueDelivery,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    SessionLease,
    project_live_event,
)
from agentos.distributed.protocols import ExecutionClaimPort, LeasePort, QueuePort
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.execution import AcceptedTurnExecution
from agentos.runtime.run_state import RunStatus
from agentos.runtime.stream_events import (
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamFailed,
    TurnStreamWaiting,
)


HeartbeatWait = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]
_TERMINAL_EVENTS = (
    TurnStreamCompleted,
    TurnStreamWaiting,
    TurnStreamFailed,
    TurnStreamCancelled,
)
_EXECUTABLE_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})
_ACKABLE_STATUSES = frozenset(
    {RunStatus.WAITING, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
)


class WorkerAgent(Protocol):
    """Worker 调用标准 Agent 所需的最小异步边界。"""

    async def run(
        self,
        input: AcceptedTurnExecution,
        *,
        stream: Literal[True],
    ) -> AgentStream: ...


class WorkerAgentFactory(Protocol):
    """从 PostgreSQL 权威状态水合一次 claim-scoped Agent。"""

    async def hydrate(self, *, claimed: ClaimedExecution) -> WorkerAgent: ...


class WorkerEventSink(Protocol):
    """Worker 写入 live replay 所需的窄 EventReplayPort 子集。"""

    async def append(
        self,
        *,
        scope: RequestScope,
        event: RunEventEnvelope,
    ) -> ReplayItem: ...


@dataclass(frozen=True, slots=True)
class WorkerRunner:
    """执行一个 delivery，不拥有 Run 状态机或 terminal commit。"""

    claims: ExecutionClaimPort
    queue: QueuePort
    leases: LeasePort
    agent_factory: WorkerAgentFactory
    event_sink: WorkerEventSink
    worker_id: str
    topic: str
    claim_ttl: timedelta
    lease_ttl: timedelta
    heartbeat_interval: timedelta
    heartbeat_wait: HeartbeatWait = asyncio.sleep
    clock: Clock = lambda: datetime.now(UTC)

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if not self.topic.strip():
            raise ValueError("topic must not be empty")
        for value, name in (
            (self.claim_ttl, "claim_ttl"),
            (self.lease_ttl, "lease_ttl"),
            (self.heartbeat_interval, "heartbeat_interval"),
        ):
            if value <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.heartbeat_interval >= min(self.claim_ttl, self.lease_ttl):
            raise ValueError("heartbeat_interval must be less than claim and lease TTL")

    async def run_delivery(self, delivery: QueueDelivery) -> bool:
        """执行或去重一个 delivery；返回是否已成功 ACK。"""

        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        target = await self.claims.resolve_delivery(outbox_id=delivery.outbox_id)
        if target is None:
            return False
        if target.run.status in _ACKABLE_STATUSES:
            await self._ack(delivery)
            return True
        if target.run.status not in _EXECUTABLE_STATUSES:
            return False

        lease = await self.leases.acquire(
            scope=target.scope,
            session_id=target.session_id,
            owner_id=self.worker_id,
            ttl=self.lease_ttl,
        )
        if lease is None:
            return False
        if (
            lease.scope != target.scope
            or lease.session_id != target.session_id
            or lease.owner_id != self.worker_id
        ):
            raise RuntimeError("lease does not match the delivery target")
        try:
            claimed = await self.claims.claim_pending_turn(
                scope=target.scope,
                outbox_id=delivery.outbox_id,
                owner_id=self.worker_id,
                ttl=self.claim_ttl,
            )
            if claimed is None:
                refreshed = await self.claims.resolve_delivery(
                    outbox_id=delivery.outbox_id,
                )
                if refreshed is None or refreshed.run.status not in _ACKABLE_STATUSES:
                    return False
                await self._ack(delivery)
                return True
            if claimed.target != target:
                raise RuntimeError("claim does not match the resolved delivery target")
            await self.leases.ensure_owned(scope=target.scope, lease=lease)
            await self._run_claimed(
                claimed=claimed,
                lease=lease,
            )
            await self._ack(delivery)
            return True
        finally:
            await self.leases.release(scope=target.scope, lease=lease)

    async def _run_claimed(
        self,
        *,
        claimed: ClaimedExecution,
        lease: SessionLease,
    ) -> None:
        heartbeat = asyncio.create_task(
            self._heartbeat(claimed, lease),
        )
        execution = asyncio.create_task(self._hydrate_and_consume(claimed))
        try:
            done, _ = await asyncio.wait(
                (execution, heartbeat),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                await heartbeat
                raise RuntimeError("claim heartbeat stopped before execution")
            await execution
        except BaseException:
            await _cancel_and_wait(execution)
            raise
        finally:
            await _cancel_and_wait(heartbeat)

    async def _hydrate_and_consume(self, claimed: ClaimedExecution) -> None:
        stream: AgentStream | None = None
        try:
            agent = await self.agent_factory.hydrate(claimed=claimed)
            stream = await agent.run(claimed.execution, stream=True)
            await self._consume_events(claimed, stream)
        except BaseException:
            if stream is not None:
                await stream.aclose()
            raise

    async def _consume_events(
        self,
        claimed: ClaimedExecution,
        stream: AgentStream,
    ) -> None:
        sequence = 0
        terminal_seen = False
        async with stream:
            async for event in stream:
                projected = project_live_event(event)
                if projected is not None:
                    envelope = RunEventEnvelope(
                        tenant_id=claimed.target.scope.tenant_id,
                        session_id=claimed.target.session_id,
                        run_id=claimed.target.run.run_id,
                        turn_id=claimed.execution.input.turn_id,
                        execution_attempt=claimed.claim.fencing_token,
                        event_sequence=sequence,
                        event=projected,
                        occurred_at=self.clock(),
                    )
                    await self.event_sink.append(
                        scope=claimed.target.scope,
                        event=envelope,
                    )
                    sequence += 1
                if isinstance(event, _TERMINAL_EVENTS):
                    terminal_seen = True
                    break
        if not terminal_seen:
            raise RuntimeError("agent stream ended without an authoritative outcome")

    async def _heartbeat(
        self,
        claimed: ClaimedExecution,
        lease: SessionLease,
    ) -> None:
        current_lease = lease
        current_claim = claimed.claim
        while True:
            await self.heartbeat_wait(self.heartbeat_interval.total_seconds())
            current_lease = await self.leases.renew(
                scope=claimed.target.scope,
                lease=current_lease,
                ttl=self.lease_ttl,
            )
            current_claim = await self.claims.heartbeat(
                scope=claimed.target.scope,
                claim=current_claim,
                ttl=self.claim_ttl,
            )

    async def _ack(self, delivery: QueueDelivery) -> None:
        await self.queue.ack(topic=self.topic, delivery=delivery)


async def _cancel_and_wait(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "WorkerAgent",
    "WorkerAgentFactory",
    "WorkerEventSink",
    "WorkerRunner",
]
