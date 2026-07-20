from __future__ import annotations

from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._side_effect_composite import (
    apply_cancel_safe_stop,
    complete_wait_control,
)
from agentos.distributed.postgres._side_effect_reservations import (
    get_record,
    reserve,
)
from agentos.distributed.postgres._side_effect_transitions import (
    begin_compensation,
    complete,
    complete_compensation,
    mark_ambiguous,
    mark_started,
    resolve,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectRecord,
    SideEffectResolution,
)


class PostgresSideEffectStore:
    """Fenced PostgreSQL implementation of the canonical SideEffectStore."""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def reserve(
        self,
        *,
        invocation: ToolInvocation,
        policy: SideEffectPolicy,
        invocation_digest: str,
        invocation_ref: ProtectedPayloadRef | None,
        guard: RunWriteGuard,
        supersedes: SideEffectAttemptId | None = None,
    ) -> SideEffectRecord:
        return await reserve(
            self._database,
            invocation=invocation,
            policy=policy,
            invocation_digest=invocation_digest,
            invocation_ref=invocation_ref,
            guard=guard,
            supersedes=supersedes,
        )

    async def get(
        self,
        *,
        tenant_id: str | None,
        session_id: str,
        operation_id: str,
        attempt: int | None,
        guard: RunWriteGuard,
    ) -> SideEffectRecord | None:
        return await get_record(
            self._database,
            tenant_id=tenant_id,
            session_id=session_id,
            operation_id=operation_id,
            attempt=attempt,
            guard=guard,
        )

    async def mark_started(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await mark_started(
            self._database,
            attempt_id=attempt_id,
            guard=guard,
        )

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await complete(
            self._database,
            attempt_id=attempt_id,
            completion=completion,
            guard=guard,
        )

    async def mark_ambiguous(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await mark_ambiguous(
            self._database,
            attempt_id=attempt_id,
            guard=guard,
        )

    async def begin_compensation(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await begin_compensation(
            self._database,
            attempt_id=attempt_id,
            guard=guard,
        )

    async def complete_compensation(
        self,
        *,
        attempt_id: CompensationAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await complete_compensation(
            self._database,
            attempt_id=attempt_id,
            guard=guard,
        )

    async def resolve(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        resolution: SideEffectResolution,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await resolve(
            self._database,
            attempt_id=attempt_id,
            resolution=resolution,
            guard=guard,
        )


__all__ = [
    "PostgresSideEffectStore",
    "apply_cancel_safe_stop",
    "complete_wait_control",
]
