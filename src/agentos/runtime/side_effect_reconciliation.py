from __future__ import annotations

from agentos.capabilities.executor import ToolExecutionError
from agentos.messages import MessageRuntime
from agentos.runtime.query_loop_support import ToolCallRouterBoundary
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_compensation import ToolCompensationRuntime
from agentos.runtime.side_effect_resume import SideEffectResume
from agentos.runtime.side_effect_resume_validator import SideEffectResumeValidator
from agentos.runtime.side_effect_types import SideEffectResolutionKind
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.runtime.tool_side_effect_runtime import ToolSideEffectRuntime


async def prepare_reconciliation_resume(
    *,
    resume: SideEffectResume,
    messages: MessageRuntime,
    payloads: ToolPayloadRuntime,
    router: ToolCallRouterBoundary | None,
    side_effects: ToolSideEffectRuntime,
    validator: SideEffectResumeValidator,
    guard: RunWriteGuard,
) -> BaseException | None:
    """Validate typed control and prepare Provider or terminal recovery."""

    await validator.validate(resume=resume, guard=guard)
    kind = resume.resolution.kind
    if kind is SideEffectResolutionKind.FAIL:
        return ToolExecutionError("side effect reconciliation failed the run")
    if kind is SideEffectResolutionKind.COMPENSATE:
        if router is None:
            raise RuntimeError("tool call router is required for compensation")
        plan = payloads.restore_pending_plan(
            run_id=resume.run_id,
            cursor=resume.source_cursor,
        )
        matches = tuple(
            entry
            for entry in plan.entries
            if entry.invocation.context.operation_id
            == resume.resolution.operation_id
        )
        if len(matches) != 1:
            raise RuntimeError("compensation source invocation is missing")
        invocation = matches[0].invocation

        async def invoke(compensation):  # type: ignore[no-untyped-def]
            await router.execute_compensation(
                invocation.tool_name,
                compensation,
            )

        await ToolCompensationRuntime(side_effects.store).execute(
            record=resume.record,
            invocation=invocation.with_attempt(resume.record.attempt_id.attempt),
            guard=guard,
            invoke=invoke,
        )
        return ToolExecutionError("compensated side effect failed the run")
    assistant_id = resume.source_cursor.assistant_message_id
    if assistant_id is None:
        raise RuntimeError("side effect source assistant is missing")
    assistant = messages.store.get(assistant_id)
    if assistant.role != "assistant" or not assistant.tool_calls:
        raise RuntimeError("side effect source assistant is invalid")
    messages.active_window.append(assistant_id)
    return None


__all__ = ["prepare_reconciliation_resume"]
