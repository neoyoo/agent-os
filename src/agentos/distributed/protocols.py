from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from agentos.artifacts.types import ArtifactPage, ArtifactRecord
from agentos.distributed._execution_outcomes import CommittedExecutionOutcome
from agentos.distributed.models import (
    ArtifactContent,
    ClaimedExecution,
    ExecutionClaim,
    OutboxClaim,
    OutboxRecord,
    QueueDelivery,
    ReplayBatch,
    ReplayItem,
    RequestScope,
    RunDeliveryTarget,
    RunEventEnvelope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
    SessionLease,
    StreamGap,
)
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand


class RunSubmissionPort(Protocol):
    """首次 Run 提交的单事务 PostgreSQL 边界。"""

    async def submit(
        self,
        *,
        scope: RequestScope,
        submission: RunSubmission,
    ) -> RunSubmissionReceipt: ...


class RunCommandPort(Protocol):
    """Durable Command 接受与 Outbox 写入的单事务边界。"""

    async def submit_command(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        command: DurableRunCommand,
    ) -> DurableCommandReceipt: ...


class RunQueryPort(Protocol):
    """Tenant-scoped Run read model 边界。"""

    async def get_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> RunReadModel | None: ...

    async def get_active_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
    ) -> RunReadModel | None: ...


class ExecutionClaimPort(Protocol):
    """Accepted input、Claim 和 Fencing 的 PostgreSQL 原子边界。"""

    async def resolve_delivery(
        self,
        *,
        outbox_id: str,
    ) -> RunDeliveryTarget | None: ...

    async def resolve_committed_outcome(
        self,
        *,
        outbox_id: str,
    ) -> CommittedExecutionOutcome | None: ...

    async def claim_pending_turn(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> ClaimedExecution | None: ...

    async def heartbeat(
        self,
        *,
        scope: RequestScope,
        claim: ExecutionClaim,
        ttl: timedelta,
    ) -> ExecutionClaim: ...

    async def release(
        self,
        *,
        scope: RequestScope,
        claim: ExecutionClaim,
    ) -> None: ...

    async def recover_expired(
        self,
        *,
        scope: RequestScope,
        limit: int,
    ) -> tuple[RunDeliveryTarget, ...]: ...


class OutboxPort(Protocol):
    """多个 Relay 安全领取并发布 PostgreSQL Outbox 的边界。"""

    async def claim_batch(
        self,
        *,
        owner_id: str,
        limit: int,
        ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]: ...

    async def mark_published(
        self,
        *,
        claim: OutboxClaim,
        queue_entry_id: str,
    ) -> None: ...

    async def release_claim(self, *, claim: OutboxClaim) -> None: ...


class QueuePort(Protocol):
    """Redis at-least-once queue，仅传递稳定 Outbox ID。"""

    async def publish(self, *, record: OutboxRecord) -> str: ...

    async def receive(
        self,
        *,
        topic: str,
        consumer_id: str,
        limit: int,
    ) -> tuple[QueueDelivery, ...]: ...

    async def reclaim(
        self,
        *,
        topic: str,
        consumer_id: str,
        min_idle: timedelta,
        limit: int,
    ) -> tuple[QueueDelivery, ...]: ...

    async def ack(
        self,
        *,
        topic: str,
        delivery: QueueDelivery,
    ) -> None: ...

    async def close(self) -> None: ...


class LeasePort(Protocol):
    """Redis Session Lease 边界；它不提供数据库写权限。"""

    async def acquire(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> SessionLease | None: ...

    async def renew(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
        ttl: timedelta,
    ) -> SessionLease: ...

    async def ensure_owned(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None: ...

    async def release(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None: ...


class EventSubscription(Protocol):
    """Closeable Replay + Tail subscription owned by its consumer."""

    def __aiter__(self) -> EventSubscription: ...

    async def __anext__(self) -> ReplayItem | StreamGap: ...

    async def aclose(self) -> None: ...


class EventReplayPort(Protocol):
    """Typed live event 的短期 Replay + Tail 边界。"""

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

    async def replay(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str | None,
        limit: int,
    ) -> ReplayBatch | StreamGap: ...

    async def high_water(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
    ) -> str | None: ...

    def follow(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        run_id: str,
        after: str | None,
    ) -> EventSubscription: ...

    async def close(self) -> None: ...


class DistributedArtifactPort(Protocol):
    """Tenant-scoped shared Artifact metadata/blob application boundary。"""

    async def upload(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        upload_id: str,
        data: bytes,
        filename: str | None,
        media_type: str,
    ) -> ArtifactRecord: ...

    async def list(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        cursor: str | None,
        limit: int,
    ) -> ArtifactPage: ...

    async def read(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
    ) -> ArtifactContent: ...

    async def delete(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        artifact_id: str,
        deletion_id: str,
    ) -> None: ...


__all__ = [
    "DistributedArtifactPort",
    "EventReplayPort",
    "EventSubscription",
    "ExecutionClaimPort",
    "LeasePort",
    "OutboxPort",
    "QueuePort",
    "RunCommandPort",
    "RunQueryPort",
    "RunSubmissionPort",
]
