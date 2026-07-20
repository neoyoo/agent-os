from __future__ import annotations

from dataclasses import dataclass

from agentos.messages import MessageRuntime
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.query_loop_support import (
    ToolCallRouterBoundary,
    restored_tool_iteration_count,
)
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.tool_invocations import (
    ToolInvocationPlan,
    prepare_tool_invocation_batch,
)
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.runtime.tool_side_effect_runtime import ToolSideEffectRuntime


@dataclass(frozen=True, slots=True)
class ToolLoopRecovery:
    iterations: int
    provider_call_index: int
    pending_plan: ToolInvocationPlan | None


async def restore_tool_loop(
    *,
    run_id: str,
    cursor: RunExecutionCursor | None,
    messages: MessageRuntime,
    payloads: ToolPayloadRuntime,
    router: ToolCallRouterBoundary | None,
    side_effects: ToolSideEffectRuntime,
    guard: RunWriteGuard,
) -> ToolLoopRecovery:
    """Restore a pending batch or SDK-owned after-tools projections."""

    if cursor is None:
        return ToolLoopRecovery(0, 0, None)
    iterations = restored_tool_iteration_count(messages, cursor)
    if cursor.stage == "pending_tools":
        return ToolLoopRecovery(
            iterations,
            cursor.provider_call_index,
            payloads.restore_pending_plan(run_id=run_id, cursor=cursor),
        )
    if cursor.stage == "after_tools":
        if router is None:
            raise RuntimeError("tool call router is required for tool recovery")
        completed_plan = payloads.restore_completed_plan(
            run_id=run_id,
            cursor=cursor,
            messages=messages,
        )
        if completed_plan is not None:
            completed_batch = prepare_tool_invocation_batch(
                completed_plan,
                router.tool_contract_for,
            )
            await side_effects.restore_after_tools(
                plan=completed_plan,
                contracts=completed_batch.contracts,
                guard=guard,
            )
        return ToolLoopRecovery(iterations, cursor.provider_call_index + 1, None)
    return ToolLoopRecovery(iterations, cursor.provider_call_index, None)


__all__ = ["ToolLoopRecovery", "restore_tool_loop"]
