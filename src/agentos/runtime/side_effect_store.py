from __future__ import annotations

from typing import Protocol

from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.tools import SideEffectPolicy
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectRecord,
    SideEffectResolution,
)


class SideEffectStore(Protocol):
    """Side Effect Ledger 的唯一原生异步 Port。"""

    async def reserve(
        self,
        *,
        invocation: ToolInvocation,
        policy: SideEffectPolicy,
        invocation_digest: str,
        invocation_ref: ProtectedPayloadRef | None,
        guard: RunWriteGuard,
        supersedes: SideEffectAttemptId | None = None,
    ) -> SideEffectRecord: ...

    async def get(
        self,
        *,
        tenant_id: str | None,
        session_id: str,
        operation_id: str,
        attempt: int | None,
        guard: RunWriteGuard,
    ) -> SideEffectRecord | None: ...

    async def mark_started(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...

    async def mark_ambiguous(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...

    async def begin_compensation(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...

    async def complete_compensation(
        self,
        *,
        attempt_id: CompensationAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...

    async def resolve(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        resolution: SideEffectResolution,
        guard: RunWriteGuard,
    ) -> SideEffectRecord: ...


__all__ = ["SideEffectStore"]
