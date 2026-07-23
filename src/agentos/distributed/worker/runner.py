from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator, Literal, Protocol

from agentos.distributed._execution_outcomes import CommittedExecutionOutcome
from agentos.distributed.models import (
    ClaimedExecution,
    LiveFinalResult,
    LiveRunEvent,
    QueueDelivery,
    ReplayItem,
    RequestScope,
    RunEventEnvelope,
    SessionLease,
    project_live_event,
)
from agentos.distributed.errors import (
    DeliveryUnavailableError,
    DistributedBackendUnavailableError,
    RunEventTooLargeError,
)
from agentos.distributed.protocols import ExecutionClaimPort, LeasePort, QueuePort
from agentos.distributed.run_event_limits import require_run_event_size
from agentos.distributed.worker._terminal_publication import (
    terminal_envelope,
    terminal_event,
)
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime._agent_stream_cleanup import raise_stream_cleanup_failure
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

    async def ensure_terminal(
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
    heartbeat_cycle_timeout: timedelta = timedelta(seconds=10)
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
            (self.heartbeat_cycle_timeout, "heartbeat_cycle_timeout"),
        ):
            if value <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.heartbeat_interval >= min(self.claim_ttl, self.lease_ttl):
            raise ValueError("heartbeat_interval must be less than claim and lease TTL")
        if self.heartbeat_interval + self.heartbeat_cycle_timeout >= min(
            self.claim_ttl,
            self.lease_ttl,
        ):
            raise ValueError("heartbeat cycle must complete before claim and lease TTL")

    async def run_delivery(self, delivery: QueueDelivery) -> bool:
        """执行或去重一个 delivery；返回是否已成功 ACK。"""

        if type(delivery) is not QueueDelivery:
            raise TypeError("delivery must be QueueDelivery")
        target = await self.claims.resolve_delivery(outbox_id=delivery.outbox_id)
        if target is None:
            return False
        committed = await self.claims.resolve_committed_outcome(
            outbox_id=delivery.outbox_id,
        )
        if committed is not None:
            if not committed.is_current:
                await self._ack(delivery)
                return True
            return await self._recover_committed_delivery(delivery, committed)
        if target.run.status in _ACKABLE_STATUSES:
            return False
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
        async with _lease_scope(
            leases=self.leases,
            scope=target.scope,
            lease=lease,
        ):
            claimed = await self.claims.claim_pending_turn(
                scope=target.scope,
                outbox_id=delivery.outbox_id,
                owner_id=self.worker_id,
                ttl=self.claim_ttl,
            )
            if claimed is None:
                committed = await self.claims.resolve_committed_outcome(
                    outbox_id=delivery.outbox_id,
                )
                if committed is None:
                    return False
                if committed.is_current:
                    await self.leases.ensure_owned(
                        scope=committed.target.scope,
                        lease=lease,
                    )
                    await self._ensure_committed_outcome(committed)
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
    async def _recover_committed_delivery(
        self,
        delivery: QueueDelivery,
        outcome: CommittedExecutionOutcome,
    ) -> bool:
        target = outcome.target
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
            raise RuntimeError("lease does not match the committed outcome")
        async with _lease_scope(
            leases=self.leases,
            scope=target.scope,
            lease=lease,
        ):
            await self.leases.ensure_owned(scope=target.scope, lease=lease)
            await self._ensure_committed_outcome(outcome)
            await self._ack(delivery)
            return True
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
                try:
                    await heartbeat
                except BaseException as heartbeat_error:
                    cleanup_error = await _cancel_and_wait_result(execution)
                    if cleanup_error is not None:
                        raise cleanup_error
                    committed = await self.claims.resolve_committed_outcome(
                        outbox_id=claimed.target.outbox_id,
                    )
                    if committed is None:
                        raise heartbeat_error
                    if committed.is_current:
                        await self.leases.ensure_owned(
                            scope=committed.target.scope,
                            lease=lease,
                        )
                        await self._ensure_committed_outcome(committed)
                    return
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
            async with stream:
                committed = await self._consume_events(claimed, stream)
            await self._ensure_committed_outcome(committed)
        except BaseException:
            if stream is not None:
                await stream.aclose()
                if isinstance(stream, AgentStream):
                    raise_stream_cleanup_failure(stream)
            raise

    async def _consume_events(
        self,
        claimed: ClaimedExecution,
        stream: AgentStream,
    ) -> CommittedExecutionOutcome:
        sequence = 0
        async for event in stream:
            projected = project_live_event(event)
            if projected is not None and not isinstance(event, _TERMINAL_EVENTS):
                envelope = self._event_envelope(claimed, sequence, projected)
                try:
                    require_run_event_size(envelope)
                except RunEventTooLargeError as error:
                    if type(projected) is LiveFinalResult:
                        continue
                    event = await stream._fail_active(error)
                    projected = project_live_event(event)
                    if projected is None:
                        raise RuntimeError("run failure event is not observable")
                else:
                    await self.event_sink.append(
                        scope=claimed.target.scope,
                        event=envelope,
                    )
                    sequence += 1
                    continue
            if isinstance(event, _TERMINAL_EVENTS):
                committed = await self.claims.resolve_committed_outcome(
                    outbox_id=claimed.target.outbox_id,
                )
                if (
                    committed is None
                    or not committed.is_current
                    or committed.execution_attempt != claimed.claim.fencing_token
                    or projected != terminal_event(committed)
                ):
                    raise RuntimeError("stream terminal does not match committed outcome")
                return committed
        raise RuntimeError("agent stream ended without an authoritative outcome")

    async def _ensure_committed_outcome(
        self,
        outcome: CommittedExecutionOutcome,
    ) -> None:
        envelope = terminal_envelope(outcome)
        require_run_event_size(envelope)
        await self.event_sink.ensure_terminal(
            scope=outcome.target.scope,
            event=envelope,
        )

    def _event_envelope(
        self,
        claimed: ClaimedExecution,
        sequence: int,
        event: LiveRunEvent,
    ) -> RunEventEnvelope:
        return RunEventEnvelope(
            tenant_id=claimed.target.scope.tenant_id,
            session_id=claimed.target.session_id,
            run_id=claimed.target.run.run_id,
            turn_id=claimed.execution.input.turn_id,
            execution_attempt=claimed.claim.fencing_token,
            event_sequence=sequence,
            event=event,
            occurred_at=self.clock(),
        )

    async def _heartbeat(
        self,
        claimed: ClaimedExecution,
        lease: SessionLease,
    ) -> None:
        current_lease = lease
        current_claim = claimed.claim
        while True:
            await self.heartbeat_wait(self.heartbeat_interval.total_seconds())
            deadline = (
                asyncio.get_running_loop().time()
                + self.heartbeat_cycle_timeout.total_seconds()
            )
            try:
                async with asyncio.timeout_at(deadline):
                    current_lease = await self.leases.renew(
                        scope=claimed.target.scope,
                        lease=current_lease,
                        ttl=self.lease_ttl,
                    )
            except TimeoutError:
                raise DeliveryUnavailableError() from None
            try:
                async with asyncio.timeout_at(deadline):
                    current_claim = await self.claims.heartbeat(
                        scope=claimed.target.scope,
                        claim=current_claim,
                        ttl=self.claim_ttl,
                    )
            except TimeoutError:
                raise DistributedBackendUnavailableError() from None

    async def _ack(self, delivery: QueueDelivery) -> None:
        await self.queue.ack(topic=self.topic, delivery=delivery)


async def _cancel_and_wait(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _cancel_and_wait_result(
    task: asyncio.Task[object],
) -> BaseException | None:
    if not task.done():
        task.cancel()
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    if isinstance(result, BaseException) and not isinstance(
        result,
        asyncio.CancelledError,
    ):
        return result
    return None


@asynccontextmanager
async def _lease_scope(
    *,
    leases: LeasePort,
    scope: RequestScope,
    lease: SessionLease,
) -> AsyncIterator[None]:
    try:
        yield
    except BaseException as error:
        try:
            await leases.release(scope=scope, lease=lease)
        except BaseException as release_error:
            raise error from release_error
        raise
    else:
        await leases.release(scope=scope, lease=lease)


__all__ = [
    "WorkerAgent",
    "WorkerAgentFactory",
    "WorkerEventSink",
    "WorkerRunner",
]
