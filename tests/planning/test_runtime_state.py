from __future__ import annotations

import pytest

from agentos.planning import (
    EvidenceHandle,
    InMemoryPlanStore,
    PlanConflictError,
    PlanNotFoundError,
    PlanRetryPolicy,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from tests.planning._async import async_test


def test_subagent_template_captures_execution_policy() -> None:
    template = SubAgentTemplate(
        template_id="researcher",
        name="Researcher",
        role="Find and summarize evidence.",
        capabilities=("research", "summarize"),
        allowed_tool_names=("web_search",),
        context_seed=("Prefer primary sources.",),
        workspace_scope="task",
        target_agent_id="expert_researcher",
    )

    assert template.capabilities == ("research", "summarize")
    assert template.allowed_tool_names == ("web_search",)
    assert template.context_seed == ("Prefer primary sources.",)
    assert template.workspace_scope == "task"
    assert template.target_agent_id == "expert_researcher"


@async_test
async def test_in_memory_plan_store_creates_and_updates_plan() -> None:
    store = InMemoryPlanStore()
    plan = PlanState(
        plan_id="plan_1",
        objective="Review the SDK architecture.",
        owner_agent_id="leader",
        created_at=1.0,
        updated_at=1.0,
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Review multi-agent primitives.",
                required_capabilities=("architecture-review",),
            ),
        ),
        evidence=(
            EvidenceHandle(
                evidence_id="evidence_1",
                kind="text",
                summary="Initial notes.",
            ),
        ),
    )

    await store.create_plan(plan)
    updated = plan.with_status("running", now=2.0)
    await store.save_plan(updated)

    assert await store.get_plan("plan_1") == updated
    assert await store.list_plans("leader") == [updated]


@async_test
async def test_planner_runtime_creates_plan_and_adds_steps() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )

    plan = await runtime.create_plan(
        objective="Review agent-os.",
        owner_agent_id="leader",
    )
    updated = await runtime.add_step(
        plan.plan_id,
        instruction="Review planner boundaries.",
        required_capabilities=("review",),
        template_id="reviewer",
    )

    assert plan.plan_id == "plan_1"
    assert updated.steps[0].step_id == "step_1"
    assert updated.steps[0].template_id == "reviewer"
    assert updated.steps[0].required_capabilities == ("review",)


@async_test
async def test_planner_runtime_rejects_non_claimed_mutation_after_plan_revision_changes() -> (
    None
):
    class RacingPlanStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self.inject_conflict = True

        async def save_plan_if_unchanged(
            self,
            plan: PlanState,
            *,
            expected_revision: int,
        ) -> bool:
            if self.inject_conflict:
                self.inject_conflict = False
                current = await self.get_plan(plan.plan_id)
                assert current is not None
                await self.save_plan(current.with_status("running", now=9.0))
            return await super().save_plan_if_unchanged(
                plan,
                expected_revision=expected_revision,
            )

    store = RacingPlanStore()
    runtime = PlannerRuntime(
        store=store,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Guard concurrent planner writes.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="existing_step",
                    instruction="Existing work.",
                ),
            ),
        ),
    )

    with pytest.raises(PlanConflictError, match="plan changed before saving"):
        await runtime.add_step("plan_1", instruction="Should not overwrite the race.")

    persisted = await runtime.get_plan("plan_1")
    assert persisted.status == "running"
    assert [step.step_id for step in persisted.steps] == ["existing_step"]


@async_test
async def test_planner_runtime_rejects_mutation_when_store_has_no_cas_boundary() -> (
    None
):
    class LegacyPlanStore:
        def __init__(self) -> None:
            self.plans: dict[str, PlanState] = {}

        async def create_plan(self, plan: PlanState) -> None:
            self.plans[plan.plan_id] = plan

        async def save_plan(self, plan: PlanState) -> None:
            self.plans[plan.plan_id] = plan

        async def get_plan(self, plan_id: str) -> PlanState | None:
            return self.plans.get(plan_id)

        async def list_plans(
            self,
            owner_agent_id: str | None = None,
        ) -> list[PlanState]:
            plans = list(self.plans.values())
            if owner_agent_id is None:
                return plans
            return [plan for plan in plans if plan.owner_agent_id == owner_agent_id]

    store = LegacyPlanStore()
    runtime = PlannerRuntime(store=store)
    await store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Reject unguarded planner writes.",
            owner_agent_id="leader",
        ),
    )

    with pytest.raises(PlanConflictError, match="save_plan_if_unchanged"):
        await runtime.add_step("plan_1", instruction="This must not overwrite blindly.")

    assert store.plans["plan_1"].steps == ()


@async_test
async def test_planner_runtime_exposes_read_only_plan_queries() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    leader_plan = await runtime.create_plan(
        objective="Review leader plan.",
        owner_agent_id="leader",
        plan_id="leader_plan",
    )
    other_plan = await runtime.create_plan(
        objective="Review another plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )

    assert await runtime.get_plan("leader_plan") == leader_plan
    assert await runtime.list_plans("leader") == [leader_plan]
    assert await runtime.list_plans() == [leader_plan, other_plan]
    with pytest.raises(PlanNotFoundError):
        await runtime.get_plan("missing_plan")


@async_test
async def test_planner_runtime_can_scope_plan_queries_by_owner() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    await runtime.create_plan(
        objective="Review private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )

    with pytest.raises(PlanNotFoundError):
        await runtime.get_plan("other_plan", owner_agent_id="leader")


@async_test
async def test_planner_runtime_records_evidence_and_completes_plan() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = await runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    await runtime.add_step(plan.plan_id, instruction="Review planner.")

    evidence = await runtime.record_evidence(
        plan.plan_id,
        step_ids=("step_1",),
        kind="text",
        summary="Planner boundary is clean.",
        uri="memory://evidence/1",
        producer_agent_id="worker",
    )
    updated = await runtime.complete_step(
        plan.plan_id,
        "step_1",
        evidence_ids=(evidence.evidence_id,),
    )

    assert evidence.evidence_id == "evidence_1"
    assert updated.steps[0].status == "completed"
    assert updated.steps[0].evidence_ids == ("evidence_1",)
    assert updated.status == "completed"


@async_test
async def test_planner_runtime_persists_partial_step_completion() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = PlanState(
        plan_id="plan_1",
        objective="Review planner persistence.",
        owner_agent_id="leader",
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Collect evidence.",
                status="pending",
            ),
            PlanStep(
                step_id="step_2",
                instruction="Review evidence.",
                status="pending",
                depends_on=("step_1",),
            ),
        ),
    )
    await runtime.store.create_plan(plan)

    await runtime.complete_step("plan_1", "step_1")

    persisted = await runtime.get_plan("plan_1")
    assert persisted.steps[0].status == "completed"
    assert [step.step_id for step in await runtime.ready_steps("plan_1")] == ["step_2"]


@async_test
async def test_planner_runtime_rejects_unknown_evidence_kind() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = await runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")

    with pytest.raises(ValueError, match="unsupported evidence kind"):
        await runtime.record_evidence(
            plan.plan_id,
            kind="unknown",
            summary="Invalid evidence kind.",
        )


def test_plan_retry_policy_calculates_exponential_backoff() -> None:
    policy = PlanRetryPolicy(
        max_attempts=4,
        backoff_seconds=5.0,
        backoff_multiplier=2.0,
    )

    assert policy.delay_for_attempt(1) == 5.0
    assert policy.delay_for_attempt(2) == 10.0
    assert policy.delay_for_attempt(3) == 20.0

    with pytest.raises(ValueError, match="max_attempts"):
        PlanRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="backoff_seconds"):
        PlanRetryPolicy(backoff_seconds=-1.0)
    with pytest.raises(ValueError, match="backoff_multiplier"):
        PlanRetryPolicy(backoff_multiplier=0.5)
