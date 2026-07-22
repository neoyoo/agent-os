from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import cast

from agentos.planning.decomposition import (
    PlanDecomposition,
    PlanDecompositionGatePolicy,
    PlanDecompositionGateReport,
    PlanDecompositionValidationReport,
    gate_decomposition_proposal,
    materialize_decomposition,
    validate_decomposition,
)
from agentos.planning.decomposition_governance import (
    PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE,
    PlannerLlmGovernanceEvidenceGateReport,
    PlannerLlmGovernanceEvidenceRecord,
    gate_llm_governance_evidence,
)
from agentos.planning.dispatch import (
    PlanDispatchReport,
    PlanStepDispatcher,
    _assign_step,
    _dispatch_ready_steps,
    _recover_pending_dispatches,
)
from agentos.planning.errors import (
    PlanClaimLostError,
    PlanNotFoundError,
)
from agentos.planning.models import (
    EvidenceHandle,
    EvidenceKind,
    PlanAssignment,
    PlanRetryPolicy,
    PlanState,
    PlanStatus,
    PlanStep,
    SubAgentTemplate,
)
from agentos.planning.mutation import PlanMutationCoordinator
from agentos.planning.runtime_support import (
    default_runtime_id,
    replace_plan_step,
    require_step,
    require_template,
    validate_plan_statuses,
)
from agentos.planning.scheduling import (
    _ready_steps,
    _retryable_steps,
    claim_schedulable_plans,
    claimed_scheduler_tick,
    pending_dispatch_step_ids,
    schedulable_plans,
    scheduler_tick,
    sweep_expired_claims,
)
from agentos.planning.scheduling_reports import (
    PlanClaimedSchedulerTickReport,
    PlanClaimSweepReport,
    PlanSchedulerTickReport,
    PlannerSchedulablePlan,
)
from agentos.planning.store import (
    PlanClaimRecord,
    PlanClaimResult,
    PlanClaimStore,
    PlanStore,
    PlanStoreRecord,
)
from agentos.planning.transitions import (
    complete_step as _complete_step,
    fail_step as _fail_step,
    record_evidence as _record_evidence,
    retry_step as _retry_step,
)
from agentos.workspace.models import WorkspaceHandle


class PlannerRuntime:
    """协调 PlanStore、调度和外部 Step 派发的 Planner 领域运行时。"""

    def __init__(
        self,
        *,
        store: PlanStore,
        templates: tuple[SubAgentTemplate, ...] = (),
        dispatcher: PlanStepDispatcher | None = None,
        retry_policy: PlanRetryPolicy | None = None,
        claim_store: PlanClaimStore | None = None,
        clock: object | None = None,
        id_factory: object | None = None,
    ) -> None:
        self.store = store
        self.templates = {item.template_id: item for item in templates}
        self.dispatcher = dispatcher
        self.retry_policy = retry_policy or PlanRetryPolicy(max_attempts=1)
        self.claim_store = claim_store
        self._clock: Callable[[], float] = clock if callable(clock) else time.time
        self._id_factory: Callable[[str], str] = (
            id_factory if callable(id_factory) else self._default_id
        )
        self._mutations = PlanMutationCoordinator(
            store=store,
            claim_store=claim_store,
            clock=self._clock,
        )
        bind_claim_store = getattr(store, "bind_claim_store", None)
        if claim_store is not None and callable(bind_claim_store):
            bind_claim_store(claim_store)

    async def create_plan(
        self,
        *,
        objective: str,
        owner_agent_id: str,
        plan_id: str | None = None,
        workspace: WorkspaceHandle | None = None,
    ) -> PlanState:
        now = float(self._clock())
        plan = PlanState(
            plan_id=plan_id or str(self._id_factory("plan")),
            objective=objective,
            owner_agent_id=owner_agent_id,
            created_at=now,
            updated_at=now,
            workspace=workspace,
        )
        await self.store.create_plan(plan)
        return plan

    async def add_step(
        self,
        plan_id: str,
        *,
        instruction: str,
        required_capabilities: tuple[str, ...] = (),
        template_id: str | None = None,
    ) -> PlanState:
        if template_id is not None:
            self._require_template(template_id)
        record = await self._require_plan_record(plan_id)
        step = PlanStep(
            step_id=str(self._id_factory("step")),
            instruction=instruction,
            required_capabilities=tuple(required_capabilities),
            template_id=template_id,
        )
        updated = replace(
            record.plan,
            steps=record.plan.steps + (step,),
            updated_at=float(self._clock()),
        )
        await self._save_plan(updated, expected_revision=record.revision)
        return updated

    async def create_plan_from_decomposition(
        self,
        decomposition: PlanDecomposition,
        *,
        owner_agent_id: str,
        plan_id: str | None = None,
        workspace: WorkspaceHandle | None = None,
    ) -> PlanState:
        objective, steps = materialize_decomposition(
            decomposition,
            templates=self.templates,
            id_factory=self._id_factory,
        )
        now = float(self._clock())
        plan = PlanState(
            plan_id=plan_id or str(self._id_factory("plan")),
            objective=objective,
            owner_agent_id=owner_agent_id,
            steps=steps,
            created_at=now,
            updated_at=now,
            workspace=workspace,
        )
        await self.store.create_plan(plan)
        return plan

    def validate_decomposition(
        self,
        decomposition: PlanDecomposition,
    ) -> PlanDecompositionValidationReport:
        return validate_decomposition(decomposition, templates=self.templates)

    def gate_decomposition_proposal(
        self,
        proposal: Mapping[str, object],
        *,
        policy: PlanDecompositionGatePolicy | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> PlanDecompositionGateReport:
        return gate_decomposition_proposal(
            proposal,
            templates=self.templates,
            policy=policy,
            metadata=metadata,
        )

    def gate_llm_governance_evidence(
        self,
        record: PlannerLlmGovernanceEvidenceRecord,
        *,
        required_evidence: tuple[str, ...] = (
            PLANNER_LLM_GOVERNANCE_EXECUTION_REQUIRED_EVIDENCE
        ),
        metadata: Mapping[str, object] | None = None,
    ) -> PlannerLlmGovernanceEvidenceGateReport:
        return gate_llm_governance_evidence(
            record,
            required_evidence=required_evidence,
            metadata=metadata,
        )

    async def ready_steps(self, plan_id: str) -> tuple[PlanStep, ...]:
        return _ready_steps(await self._require_plan(plan_id))

    async def retryable_steps(self, plan_id: str) -> tuple[PlanStep, ...]:
        return _retryable_steps(
            await self._require_plan(plan_id),
            retry_policy=self.retry_policy,
            now=float(self._clock()),
        )

    async def get_plan(
        self,
        plan_id: str,
        *,
        owner_agent_id: str | None = None,
    ) -> PlanState:
        plan = await self._require_plan(plan_id)
        if owner_agent_id is not None and plan.owner_agent_id != owner_agent_id:
            raise PlanNotFoundError(plan_id)
        return plan

    async def list_plans(
        self,
        owner_agent_id: str | None = None,
    ) -> list[PlanState]:
        return await self.store.list_plans(owner_agent_id)

    async def schedulable_plans(
        self,
        *,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
    ) -> tuple[PlannerSchedulablePlan, ...]:
        return await schedulable_plans(
            self,
            owner_agent_id=owner_agent_id,
            statuses=statuses,
            limit=limit,
        )

    async def claim_schedulable_plans(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
    ) -> tuple[PlanClaimResult, ...]:
        return await claim_schedulable_plans(
            self,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            owner_agent_id=owner_agent_id,
            statuses=statuses,
            limit=limit,
        )

    async def sweep_expired_claims(
        self,
        *,
        owner_agent_id: str | None = None,
        now: float | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> PlanClaimSweepReport:
        return await sweep_expired_claims(
            self,
            owner_agent_id=owner_agent_id,
            now=now,
            limit=limit,
            dry_run=dry_run,
        )

    async def assign_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        template_id: str,
    ) -> PlanState:
        return await _assign_step(self, plan_id, step_id, template_id=template_id)

    async def dispatch_ready_steps(
        self,
        plan_id: str,
        *,
        default_template_id: str | None = None,
        limit: int | None = None,
    ) -> PlanDispatchReport:
        return await _dispatch_ready_steps(
            self,
            plan_id,
            default_template_id=default_template_id,
            limit=limit,
        )

    async def recover_pending_dispatches(
        self,
        plan_id: str,
        *,
        limit: int | None = None,
    ) -> PlanDispatchReport:
        return await _recover_pending_dispatches(self, plan_id, limit=limit)

    async def scheduler_tick(
        self,
        plan_id: str,
        *,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
    ) -> PlanSchedulerTickReport:
        return await scheduler_tick(
            self,
            plan_id,
            default_template_id=default_template_id,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
        )

    async def claimed_scheduler_tick(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        owner_agent_id: str | None = None,
        statuses: tuple[PlanStatus, ...] = ("draft", "running"),
        limit: int | None = None,
        default_template_id: str | None = None,
        retry_limit: int | None = None,
        dispatch_limit: int | None = None,
        release_after_tick: bool = False,
    ) -> PlanClaimedSchedulerTickReport:
        return await claimed_scheduler_tick(
            self,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            owner_agent_id=owner_agent_id,
            statuses=statuses,
            limit=limit,
            default_template_id=default_template_id,
            retry_limit=retry_limit,
            dispatch_limit=dispatch_limit,
            release_after_tick=release_after_tick,
        )

    async def record_evidence(
        self,
        plan_id: str,
        *,
        step_ids: tuple[str, ...] = (),
        kind: EvidenceKind,
        summary: str,
        uri: str | None = None,
        producer_agent_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> EvidenceHandle:
        return await _record_evidence(
            self,
            plan_id,
            step_ids=step_ids,
            kind=kind,
            summary=summary,
            uri=uri,
            producer_agent_id=producer_agent_id,
            metadata=metadata,
        )

    async def complete_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        evidence_ids: tuple[str, ...] = (),
    ) -> PlanState:
        return await _complete_step(
            self,
            plan_id,
            step_id,
            evidence_ids=evidence_ids,
        )

    async def fail_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        error: str,
    ) -> PlanState:
        return await _fail_step(self, plan_id, step_id, error=error)

    async def retry_step(self, plan_id: str, step_id: str) -> PlanState:
        return await _retry_step(self, plan_id, step_id)

    async def _run_scheduler_tick_with_claim(
        self,
        claim: PlanClaimRecord | None,
        *,
        default_template_id: str | None,
        retry_limit: int | None,
        dispatch_limit: int | None,
    ) -> PlanSchedulerTickReport:
        if claim is None:
            raise PlanClaimLostError("missing scheduler claim record")
        async with self._mutations.claim_scope(claim):
            return await self.scheduler_tick(
                claim.plan_id,
                default_template_id=default_template_id,
                retry_limit=retry_limit,
                dispatch_limit=dispatch_limit,
            )

    async def _save_plan(
        self,
        plan: PlanState,
        *,
        expected_revision: int | None = None,
    ) -> None:
        await self._mutations.save(
            plan,
            expected_revision=expected_revision,
        )

    async def _ensure_active_plan_claim(self, plan_id: str) -> None:
        await self._mutations.ensure_active_claim(plan_id)

    async def _require_plan(self, plan_id: str) -> PlanState:
        plan = await self.store.get_plan(plan_id)
        if plan is None:
            raise PlanNotFoundError(plan_id)
        return plan

    async def _require_plan_record(self, plan_id: str) -> PlanStoreRecord:
        get_record = getattr(self.store, "get_plan_record", None)
        record = await get_record(plan_id) if callable(get_record) else None
        if callable(get_record):
            if record is None:
                raise PlanNotFoundError(plan_id)
            return cast(PlanStoreRecord, record)
        return PlanStoreRecord(plan=await self._require_plan(plan_id), revision=0)

    def _require_template(self, template_id: str) -> SubAgentTemplate:
        return require_template(self.templates, template_id)

    def _require_step(self, plan: PlanState, step_id: str) -> PlanStep:
        return require_step(plan, step_id)

    def _validate_plan_statuses(
        self,
        statuses: tuple[PlanStatus, ...],
    ) -> tuple[PlanStatus, ...]:
        return validate_plan_statuses(statuses)

    def _pending_dispatch_step_ids(self, plan: PlanState) -> tuple[str, ...]:
        return pending_dispatch_step_ids(plan)

    def _replace_step(
        self,
        plan: PlanState,
        step: PlanStep,
        *,
        assignments: tuple[PlanAssignment, ...] | None = None,
    ) -> PlanState:
        return replace_plan_step(
            plan,
            step,
            assignments=assignments,
            updated_at=float(self._clock()),
        )

    def _default_id(self, prefix: str) -> str:
        return default_runtime_id(prefix)


__all__ = ["PlannerRuntime"]
