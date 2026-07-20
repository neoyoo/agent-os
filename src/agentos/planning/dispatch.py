from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from agentos.planning.errors import (
    PlanClaimLostError,
    PlanConflictError,
    PlanDispatchAlreadySubmittedError,
    PlanNotFoundError,
    PlanStepNotFoundError,
)
from agentos.planning.models import (
    PlanAssignment,
    PlanState,
    PlanStep,
    SubAgentTemplate,
)
from agentos.planning.store import PlanStoreRecord


PlanDispatchSkipReason = Literal[
    "missing-template",
    "unknown-template",
    "dispatch-failed",
]


class PlanStepDispatcher(Protocol):
    """Plan Step 到外部执行系统的提交边界。"""

    async def submit(
        self,
        *,
        plan: PlanState,
        step: PlanStep,
        assignment: PlanAssignment,
        template: SubAgentTemplate,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PlanDispatchSkip:
    """一次批量派发中未提交的就绪 Step。"""

    plan_id: str
    step_id: str
    reason: PlanDispatchSkipReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PlanDispatchReport:
    """一次就绪 Step 批量派发的瞬时报告。"""

    plan_id: str
    assigned: tuple[PlanAssignment, ...] = ()
    skipped: tuple[PlanDispatchSkip, ...] = ()


class _DispatchRuntime(Protocol):
    templates: dict[str, SubAgentTemplate]
    dispatcher: PlanStepDispatcher | None
    _clock: Callable[[], float]
    _id_factory: Callable[[str], str]

    async def ready_steps(self, plan_id: str) -> tuple[PlanStep, ...]: ...

    async def _require_plan(self, plan_id: str) -> PlanState: ...

    async def _require_plan_record(self, plan_id: str) -> PlanStoreRecord: ...

    def _require_template(self, template_id: str) -> SubAgentTemplate: ...

    def _require_step(self, plan: PlanState, step_id: str) -> PlanStep: ...

    def _replace_step(
        self,
        plan: PlanState,
        step: PlanStep,
        *,
        assignments: tuple[PlanAssignment, ...] | None = None,
    ) -> PlanState: ...

    async def _save_plan(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None = None,
    ) -> None: ...

    async def _ensure_active_plan_claim(self, plan_id: str) -> None: ...


async def _assign_step(
    runtime: _DispatchRuntime,
    plan_id: str,
    step_id: str,
    *,
    template_id: str,
) -> PlanState:
    if runtime.dispatcher is None:
        raise RuntimeError("dispatcher is required to assign plan steps")
    record = await runtime._require_plan_record(plan_id)
    plan = record.plan
    template = runtime._require_template(template_id)
    step = runtime._require_step(plan, step_id)
    task_id = str(runtime._id_factory("task"))
    target_agent_id = (
        str(runtime._id_factory("subagent"))
        if template.target_agent_id is None
        else template.target_agent_id
    )
    assignment = PlanAssignment(
        plan_id=plan_id,
        step_id=step_id,
        template_id=template.template_id,
        task_id=task_id,
        target_agent_id=target_agent_id,
        created_at=float(runtime._clock()),
    )
    updated_step = replace(
        step,
        status="assigned",
        template_id=template.template_id,
        task_id=task_id,
        assigned_agent_id=target_agent_id,
    )
    updated = runtime._replace_step(
        plan,
        updated_step,
        assignments=plan.assignments + (assignment,),
    )
    await runtime._save_plan(updated, expected_revision=record.revision)
    await runtime._ensure_active_plan_claim(plan_id)
    try:
        await _submit_assignment(
            runtime,
            plan=updated,
            step=updated_step,
            assignment=assignment,
            template=template,
        )
    except PlanDispatchAlreadySubmittedError as error:
        await _mark_assignment_dispatch_failed(
            runtime,
            updated,
            updated_step,
            assignment,
            error=str(error) or error.__class__.__name__,
        )
        raise
    except Exception as error:
        await _mark_assignment_dispatch_failed(
            runtime,
            updated,
            updated_step,
            assignment,
            error=str(error) or error.__class__.__name__,
        )
        raise
    return await _mark_assignment_dispatch_submitted(runtime, plan_id, assignment)


async def _dispatch_ready_steps(
    runtime: _DispatchRuntime,
    plan_id: str,
    *,
    default_template_id: str | None = None,
    limit: int | None = None,
) -> PlanDispatchReport:
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    recovered = await _recover_pending_dispatches(runtime, plan_id, limit=limit)
    assigned = list(recovered.assigned)
    skipped = list(recovered.skipped)
    for step in await runtime.ready_steps(plan_id):
        if limit is not None and len(assigned) >= limit:
            break
        template_id = step.template_id or default_template_id
        if template_id is None:
            skipped.append(
                PlanDispatchSkip(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    reason="missing-template",
                    detail=(
                        "step has no template_id and no default_template_id "
                        "was provided"
                    ),
                ),
            )
            continue
        if template_id not in runtime.templates:
            skipped.append(
                PlanDispatchSkip(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    reason="unknown-template",
                    detail=template_id,
                ),
            )
            continue
        try:
            updated = await _assign_step(
                runtime,
                plan_id,
                step.step_id,
                template_id=template_id,
            )
        except PlanClaimLostError:
            raise
        except Exception as error:
            skipped.append(
                PlanDispatchSkip(
                    plan_id=plan_id,
                    step_id=step.step_id,
                    reason="dispatch-failed",
                    detail=str(error) or error.__class__.__name__,
                ),
            )
            continue
        assigned.append(_latest_assignment(updated, step.step_id))
    return PlanDispatchReport(
        plan_id=plan_id,
        assigned=tuple(assigned),
        skipped=tuple(skipped),
    )


async def _recover_pending_dispatches(
    runtime: _DispatchRuntime,
    plan_id: str,
    *,
    limit: int | None = None,
) -> PlanDispatchReport:
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    plan = await runtime._require_plan(plan_id)
    assigned: list[PlanAssignment] = []
    skipped: list[PlanDispatchSkip] = []
    for assignment in plan.assignments:
        if limit is not None and len(assigned) >= limit:
            break
        if assignment.dispatch_status != "pending":
            continue
        if runtime.dispatcher is None:
            raise RuntimeError("dispatcher is required to recover plan dispatches")
        try:
            template = runtime._require_template(assignment.template_id)
        except KeyError:
            skipped.append(
                PlanDispatchSkip(
                    plan_id=plan_id,
                    step_id=assignment.step_id,
                    reason="unknown-template",
                    detail=assignment.template_id,
                ),
            )
            continue
        current_plan = await runtime._require_plan(plan_id)
        step = runtime._require_step(current_plan, assignment.step_id)
        if (
            step.status != "assigned"
            or step.task_id != assignment.task_id
            or step.assigned_agent_id != assignment.target_agent_id
        ):
            skipped.append(
                PlanDispatchSkip(
                    plan_id=plan_id,
                    step_id=assignment.step_id,
                    reason="dispatch-failed",
                    detail="assignment no longer matches assigned step",
                ),
            )
            continue
        try:
            await runtime._ensure_active_plan_claim(plan_id)
            await _submit_assignment(
                runtime,
                plan=current_plan,
                step=step,
                assignment=assignment,
                template=template,
            )
        except PlanClaimLostError:
            raise
        except PlanDispatchAlreadySubmittedError:
            pass
        except Exception as error:
            await _mark_assignment_dispatch_failed(
                runtime,
                current_plan,
                step,
                assignment,
                error=str(error) or error.__class__.__name__,
            )
            skipped.append(
                PlanDispatchSkip(
                    plan_id=plan_id,
                    step_id=assignment.step_id,
                    reason="dispatch-failed",
                    detail=str(error) or error.__class__.__name__,
                ),
            )
            continue
        updated = await _mark_assignment_dispatch_submitted(
            runtime,
            plan_id,
            assignment,
        )
        assigned.append(_latest_assignment(updated, assignment.step_id))
    return PlanDispatchReport(
        plan_id=plan_id,
        assigned=tuple(assigned),
        skipped=tuple(skipped),
    )


async def _submit_assignment(
    runtime: _DispatchRuntime,
    *,
    plan: PlanState,
    step: PlanStep,
    assignment: PlanAssignment,
    template: SubAgentTemplate,
) -> None:
    if runtime.dispatcher is None:
        raise RuntimeError("dispatcher is required to assign plan steps")
    await runtime.dispatcher.submit(
        plan=plan,
        step=step,
        assignment=assignment,
        template=template,
    )


def _latest_assignment(plan: PlanState, step_id: str) -> PlanAssignment:
    for assignment in reversed(plan.assignments):
        if assignment.step_id == step_id:
            return assignment
    raise PlanStepNotFoundError(step_id)


def _replace_assignment(
    runtime: _DispatchRuntime,
    plan: PlanState,
    old_assignment: PlanAssignment,
    new_assignment: PlanAssignment,
) -> PlanState:
    return replace(
        plan,
        assignments=tuple(
            new_assignment if current == old_assignment else current
            for current in plan.assignments
        ),
        updated_at=float(runtime._clock()),
    )


async def _mark_assignment_dispatch_submitted(
    runtime: _DispatchRuntime,
    plan_id: str,
    assignment: PlanAssignment,
) -> PlanState:
    last_plan: PlanState | None = None
    for _ in range(3):
        record = await runtime._require_plan_record(plan_id)
        last_plan = record.plan
        try:
            current_step = runtime._require_step(record.plan, assignment.step_id)
        except PlanStepNotFoundError:
            return record.plan
        if (
            current_step.status != "assigned"
            or current_step.task_id != assignment.task_id
            or current_step.assigned_agent_id != assignment.target_agent_id
        ):
            return record.plan
        current_assignment = next(
            (current for current in record.plan.assignments if current == assignment),
            None,
        )
        if current_assignment is None:
            current_assignment = next(
                (
                    current
                    for current in record.plan.assignments
                    if current.plan_id == assignment.plan_id
                    and current.step_id == assignment.step_id
                    and current.task_id == assignment.task_id
                    and current.target_agent_id == assignment.target_agent_id
                    and current.dispatch_status == "submitted"
                ),
                None,
            )
            return record.plan
        if current_assignment.dispatch_status == "submitted":
            return record.plan
        submitted = replace(
            current_assignment,
            dispatch_status="submitted",
            submitted_at=float(runtime._clock()),
            dispatch_error=None,
        )
        updated = _replace_assignment(
            runtime,
            record.plan,
            current_assignment,
            submitted,
        )
        try:
            await runtime._save_plan(updated, expected_revision=record.revision)
        except PlanConflictError:
            continue
        return updated
    if last_plan is None:
        raise PlanNotFoundError(plan_id)
    raise PlanConflictError(
        f"plan changed before saving submitted dispatch marker: {plan_id}",
    )


async def _mark_assignment_dispatch_failed(
    runtime: _DispatchRuntime,
    plan: PlanState,
    step: PlanStep,
    assignment: PlanAssignment,
    *,
    error: str,
) -> None:
    record = await runtime._require_plan_record(plan.plan_id)
    current_step = runtime._require_step(record.plan, step.step_id)
    if (
        current_step.status != step.status
        or current_step.task_id != step.task_id
        or current_step.assigned_agent_id != step.assigned_agent_id
    ):
        return
    current_assignment = next(
        (current for current in record.plan.assignments if current == assignment),
        None,
    )
    if current_assignment is None:
        return
    now = float(runtime._clock())
    failed_step = replace(
        current_step,
        status="failed",
        error=error,
        last_failed_at=now,
    )
    failed_assignment = replace(
        current_assignment,
        dispatch_status="failed",
        dispatch_error=error,
    )
    failed_plan = runtime._replace_step(
        record.plan,
        failed_step,
        assignments=tuple(
            failed_assignment if current == current_assignment else current
            for current in record.plan.assignments
        ),
    )
    await runtime._save_plan(failed_plan, expected_revision=record.revision)
