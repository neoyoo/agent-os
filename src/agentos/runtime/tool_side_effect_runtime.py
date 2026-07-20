from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentos._waiting import WaitReason, WaitRequest
from agentos.capabilities.executor import (
    ToolExecutionError,
    ToolExecutionOutcome,
    ToolExecutionResult,
)
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.result_refs import (
    ArtifactToolResultRef,
    InlineToolResultRef,
    ToolResultRef,
)
from agentos.capabilities.tools import SideEffectPolicy, ToolExecutionContract
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_integrity import (
    result_ref_digest,
    wait_reason_digest,
)
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.completed_result_projection import CompletedResultProjector
from agentos.runtime.side_effect_types import (
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectResolutionOutcome,
    SideEffectStatus,
    SideEffectTransitionError,
    WaitingToolCompletion,
)
from agentos.runtime.tool_identity import invocation_digest
from agentos.runtime.tool_invocations import ToolInvocationPlan, ToolInvocationPlanEntry
from agentos.runtime.tool_result_refs import ToolResultRefProjector
from agentos.policies.security import SecurityPolicyError


ToolResultProducer = Callable[
    [ToolInvocation],
    Awaitable[ToolExecutionOutcome],
]
ToolResultMapper = Callable[[ToolExecutionResult], ToolExecutionResult]


@dataclass(frozen=True, slots=True)
class WaitingToolHandoff:
    """把 handler wait 或 reconciliation 交给 WAITING composite boundary。"""

    request: WaitRequest
    completion: WaitingToolCompletion | None


@dataclass(frozen=True, slots=True)
class ToolSideEffectRuntime:
    """Side Effect Ledger 恢复矩阵与 Tool 结果提交的唯一协调器。"""

    store: SideEffectStore
    completed_result_projector: CompletedResultProjector | None = None
    result_ref_projector: ToolResultRefProjector | None = None

    async def restore_after_tools(
        self,
        *,
        plan: ToolInvocationPlan,
        contracts: tuple[ToolExecutionContract, ...],
        guard: RunWriteGuard,
    ) -> None:
        """Restore ephemeral projections omitted from an after_tools checkpoint."""

        if len(plan.entries) != len(contracts):
            raise ValueError("completed tool contracts do not match invocation plan")
        for entry, contract in zip(plan.entries, contracts, strict=True):
            invocation = entry.invocation
            context = invocation.context
            record = await self.store.get(
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                operation_id=context.operation_id,
                attempt=None,
                guard=guard,
            )
            if record is None:
                raise SideEffectTransitionError
            self._require_contract(record, entry, contract)
            if (
                record.status is not SideEffectStatus.COMPLETED
                or record.outcome_kind is not SideEffectOutcomeKind.PROVIDER_RESULT
            ):
                raise SideEffectTransitionError
            _result_from_record(record, context.tool_call_id)
            if self.completed_result_projector is not None:
                await self.completed_result_projector.project_after_tools(invocation)

    async def reserve(
        self,
        entry: ToolInvocationPlanEntry,
        contract: ToolExecutionContract,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        invocation = entry.invocation
        return await self.store.reserve(
            invocation=invocation,
            policy=contract.side_effect_policy,
            invocation_digest=invocation_digest(invocation),
            invocation_ref=entry.invocation_ref,
            guard=guard,
        )

    async def execute(
        self,
        entry: ToolInvocationPlanEntry,
        contract: ToolExecutionContract,
        *,
        guard: RunWriteGuard,
        produce: ToolResultProducer,
        map_result: ToolResultMapper | None = None,
    ) -> ToolExecutionResult | WaitingToolHandoff:
        invocation = entry.invocation
        context = invocation.context
        current = await self.store.get(
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            operation_id=context.operation_id,
            attempt=None,
            guard=guard,
        )
        if current is None:
            current = await self.reserve(entry, contract, guard)
        else:
            self._require_contract(current, entry, contract)

        if current.status is SideEffectStatus.COMPLETED:
            return _map_replayed_result(
                current,
                await self._replay_completed(current, invocation),
                map_result,
            )
        if current.status is SideEffectStatus.STARTED:
            if contract.side_effect_policy in {
                SideEffectPolicy.PURE,
                SideEffectPolicy.IDEMPOTENT,
            }:
                invocation = invocation.with_attempt(current.attempt_id.attempt + 1)
                current = await self.store.reserve(
                    invocation=invocation,
                    policy=contract.side_effect_policy,
                    invocation_digest=invocation_digest(invocation),
                    invocation_ref=entry.invocation_ref,
                    guard=guard,
                    supersedes=current.attempt_id,
                )
            else:
                current = await self.store.mark_ambiguous(
                    attempt_id=current.attempt_id,
                    guard=guard,
                )
                return _reconciliation_handoff(current)
        elif current.status is SideEffectStatus.AMBIGUOUS:
            return _reconciliation_handoff(current)
        elif current.status is SideEffectStatus.RESOLVED:
            if current.resolution is SideEffectResolutionOutcome.ACCEPTED:
                return _map_replayed_result(
                    current,
                    self._replay_resolved(
                        current,
                        invocation.context.tool_call_id,
                    ),
                    map_result,
                )
            raise SideEffectTransitionError
        elif current.status is not SideEffectStatus.RESERVED:
            raise SideEffectTransitionError
        else:
            invocation = invocation.with_attempt(current.attempt_id.attempt)

        current = await self.store.mark_started(
            attempt_id=current.attempt_id,
            guard=guard,
        )
        try:
            outcome = await produce(invocation)
            outcome = _validate_produced_outcome(outcome, invocation, contract)
        except (asyncio.CancelledError, TimeoutError):
            raise
        except Exception as error:
            if contract.side_effect_policy in {
                SideEffectPolicy.PURE,
                SideEffectPolicy.IDEMPOTENT,
            }:
                await self.store.complete(
                    attempt_id=current.attempt_id,
                    completion=SideEffectCompletion(
                        SideEffectOutcomeKind.HANDLER_ERROR,
                        failure_code="tool_execution_failed",
                    ),
                    guard=guard,
                )
                if isinstance(error, SecurityPolicyError):
                    raise
                raise ToolExecutionError("tool execution failed") from None
            ambiguous = await self.store.mark_ambiguous(
                attempt_id=current.attempt_id,
                guard=guard,
            )
            return _reconciliation_handoff(ambiguous)
        if type(outcome) is WaitRequest:
            reason = outcome.reason
            return WaitingToolHandoff(
                outcome,
                WaitingToolCompletion(
                    invocation.context.invocation_id,
                    invocation.context.operation_id,
                    invocation.context.attempt,
                    wait_reason_digest(reason),
                ),
            )
        assert type(outcome) is ToolExecutionResult
        reference = await self._project_result_ref(invocation, outcome.content)
        visible_result = (
            _result_from_reference(reference, outcome.tool_call_id)
            if map_result is None
            else _map_visible_result(outcome, map_result)
        )
        if (
            type(reference) is ArtifactToolResultRef
            and visible_result.content != reference.preview
        ):
            raise ValueError("artifact tool result preview does not match visible result")
        await self.store.complete(
            attempt_id=current.attempt_id,
            completion=SideEffectCompletion(
                SideEffectOutcomeKind.PROVIDER_RESULT,
                result_ref=reference,
                result_digest=result_ref_digest(reference),
            ),
            guard=guard,
        )
        return visible_result

    async def _project_result_ref(
        self,
        invocation: ToolInvocation,
        content: str,
    ) -> ToolResultRef:
        if self.result_ref_projector is None:
            return InlineToolResultRef(content)
        reference = await self.result_ref_projector.project(invocation, content)
        if type(reference) not in {InlineToolResultRef, ArtifactToolResultRef}:
            raise TypeError("tool result projector returned an invalid reference")
        return reference

    @staticmethod
    def _require_contract(
        record: SideEffectRecord,
        entry: ToolInvocationPlanEntry,
        contract: ToolExecutionContract,
    ) -> None:
        invocation = entry.invocation
        if (
            record.policy is not contract.side_effect_policy
            or record.invocation_id != invocation.context.invocation_id
            or record.tool_name != invocation.tool_name
            or record.invocation_digest != invocation_digest(invocation)
            or record.invocation_ref != entry.invocation_ref
        ):
            raise SideEffectTransitionError

    async def _replay_completed(
        self,
        record: SideEffectRecord,
        invocation: ToolInvocation,
    ) -> ToolExecutionResult:
        if record.outcome_kind is SideEffectOutcomeKind.HANDLER_ERROR:
            raise ToolExecutionError("tool execution failed")
        if record.outcome_kind is not SideEffectOutcomeKind.PROVIDER_RESULT:
            raise SideEffectTransitionError
        result = _result_from_record(record, invocation.context.tool_call_id)
        if self.completed_result_projector is not None:
            await self.completed_result_projector.project(invocation)
        return result

    @staticmethod
    def _replay_resolved(
        record: SideEffectRecord,
        tool_call_id: str,
    ) -> ToolExecutionResult:
        return _result_from_record(record, tool_call_id)


def _result_from_record(
    record: SideEffectRecord,
    tool_call_id: str,
) -> ToolExecutionResult:
    reference = record.result_ref
    if reference is None or result_ref_digest(reference) != record.result_digest:
        raise SideEffectTransitionError
    return _result_from_reference(reference, tool_call_id)


def _result_from_reference(
    reference: ToolResultRef,
    tool_call_id: str,
) -> ToolExecutionResult:
    content = (
        reference.content
        if type(reference) is InlineToolResultRef
        else reference.preview
        if type(reference) is ArtifactToolResultRef
        else None
    )
    if content is None:
        raise SideEffectTransitionError
    return ToolExecutionResult(tool_call_id, content)


def _map_visible_result(
    result: ToolExecutionResult,
    mapper: ToolResultMapper | None,
) -> ToolExecutionResult:
    if mapper is None:
        return result
    mapped = mapper(result)
    if type(mapped) is not ToolExecutionResult or mapped.tool_call_id != result.tool_call_id:
        raise TypeError("tool result mapper returned an invalid result")
    return mapped


def _map_replayed_result(
    record: SideEffectRecord,
    result: ToolExecutionResult,
    mapper: ToolResultMapper | None,
) -> ToolExecutionResult:
    if type(record.result_ref) is ArtifactToolResultRef:
        return result
    return _map_visible_result(result, mapper)


def _validate_produced_outcome(
    outcome: ToolExecutionOutcome,
    invocation: ToolInvocation,
    contract: ToolExecutionContract,
) -> ToolExecutionResult | WaitRequest:
    if type(outcome) is WaitRequest:
        if not contract.wait_capable:
            raise ToolExecutionError("tool returned undeclared wait request")
        return outcome
    if type(outcome) is not ToolExecutionResult:
        raise ToolExecutionError("tool returned an invalid result")
    if outcome.tool_call_id != invocation.context.tool_call_id:
        raise ToolExecutionError("tool result does not match invocation")
    return outcome


def _reconciliation_handoff(record: SideEffectRecord) -> WaitingToolHandoff:
    operation_id = record.attempt_id.operation_id
    return WaitingToolHandoff(
        WaitRequest(WaitReason("side_effect_reconciliation", operation_id)),
        None,
    )


__all__ = ["ToolSideEffectRuntime", "WaitingToolHandoff"]
