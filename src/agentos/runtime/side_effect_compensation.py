from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentos.capabilities.invocation import (
    ToolCompensationContext,
    ToolCompensationInvocation,
    ToolInvocation,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectRecord,
    SideEffectStatus,
    SideEffectTransitionError,
)
from agentos.runtime.tool_identity import invocation_digest


CompensationInvoker = Callable[[ToolCompensationInvocation], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ToolCompensationRuntime:
    """协调 typed compensation handler 与 fenced Ledger completion。"""

    store: SideEffectStore

    async def execute(
        self,
        *,
        record: SideEffectRecord,
        invocation: ToolInvocation,
        guard: RunWriteGuard,
        invoke: CompensationInvoker,
        recovery: bool = False,
    ) -> SideEffectRecord:
        if type(record) is not SideEffectRecord:
            raise TypeError("record must be SideEffectRecord")
        if type(invocation) is not ToolInvocation:
            raise TypeError("invocation must be ToolInvocation")
        if type(guard) is not RunWriteGuard:
            raise TypeError("guard must be RunWriteGuard")
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        if type(recovery) is not bool:
            raise TypeError("recovery must be bool")
        self._require_pair(record, invocation)
        if record.status is SideEffectStatus.COMPENSATED:
            return record
        if record.status is not SideEffectStatus.COMPENSATING:
            raise SideEffectTransitionError
        current = (
            await self.store.begin_compensation(
                attempt_id=record.attempt_id,
                guard=guard,
            )
            if recovery
            else record
        )
        compensation_id = current.compensation_operation_id
        compensation_attempt = current.compensation_attempt
        if compensation_id is None or compensation_attempt is None:
            raise SideEffectTransitionError
        await invoke(
            ToolCompensationInvocation(
                arguments=invocation.arguments,
                context=ToolCompensationContext(
                    original_operation_id=current.attempt_id.operation_id,
                    compensation_operation_id=compensation_id,
                    tenant_id=current.attempt_id.tenant_id,
                    session_id=current.attempt_id.session_id,
                    run_id=current.run_id,
                    turn_id=current.turn_id,
                    invocation_id=current.invocation_id,
                    attempt=current.attempt_id.attempt,
                ),
                result_ref=current.result_ref,
            ),
        )
        return await self.store.complete_compensation(
            attempt_id=CompensationAttemptId(
                current.attempt_id,
                compensation_id,
                compensation_attempt,
            ),
            guard=guard,
        )

    @staticmethod
    def _require_pair(record: SideEffectRecord, invocation: ToolInvocation) -> None:
        context = invocation.context
        if (
            record.attempt_id.tenant_id != context.tenant_id
            or record.attempt_id.session_id != context.session_id
            or record.attempt_id.operation_id != context.operation_id
            or record.attempt_id.attempt != context.attempt
            or record.run_id != context.run_id
            or record.turn_id != context.turn_id
            or record.invocation_id != context.invocation_id
            or record.tool_name != invocation.tool_name
            or record.invocation_digest != invocation_digest(invocation)
        ):
            raise SideEffectTransitionError


__all__ = ["CompensationInvoker", "ToolCompensationRuntime"]
