from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Protocol, cast

from agentos.planning.models import (
    EVIDENCE_KINDS,
    EvidenceHandle,
    EvidenceKind,
    PlanRetryPolicy,
    PlanState,
    PlanStep,
    PlanStepRetryStatus,
)
from agentos.planning.store import PlanStoreRecord


class _TransitionRuntime(Protocol):
    retry_policy: PlanRetryPolicy
    _clock: Callable[[], float]
    _id_factory: Callable[[str], str]

    def retryable_steps(self, plan_id: str) -> tuple[PlanStep, ...]: ...

    def _require_plan_record(self, plan_id: str) -> PlanStoreRecord: ...

    def _require_step(self, plan: PlanState, step_id: str) -> PlanStep: ...

    def _replace_step(self, plan: PlanState, step: PlanStep) -> PlanState: ...

    def _save_plan(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None = None,
    ) -> None: ...


def record_evidence(
    runtime: _TransitionRuntime,
    plan_id: str,
    *,
    step_ids: tuple[str, ...] = (),
    kind: EvidenceKind,
    summary: str,
    uri: str | None = None,
    producer_agent_id: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> EvidenceHandle:
    record = runtime._require_plan_record(plan_id)
    evidence = EvidenceHandle(
        evidence_id=str(runtime._id_factory("evidence")),
        kind=_validate_evidence_kind(kind),
        summary=summary,
        uri=uri,
        producer_agent_id=producer_agent_id,
        metadata=dict(metadata or {}),
    )
    steps = record.plan.steps
    for step_id in step_ids:
        step = runtime._require_step(replace(record.plan, steps=steps), step_id)
        changed = replace(
            step,
            evidence_ids=step.evidence_ids + (evidence.evidence_id,),
        )
        steps = tuple(changed if item.step_id == step_id else item for item in steps)
    updated = replace(
        record.plan,
        steps=steps,
        evidence=record.plan.evidence + (evidence,),
        updated_at=float(runtime._clock()),
    )
    runtime._save_plan(updated, expected_revision=record.revision)
    return evidence


def complete_step(
    runtime: _TransitionRuntime,
    plan_id: str,
    step_id: str,
    *,
    evidence_ids: tuple[str, ...] = (),
) -> PlanState:
    record = runtime._require_plan_record(plan_id)
    step = runtime._require_step(record.plan, step_id)
    merged = step.evidence_ids + tuple(
        item for item in evidence_ids if item not in step.evidence_ids
    )
    updated_step = replace(
        step,
        status="completed",
        evidence_ids=merged,
        error=None,
        next_retry_at=None,
        retry_status=None,
        retry_exhausted_at=None,
    )
    updated = runtime._replace_step(record.plan, updated_step)
    if all(item.status == "completed" for item in updated.steps):
        updated = replace(
            updated,
            status="completed",
            updated_at=float(runtime._clock()),
        )
    runtime._save_plan(updated, expected_revision=record.revision)
    return updated


def _validate_evidence_kind(kind: str) -> EvidenceKind:
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unsupported evidence kind: {kind}")
    return cast(EvidenceKind, kind)


def fail_step(
    runtime: _TransitionRuntime,
    plan_id: str,
    step_id: str,
    *,
    error: str,
) -> PlanState:
    record = runtime._require_plan_record(plan_id)
    step = runtime._require_step(record.plan, step_id)
    if step.status == "completed":
        raise ValueError(f"completed step cannot be failed: {step_id}")
    now = float(runtime._clock())
    attempts = step.attempts + 1
    retry_status: PlanStepRetryStatus = (
        "exhausted"
        if attempts >= runtime.retry_policy.max_attempts
        else "scheduled"
    )
    updated_step = replace(
        step,
        status="failed",
        error=error,
        attempts=attempts,
        last_failed_at=now,
        next_retry_at=(
            None
            if retry_status == "exhausted"
            else now + runtime.retry_policy.delay_for_attempt(attempts)
        ),
        retry_status=retry_status,
        retry_exhausted_at=now if retry_status == "exhausted" else None,
    )
    updated = runtime._replace_step(record.plan, updated_step)
    if retry_status == "exhausted":
        updated = replace(updated, status="failed", updated_at=now)
    runtime._save_plan(updated, expected_revision=record.revision)
    return updated


def retry_step(
    runtime: _TransitionRuntime,
    plan_id: str,
    step_id: str,
) -> PlanState:
    record = runtime._require_plan_record(plan_id)
    step = runtime._require_step(record.plan, step_id)
    if step not in runtime.retryable_steps(plan_id):
        raise ValueError(f"step is not retryable: {step_id}")
    updated = runtime._replace_step(
        record.plan,
        replace(
            step,
            status="pending",
            assigned_agent_id=None,
            task_id=None,
            error=None,
            next_retry_at=None,
            retry_status=None,
            retry_exhausted_at=None,
        ),
    )
    runtime._save_plan(updated, expected_revision=record.revision)
    return updated
