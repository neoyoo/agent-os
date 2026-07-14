from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread, current_thread

import pytest

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskHandle,
)
from agentos.multi.planner import (
    EvidenceHandle,
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanDecomposition,
    PlanDecompositionGatePolicy,
    PlanConflictError,
    PlanDispatchReport,
    PlanNotFoundError,
    PlanRetryPolicy,
    PlanClaimRecord,
    PlanClaimResult,
    PlanClaimSweepReport,
    PlanClaimSweepSkip,
    PlanClaimedSchedulerTickReport,
    PlanClaimedSchedulerTickSkip,
    PlanAssignment,
    PlanDispatchAlreadySubmittedError,
    PlanSchedulerTickReport,
    PlannerStaleClaimSweepProfile,
    PlannerWorkerDispatchSupervisionProfile,
    PlannerRuntime,
    PlanState,
    PlanStep,
    PlanStepSpec,
    SubAgentTemplate,
)
from agentos.multi.postgres_tasks import PostgresTaskStore
from tests.multi.helpers import build_sync_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory
from tests.multi.test_postgres_task_store import FakeConnection


class ManualClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


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


def test_in_memory_plan_store_creates_and_updates_plan() -> None:
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

    store.create_plan(plan)
    updated = plan.with_status("running", now=2.0)
    store.save_plan(updated)

    assert store.get_plan("plan_1") == updated
    assert store.list_plans("leader") == [updated]


def test_planner_runtime_creates_plan_and_adds_steps() -> None:
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

    plan = runtime.create_plan(
        objective="Review agent-os.",
        owner_agent_id="leader",
    )
    updated = runtime.add_step(
        plan.plan_id,
        instruction="Review planner boundaries.",
        required_capabilities=("review",),
        template_id="reviewer",
    )

    assert plan.plan_id == "plan_1"
    assert updated.steps[0].step_id == "step_1"
    assert updated.steps[0].template_id == "reviewer"
    assert updated.steps[0].required_capabilities == ("review",)


def test_planner_runtime_rejects_non_claimed_mutation_after_plan_revision_changes() -> None:
    class RacingPlanStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self.inject_conflict = True

        def save_plan_if_unchanged(
            self,
            plan: PlanState,
            *,
            expected_revision: int,
        ) -> bool:
            if self.inject_conflict:
                self.inject_conflict = False
                current = self.get_plan(plan.plan_id)
                assert current is not None
                self.save_plan(current.with_status("running", now=9.0))
            return super().save_plan_if_unchanged(
                plan,
                expected_revision=expected_revision,
            )

    store = RacingPlanStore()
    runtime = PlannerRuntime(
        store=store,
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    store.create_plan(
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
        runtime.add_step("plan_1", instruction="Should not overwrite the race.")

    persisted = runtime.get_plan("plan_1")
    assert persisted.status == "running"
    assert [step.step_id for step in persisted.steps] == ["existing_step"]


def test_planner_runtime_rejects_mutation_when_store_has_no_cas_boundary() -> None:
    class LegacyPlanStore:
        def __init__(self) -> None:
            self.plans: dict[str, PlanState] = {}

        def create_plan(self, plan: PlanState) -> None:
            self.plans[plan.plan_id] = plan

        def save_plan(self, plan: PlanState) -> None:
            self.plans[plan.plan_id] = plan

        def get_plan(self, plan_id: str) -> PlanState | None:
            return self.plans.get(plan_id)

        def list_plans(self, owner_agent_id: str | None = None) -> list[PlanState]:
            plans = list(self.plans.values())
            if owner_agent_id is None:
                return plans
            return [plan for plan in plans if plan.owner_agent_id == owner_agent_id]

    store = LegacyPlanStore()
    runtime = PlannerRuntime(store=store)
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Reject unguarded planner writes.",
            owner_agent_id="leader",
        ),
    )

    with pytest.raises(PlanConflictError, match="save_plan_if_unchanged"):
        runtime.add_step("plan_1", instruction="This must not overwrite blindly.")

    assert store.plans["plan_1"].steps == ()


def test_planner_runtime_creates_plan_from_structured_decomposition() -> None:
    id_counts = {"plan": 0, "step": 0}

    def next_id(prefix: str) -> str:
        id_counts[prefix] += 1
        return f"{prefix}_{id_counts[prefix]}"

    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
                capabilities=("research",),
            ),
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review findings.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 10.0,
        id_factory=next_id,
    )
    decomposition = PlanDecomposition(
        objective="Review AgentOS planner architecture.",
        steps=(
            PlanStepSpec(
                instruction="Collect planner requirements and current gaps.",
                required_capabilities=("research",),
                template_id="researcher",
            ),
            PlanStepSpec(
                instruction="Review proposed plan for production risks.",
                required_capabilities=("review",),
                template_id="reviewer",
                depends_on=("step_1",),
            ),
        ),
    )

    plan = runtime.create_plan_from_decomposition(
        decomposition,
        owner_agent_id="leader",
        plan_id="plan_from_decomposition",
    )

    assert plan.plan_id == "plan_from_decomposition"
    assert plan.objective == "Review AgentOS planner architecture."
    assert plan.status == "draft"
    assert [step.step_id for step in plan.steps] == ["step_1", "step_2"]
    assert [step.template_id for step in plan.steps] == ["researcher", "reviewer"]
    assert [step.depends_on for step in plan.steps] == [(), ("step_1",)]
    assert [step.required_capabilities for step in plan.steps] == [
        ("research",),
        ("review",),
    ]


def test_planner_runtime_rejects_invalid_decomposition() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
        ),
    )

    with pytest.raises(ValueError, match="decomposition must include at least one step"):
        runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective="Empty plan.",
                steps=(),
            ),
            owner_agent_id="leader",
        )


def test_planner_runtime_validates_decomposition_without_creating_plan() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
        ),
        id_factory=lambda prefix: f"{prefix}_1",
    )

    report = runtime.validate_decomposition(
        PlanDecomposition(
            objective="Review planner decomposition policy.",
            steps=(
                PlanStepSpec(
                    instruction="Collect evidence.",
                    step_id="collect",
                    template_id="researcher",
                ),
                PlanStepSpec(
                    instruction="Write synthesis.",
                    step_id="write",
                    depends_on=("collect",),
                ),
            ),
        ),
    )

    assert report.ok is True
    assert report.errors == ()
    assert report.step_count == 2
    assert report.required_templates == ("researcher",)
    assert report.unknown_templates == ()
    assert report.as_dict()["ok"] is True
    assert runtime.list_plans() == []


def test_planner_runtime_validation_reports_decomposition_errors() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="known",
                name="Known",
                role="Known worker.",
            ),
        ),
    )

    report = runtime.validate_decomposition(
        PlanDecomposition(
            objective="Invalid plan.",
            steps=(
                PlanStepSpec(
                    instruction="Use missing template.",
                    step_id="first",
                    template_id="missing",
                ),
                PlanStepSpec(
                    instruction="Duplicate id.",
                    step_id="first",
                    depends_on=("unknown",),
                ),
            ),
        ),
    )

    assert report.ok is False
    assert report.step_count == 2
    assert report.required_templates == ("missing",)
    assert report.unknown_templates == ("missing",)
    assert any("unknown template" in error for error in report.errors)
    assert runtime.list_plans() == []


def test_planner_runtime_validation_does_not_consume_generated_step_ids() -> None:
    id_counts = {"plan": 0, "step": 0}

    def next_id(prefix: str) -> str:
        id_counts[prefix] += 1
        return f"{prefix}_{id_counts[prefix]}"

    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        id_factory=next_id,
    )
    decomposition = PlanDecomposition(
        objective="Validate before creation.",
        steps=(
            PlanStepSpec(instruction="First generated step."),
            PlanStepSpec(instruction="Second generated step."),
        ),
    )

    report = runtime.validate_decomposition(decomposition)
    plan = runtime.create_plan_from_decomposition(
        decomposition,
        owner_agent_id="leader",
    )

    assert report.ok is True
    assert [step.step_id for step in plan.steps] == ["step_1", "step_2"]


def test_planner_runtime_gates_valid_raw_decomposition_proposal() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review findings.",
            ),
        ),
    )

    report = runtime.gate_decomposition_proposal(
        {
            "objective": "Review AgentOS planner architecture.",
            "steps": [
                {
                    "step_id": "collect",
                    "instruction": "Collect planner requirements.",
                    "required_capabilities": ["research"],
                    "template_id": "researcher",
                },
                {
                    "step_id": "review",
                    "instruction": "Review planner risks.",
                    "required_capabilities": ["review"],
                    "template_id": "reviewer",
                    "depends_on": ["collect"],
                },
            ],
        },
    )

    assert report.accepted is True
    assert report.requires_approval is False
    assert report.errors == ()
    assert report.step_count == 2
    assert report.required_templates == ("researcher", "reviewer")
    assert report.unknown_templates == ()
    assert report.missing_templates == ()
    assert report.disallowed_templates == ()
    assert report.validation is not None
    assert report.validation.ok is True
    assert report.normalized_decomposition is not None
    assert report.normalized_decomposition.objective == (
        "Review AgentOS planner architecture."
    )
    assert report.normalized_decomposition.steps[1].depends_on == ("collect",)
    assert report.as_dict()["normalized_decomposition"] == {
        "objective": "Review AgentOS planner architecture.",
        "steps": [
            {
                "step_id": "collect",
                "instruction": "Collect planner requirements.",
                "required_capabilities": ("research",),
                "template_id": "researcher",
                "depends_on": (),
            },
            {
                "step_id": "review",
                "instruction": "Review planner risks.",
                "required_capabilities": ("review",),
                "template_id": "reviewer",
                "depends_on": ("collect",),
            },
        ],
    }
    assert runtime.list_plans() == []


def test_planner_runtime_gate_rejects_malformed_raw_proposal_without_mutation() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    report = runtime.gate_decomposition_proposal(
        {
            "objective": "Malformed plan.",
            "steps": [
                {
                    "instruction": "Valid step.",
                },
                "not an object",
            ],
        },
    )

    assert report.accepted is False
    assert report.validation is None
    assert report.normalized_decomposition is None
    assert report.step_count == 0
    assert report.errors == ("step 2 must be an object",)
    assert report.as_dict()["accepted"] is False
    assert runtime.list_plans() == []


def test_planner_runtime_gate_applies_template_policy_and_limits() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        templates=(
            SubAgentTemplate(
                template_id="researcher",
                name="Researcher",
                role="Find evidence.",
            ),
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review findings.",
            ),
        ),
    )

    report = runtime.gate_decomposition_proposal(
        {
            "objective": "Review AgentOS planner architecture.",
            "steps": [
                {
                    "step_id": "collect",
                    "instruction": "Collect planner requirements.",
                    "template_id": "researcher",
                },
                {
                    "step_id": "write",
                    "instruction": "Write summary.",
                },
                {
                    "step_id": "review",
                    "instruction": "Review planner risks.",
                    "template_id": "reviewer",
                },
            ],
        },
        policy=PlanDecompositionGatePolicy(
            max_steps=2,
            require_template=True,
            allowed_template_ids=("researcher",),
        ),
    )

    assert report.accepted is False
    assert report.step_count == 3
    assert report.missing_templates == ("write",)
    assert report.disallowed_templates == ("reviewer",)
    assert "decomposition exceeds max_steps: 3 > 2" in report.errors
    assert "step write requires a template_id" in report.errors
    assert "template not allowed: reviewer" in report.errors
    assert report.validation is not None
    assert report.validation.ok is True
    assert runtime.list_plans() == []


def test_planner_runtime_gate_blocks_until_required_approval_is_present() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())
    proposal = {
        "objective": "Review AgentOS planner architecture.",
        "steps": [
            {
                "instruction": "Collect planner requirements.",
            },
        ],
    }

    blocked = runtime.gate_decomposition_proposal(
        proposal,
        policy=PlanDecompositionGatePolicy(require_approval=True),
    )
    approved = runtime.gate_decomposition_proposal(
        proposal,
        policy=PlanDecompositionGatePolicy(
            require_approval=True,
            approved=True,
        ),
    )

    assert blocked.accepted is False
    assert blocked.requires_approval is True
    assert blocked.errors == ("decomposition approval is required",)
    assert blocked.as_dict()["requires_approval"] is True
    assert approved.accepted is True
    assert approved.requires_approval is False
    assert approved.errors == ()
    assert runtime.list_plans() == []


def test_planner_orchestration_profile_reports_missing_components() -> None:
    from agentos.multi.planner import PlannerOrchestrationDeploymentProfile

    profile = PlannerOrchestrationDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerOrchestrationDeploymentProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "decomposition_policy",
        "dag_scheduler",
        "worker_dispatch_loop",
        "compensation_policy",
        "plan_store",
        "worker_supervision",
    }
    assert "PlannerRuntime" in metadata["sdk_owned"]
    assert "production DAG scheduler" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_orchestration_profile_marks_ready_when_components_are_configured() -> None:
    from agentos.multi.planner import PlannerOrchestrationDeploymentProfile

    profile = PlannerOrchestrationDeploymentProfile(
        configured_components=(
            "decomposition_policy",
            "dag_scheduler",
            "worker_dispatch_loop",
            "compensation_policy",
            "plan_store",
            "worker_supervision",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_orchestration_profile_rejects_empty_names() -> None:
    from agentos.multi.planner import PlannerOrchestrationDeploymentProfile

    with pytest.raises(ValueError, match="probe_name"):
        PlannerOrchestrationDeploymentProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerOrchestrationDeploymentProfile(configured_components=("",))


def test_planner_decomposition_policy_profile_reports_missing_components() -> None:
    from agentos.multi.planner import PlannerDecompositionPolicyDeploymentProfile

    profile = PlannerDecompositionPolicyDeploymentProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerDecompositionPolicyDeploymentProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "prompt_policy",
        "output_schema",
        "validation_gate",
        "template_mapping_policy",
        "approval_policy",
        "model_routing_policy",
        "evaluation_policy",
        "trace_logging",
        "rollback_policy",
    }
    assert "PlanDecompositionValidationReport" in metadata["sdk_owned"]
    assert "LLM decomposition prompt/policy" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_decomposition_policy_profile_marks_ready_when_components_are_configured() -> None:
    from agentos.multi.planner import PlannerDecompositionPolicyDeploymentProfile

    profile = PlannerDecompositionPolicyDeploymentProfile(
        configured_components=(
            "prompt_policy",
            "output_schema",
            "validation_gate",
            "template_mapping_policy",
            "approval_policy",
            "model_routing_policy",
            "evaluation_policy",
            "trace_logging",
            "rollback_policy",
        ),
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_decomposition_policy_profile_rejects_empty_names() -> None:
    from agentos.multi.planner import PlannerDecompositionPolicyDeploymentProfile

    with pytest.raises(ValueError, match="probe_name"):
        PlannerDecompositionPolicyDeploymentProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerDecompositionPolicyDeploymentProfile(configured_components=("",))


def test_planner_llm_governance_profile_reports_missing_references() -> None:
    from agentos.multi.planner import PlannerLlmDecompositionGovernanceProfile

    profile = PlannerLlmDecompositionGovernanceProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert metadata["profile"] == "PlannerLlmDecompositionGovernanceProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert metadata["component_refs"] == {}
    assert metadata["evidence_refs"] == {}
    assert set(metadata["missing_components"]) == {
        "prompt_policy",
        "model_routing_policy",
        "approval_policy",
        "evaluation_policy",
        "trace_logging",
        "rollback_policy",
        "output_schema",
        "validation_gate",
        "template_mapping_policy",
        "budget_policy",
        "live_backend_verification",
    }
    assert "PlannerRuntime.gate_decomposition_proposal" in metadata["sdk_owned"]
    assert "prompt text and prompt review workflow" in metadata["deployment_owned"]
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_llm_governance_profile_marks_ready_with_policy_refs() -> None:
    from agentos.multi.planner import PlannerLlmDecompositionGovernanceProfile

    refs = {
        "prompt_policy": "policy://planner/prompts/v3",
        "model_routing_policy": "router://planner/decomposition/v2",
        "approval_policy": "approval://planner/decomposition/human-gate",
        "evaluation_policy": "eval://planner/decomposition/regression-suite",
        "trace_logging": "trace://planner/decomposition",
        "rollback_policy": "release://planner/decomposition/rollback",
        "output_schema": "schema://planner/decomposition/v1",
        "validation_gate": "gate://planner/decomposition/pre-create",
        "template_mapping_policy": "policy://planner/templates/v4",
        "budget_policy": "budget://planner/decomposition/default",
        "live_backend_verification": "probe://planner/decomposition/live",
    }
    profile = PlannerLlmDecompositionGovernanceProfile(
        component_refs=refs,
        evidence_refs={
            "prompt_policy": (
                "docs://reviews/planner-prompt-v3",
                "ci://planner-evals/2026-06-16",
            ),
            "evaluation_policy": ("artifact://eval-report/planner-v3",),
        },
        metadata={
            "release": "planner-decomposition-v3",
            "schema_version": 1,
            "approval_required": True,
        },
    )

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()

    assert profile.configured_component_names() == tuple(refs)
    assert metadata["ready"] is True
    assert metadata["missing_components"] == ()
    assert metadata["component_refs"] == refs
    assert metadata["evidence_refs"]["prompt_policy"] == (
        "docs://reviews/planner-prompt-v3",
        "ci://planner-evals/2026-06-16",
    )
    assert metadata["metadata"] == {
        "release": "planner-decomposition-v3",
        "schema_version": 1,
        "approval_required": True,
    }
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_llm_governance_profile_rejects_invalid_refs() -> None:
    from agentos.multi.planner import PlannerLlmDecompositionGovernanceProfile

    with pytest.raises(ValueError, match="probe_name"):
        PlannerLlmDecompositionGovernanceProfile(probe_name=" ")
    with pytest.raises(ValueError, match="component_refs"):
        PlannerLlmDecompositionGovernanceProfile(
            component_refs={"prompt_policy": " "},
        )
    with pytest.raises(ValueError, match="component_refs"):
        PlannerLlmDecompositionGovernanceProfile(
            component_refs={" ": "policy://planner"},
        )
    with pytest.raises(ValueError, match="evidence_refs"):
        PlannerLlmDecompositionGovernanceProfile(
            evidence_refs={"prompt_policy": ("",)},
        )
    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmDecompositionGovernanceProfile(
            metadata={"not_json": object()},
        )


def test_planner_llm_governance_evidence_gate_accepts_complete_external_evidence() -> None:
    from agentos.multi.planner import PlannerLlmGovernanceEvidenceRecord

    runtime = PlannerRuntime(store=InMemoryPlanStore())
    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        prompt_hash="sha256:prompt-v4",
        model_ref="model-router://planner/decomposition",
        model_version="gpt-5.1-planner-2026-06-16",
        approval_ref="approval://planner/decomposition/approved-123",
        approved=True,
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=True,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
        output_schema_ref="schema://planner/decomposition/v1",
        trace_ref="trace://planner/decomposition/run-123",
        budget_ref="budget://planner/decomposition/default",
        metadata={
            "release": "planner-decomposition-v4",
            "tenant": "internal-platform",
        },
    )

    report = runtime.gate_llm_governance_evidence(record)

    assert report.accepted is True
    assert report.block_plan_creation is False
    assert report.errors == ()
    assert report.missing_evidence == ()
    assert report.failed_evidence == ()
    assert report.record == record
    assert report.as_dict() == {
        "accepted": True,
        "block_plan_creation": False,
        "errors": (),
        "missing_evidence": (),
        "failed_evidence": (),
        "record": record.as_dict(),
        "metadata": {},
    }
    assert record.as_dict()["prompt_ref"] == "prompt://planner/decomposition/v4"
    assert record.as_dict()["metadata"] == {
        "release": "planner-decomposition-v4",
        "tenant": "internal-platform",
    }
    assert runtime.list_plans() == []


def test_planner_llm_governance_evidence_gate_blocks_missing_approval_evidence() -> None:
    from agentos.multi.planner import PlannerLlmGovernanceEvidenceRecord

    runtime = PlannerRuntime(store=InMemoryPlanStore())
    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        model_ref="model-router://planner/decomposition",
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=True,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
    )

    report = runtime.gate_llm_governance_evidence(record)

    assert report.accepted is False
    assert report.block_plan_creation is True
    assert report.missing_evidence == ("approval_evidence",)
    assert report.failed_evidence == ()
    assert report.errors == ("approval evidence is required",)
    assert report.as_dict()["block_plan_creation"] is True
    assert runtime.list_plans() == []


def test_planner_llm_governance_evidence_gate_blocks_failed_evaluation() -> None:
    from agentos.multi.planner import PlannerLlmGovernanceEvidenceRecord

    runtime = PlannerRuntime(store=InMemoryPlanStore())
    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        model_ref="model-router://planner/decomposition",
        approval_ref="approval://planner/decomposition/approved-123",
        approved=True,
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=False,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
    )

    report = runtime.gate_llm_governance_evidence(record)

    assert report.accepted is False
    assert report.block_plan_creation is True
    assert report.missing_evidence == ()
    assert report.failed_evidence == ("evaluation_evidence",)
    assert report.errors == ("evaluation evidence did not pass",)
    assert runtime.list_plans() == []


def test_planner_llm_governance_evidence_record_rejects_invalid_refs_and_metadata() -> None:
    from agentos.multi.planner import PlannerLlmGovernanceEvidenceRecord

    with pytest.raises(ValueError, match="proposal_id"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id=" ",
            objective="Review AgentOS planner architecture.",
        )
    with pytest.raises(ValueError, match="objective"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective=" ",
        )
    with pytest.raises(ValueError, match="prompt_ref"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            prompt_ref=" ",
        )
    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            metadata={"not_json": object()},
        )
    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            metadata={"secret_token": "sk-test"},
        )


def test_planner_llm_governance_evidence_payload_omits_raw_prompt_and_secrets() -> None:
    from agentos.multi.planner import PlannerLlmGovernanceEvidenceRecord

    with pytest.raises(ValueError, match="metadata"):
        PlannerLlmGovernanceEvidenceRecord(
            proposal_id="proposal_123",
            objective="Review AgentOS planner architecture.",
            metadata={"raw_prompt": "Create an internal production plan."},
        )

    record = PlannerLlmGovernanceEvidenceRecord(
        proposal_id="proposal_123",
        objective="Review AgentOS planner architecture.",
        prompt_ref="prompt://planner/decomposition/v4",
        prompt_hash="sha256:prompt-v4",
        model_ref="model-router://planner/decomposition",
        model_version="gpt-5.1-planner-2026-06-16",
        approval_ref="approval://planner/decomposition/approved-123",
        approved=True,
        evaluation_ref="eval://planner/decomposition/report-456",
        evaluation_passed=True,
        validation_ref="schema://planner/decomposition/validation-789",
        validation_passed=True,
        metadata={"release": "planner-decomposition-v4"},
    )
    payload = record.as_dict()

    serialized = str(payload)
    assert "raw_prompt" not in serialized
    assert "Create an internal production plan." not in serialized
    assert "sk-test" not in serialized
    assert payload["prompt_ref"] == "prompt://planner/decomposition/v4"


def test_planner_worker_dispatch_supervision_profile_reports_missing_components() -> None:
    profile = PlannerWorkerDispatchSupervisionProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()
    health = profile.health_payload()

    assert metadata["profile"] == "PlannerWorkerDispatchSupervisionProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "claimed_scheduler_tick_loop",
        "worker_process_lifecycle",
        "plan_claim_store",
        "scheduler_lock_policy",
        "stale_lease_recovery",
        "compensation_policy",
        "metrics_alerting",
        "live_backend_verification",
    }
    assert "PlanClaimedSchedulerTickReport" in metadata["sdk_owned"]
    assert "process supervisor or job runner" in metadata["deployment_owned"]
    assert health["status"] == "unstarted"
    assert health["ok"] is False
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_worker_dispatch_supervision_profile_summarizes_dispatch_reports() -> None:
    profile = PlannerWorkerDispatchSupervisionProfile(
        reports=(
            PlanClaimedSchedulerTickReport(
                worker_id="scheduler_a",
                claims=(
                    PlanClaimResult(status="claimed"),
                    PlanClaimResult(status="busy"),
                ),
                tick_reports=(
                    PlanSchedulerTickReport(plan_id="plan_1"),
                ),
                skipped=(
                    PlanClaimedSchedulerTickSkip(
                        plan_id="busy_plan",
                        reason="busy",
                    ),
                ),
                released_plan_ids=("plan_1",),
            ),
        ),
        configured_components=(
            "claimed_scheduler_tick_loop",
            "worker_process_lifecycle",
            "plan_claim_store",
            "scheduler_lock_policy",
            "stale_lease_recovery",
            "compensation_policy",
            "metrics_alerting",
            "live_backend_verification",
        ),
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "healthy"
    assert health["ok"] is True
    assert health["report_count"] == 1
    assert health["last_worker_id"] == "scheduler_a"
    assert health["tick_report_count"] == 1
    assert health["claim_count"] == 2
    assert health["claimed_count"] == 1
    assert health["busy_count"] == 1
    assert health["tick_failed_count"] == 0
    assert health["released_count"] == 1
    assert health["consecutive_failed_batches"] == 0
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True
    assert readiness["health_status"] == "healthy"


def test_planner_worker_dispatch_supervision_profile_fails_on_consecutive_tick_failures() -> None:
    profile = PlannerWorkerDispatchSupervisionProfile(
        reports=(
            PlanClaimedSchedulerTickReport(
                worker_id="scheduler_a",
                claims=(PlanClaimResult(status="claimed"),),
                skipped=(
                    PlanClaimedSchedulerTickSkip(
                        plan_id="plan_1",
                        reason="tick-failed",
                        detail="missing template",
                    ),
                ),
            ),
            PlanClaimedSchedulerTickReport(
                worker_id="scheduler_a",
                claims=(PlanClaimResult(status="claimed"),),
                skipped=(
                    PlanClaimedSchedulerTickSkip(
                        plan_id="plan_2",
                        reason="tick-failed",
                        detail="coordinator unavailable",
                    ),
                ),
            ),
        ),
        configured_components=(
            "claimed_scheduler_tick_loop",
            "worker_process_lifecycle",
            "plan_claim_store",
            "scheduler_lock_policy",
            "stale_lease_recovery",
            "compensation_policy",
            "metrics_alerting",
            "live_backend_verification",
        ),
        max_consecutive_failed_batches=1,
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "unhealthy"
    assert health["ok"] is False
    assert health["tick_failed_count"] == 2
    assert health["consecutive_failed_batches"] == 2
    assert health["last_tick_failed_plan_ids"] == ("plan_2",)
    assert readiness["status"] == "failed"
    assert readiness["health_status"] == "unhealthy"
    assert readiness["ok"] is False


def test_planner_worker_dispatch_supervision_profile_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerWorkerDispatchSupervisionProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerWorkerDispatchSupervisionProfile(configured_components=("",))
    with pytest.raises(ValueError, match="max_consecutive_failed_batches"):
        PlannerWorkerDispatchSupervisionProfile(max_consecutive_failed_batches=-1)


def test_planner_stale_claim_sweep_profile_reports_missing_components() -> None:
    profile = PlannerStaleClaimSweepProfile()

    metadata = profile.readiness_metadata()
    readiness = profile.readiness_check()
    health = profile.health_payload()

    assert metadata["profile"] == "PlannerStaleClaimSweepProfile"
    assert metadata["ready"] is False
    assert metadata["configured_components"] == ()
    assert set(metadata["missing_components"]) == {
        "stale_claim_sweep_schedule",
        "plan_claim_store",
        "scheduler_lock_policy",
        "sweep_safety_window",
        "metrics_alerting",
        "live_backend_verification",
    }
    assert "PlanClaimSweepReport" in metadata["sdk_owned"]
    assert "cron or scheduler" in metadata["deployment_owned"]
    assert health["status"] == "unstarted"
    assert health["ok"] is False
    assert readiness["status"] == "failed"
    assert readiness["ok"] is False


def test_planner_stale_claim_sweep_profile_summarizes_reports() -> None:
    report = PlanClaimSweepReport(
        checked_claims=(
            PlanClaimRecord(
                plan_id="expired_plan",
                owner_agent_id="leader",
                worker_id="scheduler_a",
                claimed_at=10.0,
                lease_expires_at=20.0,
                generation=1,
            ),
        ),
        released_claims=(
            PlanClaimRecord(
                plan_id="expired_plan",
                owner_agent_id="leader",
                worker_id="scheduler_a",
                claimed_at=10.0,
                lease_expires_at=20.0,
                generation=1,
            ),
        ),
        dry_run=False,
        now=30.0,
    )
    profile = PlannerStaleClaimSweepProfile(
        reports=(report,),
        configured_components=(
            "stale_claim_sweep_schedule",
            "plan_claim_store",
            "scheduler_lock_policy",
            "sweep_safety_window",
            "metrics_alerting",
            "live_backend_verification",
        ),
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "healthy"
    assert health["ok"] is True
    assert health["report_count"] == 1
    assert health["checked_count"] == 1
    assert health["released_count"] == 1
    assert health["skipped_count"] == 0
    assert health["last_released_plan_ids"] == ("expired_plan",)
    assert readiness["status"] == "ok"
    assert readiness["ok"] is True


def test_planner_stale_claim_sweep_profile_degrades_on_skips() -> None:
    report = PlanClaimSweepReport(
        checked_claims=(
            PlanClaimRecord(
                plan_id="expired_plan",
                owner_agent_id="leader",
                worker_id="scheduler_a",
                claimed_at=10.0,
                lease_expires_at=20.0,
                generation=1,
            ),
        ),
        skipped_claims=(
            PlanClaimSweepSkip(
                plan_id="expired_plan",
                reason="release-race",
                detail="claim changed before release",
            ),
        ),
        dry_run=False,
        now=30.0,
    )
    profile = PlannerStaleClaimSweepProfile(
        reports=(report,),
        configured_components=(
            "stale_claim_sweep_schedule",
            "plan_claim_store",
            "scheduler_lock_policy",
            "sweep_safety_window",
            "metrics_alerting",
            "live_backend_verification",
        ),
    )

    health = profile.health_payload()
    readiness = profile.readiness_check()

    assert health["status"] == "degraded"
    assert health["ok"] is False
    assert health["skipped_count"] == 1
    assert health["last_skipped_plan_ids"] == ("expired_plan",)
    assert readiness["status"] == "failed"
    assert readiness["health_status"] == "degraded"


def test_planner_stale_claim_sweep_profile_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="probe_name"):
        PlannerStaleClaimSweepProfile(probe_name=" ")
    with pytest.raises(ValueError, match="configured_components"):
        PlannerStaleClaimSweepProfile(configured_components=("",))


def test_planner_runtime_lists_ready_steps_after_dependencies_complete() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = PlanState(
        plan_id="plan_1",
        objective="Review DAG scheduling.",
        owner_agent_id="leader",
        steps=(
            PlanStep(
                step_id="step_1",
                instruction="Collect evidence.",
                status="completed",
            ),
            PlanStep(
                step_id="step_2",
                instruction="Review evidence.",
                status="pending",
                depends_on=("step_1",),
            ),
            PlanStep(
                step_id="step_3",
                instruction="Write final synthesis.",
                status="pending",
                depends_on=("step_2",),
            ),
            PlanStep(
                step_id="step_4",
                instruction="Already assigned.",
                status="assigned",
            ),
        ),
    )
    runtime.store.create_plan(plan)

    ready = runtime.ready_steps("plan_1")

    assert [step.step_id for step in ready] == ["step_2"]


def test_planner_runtime_rejects_cyclic_decomposition_dependencies() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        id_factory=lambda prefix: f"{prefix}_1",
    )

    with pytest.raises(ValueError, match="cycle"):
        runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective="Cyclic plan.",
                steps=(
                    PlanStepSpec(
                        instruction="First step.",
                        step_id="step_a",
                        depends_on=("step_b",),
                    ),
                    PlanStepSpec(
                        instruction="Second step.",
                        step_id="step_b",
                        depends_on=("step_a",),
                    ),
                ),
            ),
            owner_agent_id="leader",
        )
    with pytest.raises(KeyError):
        runtime.create_plan_from_decomposition(
            PlanDecomposition(
                objective="Unknown template.",
                steps=(
                    PlanStepSpec(
                        instruction="Use unknown template.",
                        template_id="missing",
                    ),
                ),
            ),
            owner_agent_id="leader",
        )


def test_planner_runtime_exposes_read_only_plan_queries() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    leader_plan = runtime.create_plan(
        objective="Review leader plan.",
        owner_agent_id="leader",
        plan_id="leader_plan",
    )
    other_plan = runtime.create_plan(
        objective="Review another plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )

    assert runtime.get_plan("leader_plan") == leader_plan
    assert runtime.list_plans("leader") == [leader_plan]
    assert runtime.list_plans() == [leader_plan, other_plan]
    with pytest.raises(PlanNotFoundError):
        runtime.get_plan("missing_plan")


def test_planner_runtime_can_scope_plan_queries_by_owner() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    runtime.create_plan(
        objective="Review private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )

    with pytest.raises(PlanNotFoundError):
        runtime.get_plan("other_plan", owner_agent_id="leader")


class FakeCoordinator:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, object]] = []

    def spawn(self, **kwargs: object) -> TaskHandle:
        self.spawn_calls.append(kwargs)
        return TaskHandle(
            task_id="task_spawn",
            mode="spawn",
            target_agent_id="subagent_1",
            status="queued",
        )

    def dispatch(self, **kwargs: object) -> TaskHandle:
        self.dispatch_calls.append(kwargs)
        return TaskHandle(
            task_id="task_dispatch",
            mode="dispatch",
            target_agent_id="expert_reviewer",
            status="queued",
        )


class RejectingPlanStore(InMemoryPlanStore):
    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        return False

    def save_plan_if_claimed(
        self,
        plan: PlanState,
        claim: PlanClaimRecord,
        *,
        expected_revision: int,
        now: float,
    ) -> bool:
        return False


class ConflictOncePlanStore(InMemoryPlanStore):
    def __init__(self) -> None:
        super().__init__()
        self.conflict_next_submitted_save = True

    def save_plan_if_unchanged(
        self,
        plan: PlanState,
        *,
        expected_revision: int,
    ) -> bool:
        if (
            self.conflict_next_submitted_save
            and any(
                assignment.dispatch_status == "submitted"
                for assignment in plan.assignments
            )
        ):
            self.conflict_next_submitted_save = False
            current = self.get_plan(plan.plan_id)
            assert current is not None
            super().save_plan(replace(current, updated_at=10.5))
            return False
        return super().save_plan_if_unchanged(
            plan,
            expected_revision=expected_revision,
        )


def test_planner_runtime_assigns_step_to_spawn_template() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
                allowed_tool_names=("read_file",),
                timeout_seconds=30,
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    plan = runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    assigned = runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    assert assigned.steps[0].status == "assigned"
    assert assigned.steps[0].task_id == "task_1"
    assert assigned.steps[0].assigned_agent_id == "subagent_1"
    assert assigned.assignments[0].task_id == "task_1"
    assert assigned.assignments[0].dispatch_status == "submitted"
    assert assigned.assignments[0].submitted_at == 10.0
    assert assigned.assignments[0].dispatch_error is None
    assert coordinator.spawn_calls[0]["task_id"] == "task_1"
    assert coordinator.spawn_calls[0]["allowed_tool_names"] == ("read_file",)
    assert coordinator.spawn_calls[0]["timeout_seconds"] == 30


def test_planner_runtime_retries_submitted_marker_after_revision_conflict() -> None:
    store = ConflictOncePlanStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 20.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    assigned = runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    persisted = runtime.get_plan(plan.plan_id)
    assert len(coordinator.spawn_calls) == 1
    assert assigned.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].submitted_at == 20.0
    assert store.conflict_next_submitted_save is False


def test_planner_runtime_fresh_assignment_duplicate_task_is_not_submitted() -> None:
    class DuplicateTaskCoordinator(FakeCoordinator):
        def spawn(self, **kwargs: object) -> TaskHandle:
            self.spawn_calls.append(kwargs)
            raise PlanDispatchAlreadySubmittedError(str(kwargs["task_id"]))

    store = InMemoryPlanStore()
    coordinator = DuplicateTaskCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 20.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review planner module.",
        template_id="reviewer",
    )

    with pytest.raises(PlanDispatchAlreadySubmittedError, match="task_1"):
        runtime.assign_step(plan.plan_id, "step_1", template_id="reviewer")

    persisted = runtime.get_plan(plan.plan_id)
    assert persisted.steps[0].status == "failed"
    assert persisted.assignments[0].dispatch_status == "failed"
    assert persisted.assignments[0].dispatch_error == "task_1"


def test_planner_runtime_assigns_step_to_dispatch_template() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                capabilities=("architecture-review",),
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(
        plan.plan_id,
        instruction="Review architecture.",
        required_capabilities=("architecture-review",),
        template_id="expert-reviewer",
    )

    assigned = runtime.assign_step(
        plan.plan_id,
        "step_1",
        template_id="expert-reviewer",
    )

    assert assigned.steps[0].task_id == "task_1"
    assert assigned.steps[0].assigned_agent_id == "expert_reviewer"
    assert coordinator.dispatch_calls[0]["task_id"] == "task_1"
    assert coordinator.dispatch_calls[0]["target_agent_id"] == "expert_reviewer"
    assert coordinator.dispatch_calls[0]["required_capabilities"] == (
        "architecture-review",
    )


def test_planner_runtime_does_not_dispatch_when_assignment_save_fails() -> None:
    coordinator = FakeCoordinator()
    store = RejectingPlanStore()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Guard planner dispatch side effects.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not dispatch if save fails.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    with pytest.raises(PlanConflictError, match="plan changed before saving"):
        runtime.assign_step("plan_1", "step_1", template_id="reviewer")

    assert coordinator.spawn_calls == []
    assert coordinator.dispatch_calls == []
    assert store.get_plan("plan_1").steps[0].status == "pending"  # type: ignore[union-attr]


def test_planner_runtime_dispatches_ready_steps_with_limit() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
                allowed_tool_names=("read_file",),
            ),
        ),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Collect evidence.",
                    status="completed",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Review evidence.",
                    status="pending",
                    depends_on=("step_1",),
                    template_id="reviewer",
                    required_capabilities=("review",),
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Review independent module.",
                    status="pending",
                    template_id="reviewer",
                    required_capabilities=("review",),
                ),
                PlanStep(
                    step_id="step_4",
                    instruction="Wait for step three.",
                    status="pending",
                    depends_on=("step_3",),
                    template_id="reviewer",
                ),
                PlanStep(
                    step_id="step_5",
                    instruction="Already assigned.",
                    status="assigned",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps("plan_1", limit=1)

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_2"]
    assert report.skipped == ()
    assert persisted.steps[1].status == "assigned"
    assert persisted.steps[1].task_id is not None
    assert persisted.steps[2].status == "pending"
    assert persisted.steps[3].status == "pending"
    assert persisted.steps[4].status == "assigned"
    assert len(coordinator.spawn_calls) == 1
    assert coordinator.spawn_calls[0]["task_id"] == persisted.steps[1].task_id


def test_planner_runtime_dispatch_ready_steps_reports_template_skips() -> None:
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Missing template.",
                    status="pending",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Unknown template.",
                    status="pending",
                    template_id="missing",
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Known default template.",
                    status="pending",
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps(
        "plan_1",
        default_template_id="reviewer",
    )

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == [
        "step_1",
        "step_3",
    ]
    assert [(skip.step_id, skip.reason) for skip in report.skipped] == [
        ("step_2", "unknown-template"),
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[1].status == "pending"
    assert persisted.steps[2].status == "assigned"


class FailingCoordinator(FakeCoordinator):
    def dispatch(self, **kwargs: object) -> TaskHandle:
        self.dispatch_calls.append(kwargs)
        raise RuntimeError("expert unavailable")


def test_planner_runtime_dispatch_ready_steps_reports_coordinator_failure() -> None:
    coordinator = FailingCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                capabilities=("architecture-review",),
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Dispatch to unavailable expert.",
                    status="pending",
                    template_id="expert-reviewer",
                    required_capabilities=("architecture-review",),
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert report.assigned == ()
    assert [(skip.step_id, skip.reason, skip.detail) for skip in report.skipped] == [
        ("step_1", "dispatch-failed", "expert unavailable"),
    ]
    assert persisted.steps[0].status == "failed"
    assert persisted.steps[0].task_id is not None
    assert persisted.steps[0].error == "expert unavailable"
    assert persisted.assignments[0].dispatch_status == "failed"
    assert persisted.assignments[0].dispatch_error == "expert unavailable"


def test_planner_runtime_recovers_pending_assignment_after_restart() -> None:
    store = InMemoryPlanStore()
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover a saved but unsubmitted planner assignment.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Dispatch after restart.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_1",
                    assigned_agent_id="expert_reviewer",
                    required_capabilities=("architecture-review",),
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_1",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                capabilities=("architecture-review",),
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 20.0,
    )

    report = runtime.recover_pending_dispatches("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert coordinator.dispatch_calls == [
        {
            "instruction": "Dispatch after restart.",
            "required_capabilities": ("architecture-review",),
            "parent_agent_id": "leader",
            "target_agent_id": "expert_reviewer",
            "allowed_tool_names": (),
            "timeout_seconds": 300,
            "task_id": "task_1",
        },
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].submitted_at == 20.0
    assert persisted.assignments[0].dispatch_error is None


def test_planner_runtime_treats_duplicate_task_recovery_as_submitted() -> None:
    class DuplicateTaskCoordinator(FakeCoordinator):
        def dispatch(self, **kwargs: object) -> TaskHandle:
            self.dispatch_calls.append(kwargs)
            raise PlanDispatchAlreadySubmittedError(str(kwargs["task_id"]))

    store = InMemoryPlanStore()
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover a task that the coordinator already accepted.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover duplicate task.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = DuplicateTaskCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 20.0,
    )

    report = runtime.recover_pending_dispatches("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert coordinator.dispatch_calls[0]["task_id"] == "task_existing"
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_error is None


def test_planner_runtime_treats_spawn_duplicate_task_recovery_as_submitted() -> None:
    class DuplicateSpawnCoordinator(FakeCoordinator):
        def spawn(self, **kwargs: object) -> TaskHandle:
            self.spawn_calls.append(kwargs)
            raise PlanDispatchAlreadySubmittedError(str(kwargs["task_id"]))

    store = InMemoryPlanStore()
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover a spawned task accepted before restart.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover duplicate spawned task.",
                    status="assigned",
                    template_id="reviewer",
                    task_id="task_existing",
                    assigned_agent_id="subagent_existing",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="reviewer",
                    task_id="task_existing",
                    target_agent_id="subagent_existing",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = DuplicateSpawnCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review architecture.",
            ),
        ),
        clock=lambda: 20.0,
    )

    report = runtime.recover_pending_dispatches("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert coordinator.spawn_calls[0]["task_id"] == "task_existing"
    assert coordinator.spawn_calls[0]["child_agent_id"] == "subagent_existing"
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_error is None


def test_planner_recovery_accepts_postgres_duplicate_from_agent_coordinator() -> None:
    connection = FakeConnection()
    task_store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_store=task_store,
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="leader",
            name="Leader",
            description="Plan owner.",
            capabilities=("coordinate",),
        ),
        build_sync_agent_with_response("leader"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert_reviewer",
            name="Expert Reviewer",
            description="Architecture reviewer.",
            capabilities=("architecture-review",),
            max_concurrent_tasks=2,
        ),
        build_sync_agent_with_response("expert"),
    )
    accepted = coordinator.dispatch(
        instruction="Previously submitted task.",
        required_capabilities=("architecture-review",),
        parent_agent_id="leader",
        target_agent_id="expert_reviewer",
        task_id="task_existing",
    )
    assert accepted.task_id == "task_existing"
    store = InMemoryPlanStore()
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover duplicate task from durable store.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover duplicate task.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                    required_capabilities=("architecture-review",),
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                capabilities=("architecture-review",),
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 20.0,
    )

    try:
        report = runtime.recover_pending_dispatches("plan_1")
    finally:
        coordinator.spawn_executor.shutdown()

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == ["step_1"]
    assert report.skipped == ()
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.assignments[0].dispatch_status == "submitted"
    assert persisted.assignments[0].dispatch_error is None
    existing = task_store.get("task_existing")
    assert existing is not None
    assert existing.parent_agent_id == "leader"
    assert existing.target_agent_id == "expert_reviewer"
    assert len(coordinator.inbox.collect("expert_reviewer")) == 1
    assert connection.rollbacks == 1


def test_planner_runtime_dispatch_ready_steps_recovers_before_new_assignments() -> None:
    store = InMemoryPlanStore()
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover existing assignment before creating more work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover first.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Dispatch second.",
                    status="pending",
                    template_id="expert-reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 20.0,
        id_factory=lambda prefix: f"{prefix}_new",
    )

    report = runtime.dispatch_ready_steps("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert [assignment.step_id for assignment in report.assigned] == [
        "step_1",
        "step_2",
    ]
    assert [assignment.task_id for assignment in report.assigned] == [
        "task_existing",
        "task_new",
    ]
    assert [
        assignment.dispatch_status for assignment in persisted.assignments
    ] == ["submitted", "submitted"]
    assert [call["task_id"] for call in coordinator.dispatch_calls] == [
        "task_existing",
        "task_new",
    ]


def test_planner_runtime_claimed_scheduler_tick_recovers_pending_assignment() -> None:
    store = InMemoryPlanStore()
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        claim_store=claim_store,
        clock=lambda: 20.0,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Recover pending assignment through claimed scheduler.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recover through scheduler.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )

    summaries = runtime.schedulable_plans(owner_agent_id="leader")
    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
    )

    persisted = runtime.get_plan("plan_1")
    assert [summary.plan_id for summary in summaries] == ["plan_1"]
    assert summaries[0].reasons == ("pending-dispatch",)
    assert summaries[0].ready_step_ids == ()
    assert [tick.plan_id for tick in report.tick_reports] == ["plan_1"]
    assert [assignment.step_id for assignment in report.tick_reports[0].dispatch.assigned] == [
        "step_1",
    ]
    assert coordinator.dispatch_calls[0]["task_id"] == "task_existing"
    assert persisted.assignments[0].dispatch_status == "submitted"


def test_planner_runtime_claimed_scheduler_stops_when_claim_expires_before_recovery_dispatch() -> None:
    class ExpiringDuringRecoveryStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self._reads = 0

        def get_plan(self, plan_id: str) -> PlanState | None:
            self._reads += 1
            if self._reads == 3:
                clock.value = 22.0
            return super().get_plan(plan_id)

    clock = ManualClock(20.0)
    store = ExpiringDuringRecoveryStore()
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Do not recover pending dispatch after lease expiry.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Recovery should keep the pending outbox item.",
                    status="assigned",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    assigned_agent_id="expert_reviewer",
                ),
            ),
            assignments=(
                PlanAssignment(
                    plan_id="plan_1",
                    step_id="step_1",
                    template_id="expert-reviewer",
                    task_id="task_existing",
                    target_agent_id="expert_reviewer",
                    created_at=10.0,
                    dispatch_status="pending",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
    )

    persisted = runtime.get_plan("plan_1")
    assert [claim.status for claim in report.claims] == ["claimed"]
    assert report.tick_reports == ()
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    assert coordinator.dispatch_calls == []
    assert persisted.assignments[0].dispatch_status == "pending"


def test_planner_runtime_dispatch_failure_does_not_overwrite_concurrent_step_completion() -> None:
    class CompletingFailingCoordinator(FakeCoordinator):
        def dispatch(self, **kwargs: object) -> TaskHandle:
            self.dispatch_calls.append(kwargs)
            current = store.get_plan("plan_1")
            assert current is not None
            completed_step = replace(
                current.steps[0],
                status="completed",
                error=None,
            )
            store.save_plan(
                replace(
                    current,
                    steps=(completed_step,),
                    status="completed",
                    updated_at=11.0,
                ),
            )
            raise RuntimeError("expert unavailable")

    store = InMemoryPlanStore()
    coordinator = CompletingFailingCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="expert-reviewer",
                name="Expert Reviewer",
                role="Review architecture.",
                target_agent_id="expert_reviewer",
            ),
        ),
        clock=lambda: 10.0,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Dispatch ready planner steps.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Dispatch to unavailable expert.",
                    status="pending",
                    template_id="expert-reviewer",
                ),
            ),
        ),
    )

    report = runtime.dispatch_ready_steps("plan_1")

    persisted = runtime.get_plan("plan_1")
    assert [(skip.step_id, skip.reason, skip.detail) for skip in report.skipped] == [
        ("step_1", "dispatch-failed", "expert unavailable"),
    ]
    assert persisted.status == "completed"
    assert persisted.steps[0].status == "completed"
    assert persisted.steps[0].error is None


def test_planner_runtime_records_evidence_and_completes_plan() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(plan.plan_id, instruction="Review planner.")

    evidence = runtime.record_evidence(
        plan.plan_id,
        step_ids=("step_1",),
        kind="text",
        summary="Planner boundary is clean.",
        uri="memory://evidence/1",
        producer_agent_id="worker",
    )
    updated = runtime.complete_step(
        plan.plan_id,
        "step_1",
        evidence_ids=(evidence.evidence_id,),
    )

    assert evidence.evidence_id == "evidence_1"
    assert updated.steps[0].status == "completed"
    assert updated.steps[0].evidence_ids == ("evidence_1",)
    assert updated.status == "completed"


def test_planner_runtime_persists_partial_step_completion() -> None:
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
    runtime.store.create_plan(plan)

    runtime.complete_step("plan_1", "step_1")

    persisted = runtime.get_plan("plan_1")
    assert persisted.steps[0].status == "completed"
    assert [step.step_id for step in runtime.ready_steps("plan_1")] == ["step_2"]


def test_planner_runtime_rejects_unknown_evidence_kind() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")

    with pytest.raises(ValueError, match="unsupported evidence kind"):
        runtime.record_evidence(
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


def test_planner_runtime_records_failed_step_and_schedules_retry() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(
            max_attempts=3,
            backoff_seconds=5.0,
            backoff_multiplier=2.0,
        ),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review SDK.", owner_agent_id="leader")
    runtime.add_step(plan.plan_id, instruction="Review planner recovery.")

    clock.value = 20.0
    updated = runtime.fail_step(
        plan.plan_id,
        "step_1",
        error="worker crashed",
    )

    failed_step = updated.steps[0]
    assert updated.status == "running"
    assert failed_step.status == "failed"
    assert failed_step.error == "worker crashed"
    assert failed_step.attempts == 1
    assert failed_step.last_failed_at == 20.0
    assert failed_step.retry_status == "scheduled"
    assert failed_step.next_retry_at == 25.0
    assert failed_step.retry_exhausted_at is None


def test_planner_runtime_lists_due_retryable_steps_and_resets_for_retry() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review retry flow.", owner_agent_id="leader")
    runtime.add_step(plan.plan_id, instruction="Run worker.")
    clock.value = 20.0
    runtime.fail_step(plan.plan_id, "step_1", error="temporary outage")

    clock.value = 24.0
    assert runtime.retryable_steps(plan.plan_id) == ()

    clock.value = 25.0
    assert [step.step_id for step in runtime.retryable_steps(plan.plan_id)] == [
        "step_1",
    ]

    retried = runtime.retry_step(plan.plan_id, "step_1")
    retried_step = retried.steps[0]
    assert retried.status == "running"
    assert retried_step.status == "pending"
    assert retried_step.error is None
    assert retried_step.attempts == 1
    assert retried_step.last_failed_at == 20.0
    assert retried_step.next_retry_at is None
    assert retried_step.retry_status is None
    assert retried_step.retry_exhausted_at is None


def test_planner_runtime_exhausts_failed_step_retries() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=1, backoff_seconds=5.0),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    plan = runtime.create_plan(objective="Review exhausted retry.", owner_agent_id="leader")
    runtime.add_step(plan.plan_id, instruction="Run worker.")

    clock.value = 20.0
    updated = runtime.fail_step(plan.plan_id, "step_1", error="permanent failure")

    failed_step = updated.steps[0]
    assert updated.status == "failed"
    assert failed_step.status == "failed"
    assert failed_step.attempts == 1
    assert failed_step.retry_status == "exhausted"
    assert failed_step.next_retry_at is None
    assert failed_step.retry_exhausted_at == 20.0
    assert runtime.retryable_steps(plan.plan_id) == ()
    with pytest.raises(ValueError, match="not retryable"):
        runtime.retry_step(plan.plan_id, "step_1")


def test_planner_runtime_scheduler_tick_retries_due_steps_before_dispatch() -> None:
    clock = ManualClock(10.0)
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
                capabilities=("review",),
            ),
        ),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Run one planner scheduler pass.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Collect evidence.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    last_failed_at=5.0,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                    error="temporary outage",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Review evidence.",
                    status="pending",
                    template_id="reviewer",
                    depends_on=("step_1",),
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Review independent module.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.scheduler_tick("plan_1", dispatch_limit=2)

    persisted = runtime.get_plan("plan_1")
    assert isinstance(report, PlanSchedulerTickReport)
    assert [reset.step_id for reset in report.retry_resets] == ["step_1"]
    assert report.retry_resets[0].attempts == 1
    assert [assignment.step_id for assignment in report.dispatch.assigned] == [
        "step_1",
        "step_3",
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[0].error is None
    assert persisted.steps[0].retry_status is None
    assert persisted.steps[1].status == "pending"
    assert persisted.steps[2].status == "assigned"
    assert len(coordinator.spawn_calls) == 2


def test_planner_runtime_scheduler_tick_respects_retry_and_dispatch_limits() -> None:
    clock = ManualClock(10.0)
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Bound scheduler work.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Retry first.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Retry second.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
                PlanStep(
                    step_id="step_3",
                    instruction="Independent ready step.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.scheduler_tick(
        "plan_1",
        retry_limit=1,
        dispatch_limit=1,
    )

    persisted = runtime.get_plan("plan_1")
    assert [reset.step_id for reset in report.retry_resets] == ["step_1"]
    assert [assignment.step_id for assignment in report.dispatch.assigned] == [
        "step_1",
    ]
    assert persisted.steps[0].status == "assigned"
    assert persisted.steps[1].status == "failed"
    assert persisted.steps[1].retry_status == "scheduled"
    assert persisted.steps[2].status == "pending"


def test_planner_runtime_scheduler_tick_rejects_invalid_limits() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(ValueError, match="retry_limit"):
        runtime.scheduler_tick("plan_1", retry_limit=0)
    with pytest.raises(ValueError, match="dispatch_limit"):
        runtime.scheduler_tick("plan_1", dispatch_limit=0)


def test_planner_runtime_lists_schedulable_plans_by_ready_or_retryable_steps() -> None:
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="ready_plan",
            objective="Has ready work.",
            owner_agent_id="leader",
            status="draft",
            updated_at=3.0,
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run ready work.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="retry_plan",
            objective="Has due retry work.",
            owner_agent_id="leader",
            status="running",
            updated_at=5.0,
            steps=(
                PlanStep(
                    step_id="retry_step",
                    instruction="Retry failed work.",
                    status="failed",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="blocked_plan",
            objective="Blocked by dependency.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="blocked_step",
                    instruction="Wait for dependency.",
                    status="pending",
                    depends_on=("missing_completion",),
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="assigned_plan",
            objective="Already assigned.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="assigned_step",
                    instruction="Already assigned.",
                    status="assigned",
                ),
            ),
        ),
    )

    summaries = runtime.schedulable_plans(owner_agent_id="leader")

    assert [summary.plan_id for summary in summaries] == [
        "ready_plan",
        "retry_plan",
    ]
    assert summaries[0].ready_step_ids == ("ready_step",)
    assert summaries[0].retryable_step_ids == ()
    assert summaries[0].reasons == ("ready-steps",)
    assert summaries[0].as_dict() == {
        "plan_id": "ready_plan",
        "owner_agent_id": "leader",
        "status": "draft",
        "ready_step_ids": ["ready_step"],
        "retryable_step_ids": [],
        "reasons": ["ready-steps"],
        "updated_at": 3.0,
    }
    assert summaries[1].ready_step_ids == ()
    assert summaries[1].retryable_step_ids == ("retry_step",)
    assert summaries[1].reasons == ("due-retries",)


def test_planner_runtime_schedulable_plans_filters_owner_status_and_limit() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    runtime.store.create_plan(
        PlanState(
            plan_id="first_running",
            objective="First running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="first_step",
                    instruction="Run first.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="other_owner",
            objective="Other owner plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="draft_plan",
            objective="Draft plan.",
            owner_agent_id="leader",
            status="draft",
            steps=(
                PlanStep(
                    step_id="draft_step",
                    instruction="Run draft.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="completed_plan",
            objective="Completed plan should not be selected by default.",
            owner_agent_id="leader",
            status="completed",
            steps=(
                PlanStep(
                    step_id="completed_ready",
                    instruction="Would be ready if status allowed.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="second_running",
            objective="Second running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="second_step",
                    instruction="Run second.",
                    status="pending",
                ),
            ),
        ),
    )

    running_only = runtime.schedulable_plans(
        owner_agent_id="leader",
        statuses=("running",),
        limit=1,
    )
    with_completed = runtime.schedulable_plans(
        owner_agent_id="leader",
        statuses=("completed",),
    )

    assert [summary.plan_id for summary in running_only] == ["first_running"]
    assert [summary.plan_id for summary in with_completed] == ["completed_plan"]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": -1}, "limit"),
        ({"statuses": ()}, "statuses"),
        ({"statuses": ("running", "unknown")}, "unsupported plan status"),
        ({"statuses": ("",)}, "unsupported plan status"),
    ],
)
def test_planner_runtime_schedulable_plans_rejects_invalid_filters(
    kwargs: dict[str, object],
    match: str,
) -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(ValueError, match=match):
        runtime.schedulable_plans(**kwargs)


def test_in_memory_plan_claim_store_claims_releases_and_expires_leases() -> None:
    store = InMemoryPlanClaimStore()

    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_a",
        lease_seconds=30.0,
        now=10.0,
    )
    busy = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_b",
        lease_seconds=30.0,
        now=20.0,
    )
    renewed = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_a",
        lease_seconds=30.0,
        now=25.0,
    )

    assert first.as_dict() == {
        "status": "claimed",
        "claim": {
            "plan_id": "plan_1",
            "owner_agent_id": "leader",
            "worker_id": "worker_a",
            "claimed_at": 10.0,
            "lease_expires_at": 40.0,
            "generation": 1,
        },
        "existing_claim": None,
    }
    assert busy.status == "busy"
    assert busy.claim is None
    assert busy.existing_claim is not None
    assert busy.existing_claim.worker_id == "worker_a"
    assert renewed.status == "claimed"
    assert renewed.claim is not None
    assert renewed.claim.worker_id == "worker_a"
    assert renewed.claim.claimed_at == 25.0
    assert renewed.claim.lease_expires_at == 55.0
    assert renewed.claim.generation == 2
    assert store.release_plan(plan_id="plan_1", worker_id="worker_b") is False
    assert store.release_plan(plan_id="plan_1", worker_id="worker_a") is True
    assert store.get_claim("plan_1") is None

    expired = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_a",
        lease_seconds=5.0,
        now=100.0,
    )
    takeover = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="worker_b",
        lease_seconds=5.0,
        now=106.0,
    )

    assert expired.status == "claimed"
    assert takeover.status == "claimed"
    assert takeover.claim is not None
    assert takeover.claim.worker_id == "worker_b"
    assert takeover.claim.generation == 2


def test_in_memory_plan_claim_store_does_not_renew_active_claim_for_other_owner() -> None:
    store = InMemoryPlanClaimStore()
    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="shared_worker",
        lease_seconds=30.0,
        now=10.0,
    )

    other_owner = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="other",
        worker_id="shared_worker",
        lease_seconds=30.0,
        now=20.0,
    )

    assert other_owner.status == "busy"
    assert other_owner.existing_claim == first.claim
    assert store.get_claim("plan_1") == first.claim


def test_in_memory_plan_claim_store_release_can_require_matching_owner() -> None:
    store = InMemoryPlanClaimStore()
    first = store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="shared_worker",
        lease_seconds=30.0,
        now=10.0,
    )

    assert (
        store.release_plan(
            plan_id="plan_1",
            worker_id="shared_worker",
            owner_agent_id="other",
        )
        is False
    )
    assert store.get_claim("plan_1") == first.claim
    assert (
        store.release_plan(
            plan_id="plan_1",
            worker_id="shared_worker",
            owner_agent_id="leader",
        )
        is True
    )
    assert store.get_claim("plan_1") is None


def test_planner_runtime_sweep_expired_claims_supports_dry_run_and_release() -> None:
    claim_store = InMemoryPlanClaimStore()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=claim_store,
        clock=lambda: 30.0,
    )
    claim_store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    claim_store.claim_plan(
        plan_id="active_plan",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=40.0,
        now=10.0,
    )
    claim_store.claim_plan(
        plan_id="other_owner",
        owner_agent_id="other",
        worker_id="scheduler_c",
        lease_seconds=10.0,
        now=10.0,
    )

    dry_run = runtime.sweep_expired_claims(
        owner_agent_id="leader",
        dry_run=True,
    )
    release = runtime.sweep_expired_claims(owner_agent_id="leader")

    assert dry_run.as_dict() == {
        "now": 30.0,
        "owner_agent_id": "leader",
        "dry_run": True,
        "checked_claims": [
            {
                "plan_id": "expired_plan",
                "owner_agent_id": "leader",
                "worker_id": "scheduler_a",
                "claimed_at": 10.0,
                "lease_expires_at": 20.0,
                "generation": 1,
            },
        ],
        "released_claims": [],
        "skipped_claims": [],
    }
    assert claim_store.get_claim("expired_plan") is None
    assert claim_store.get_claim("active_plan") is not None
    assert claim_store.get_claim("other_owner") is not None
    assert release.dry_run is False
    assert [claim.plan_id for claim in release.checked_claims] == ["expired_plan"]
    assert [claim.plan_id for claim in release.released_claims] == ["expired_plan"]
    assert release.skipped_claims == ()


def test_plan_claim_store_release_expired_claim_is_generation_safe() -> None:
    claim_store = InMemoryPlanClaimStore()
    original = claim_store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=10.0,
        now=10.0,
    )
    assert original.claim is not None
    takeover = claim_store.claim_plan(
        plan_id="expired_plan",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        now=30.0,
    )
    assert takeover.claim is not None

    released = claim_store.release_expired_claim(
        original.claim,
        now=30.0,
    )

    assert released is False
    current = claim_store.get_claim("expired_plan")
    assert current is not None
    assert current.worker_id == "scheduler_b"
    assert current.generation == 2


def test_planner_runtime_sweep_expired_claims_rejects_invalid_arguments() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
    )

    with pytest.raises(ValueError, match="owner_agent_id"):
        runtime.sweep_expired_claims(owner_agent_id=" ")
    with pytest.raises(ValueError, match="limit"):
        runtime.sweep_expired_claims(limit=0)


def test_planner_runtime_sweep_expired_claims_requires_sweep_store() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(RuntimeError, match="claim_store"):
        runtime.sweep_expired_claims()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {
                "plan_id": "",
                "owner_agent_id": "leader",
                "worker_id": "worker",
                "lease_seconds": 10.0,
                "now": 1.0,
            },
            "plan_id",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": " ",
                "worker_id": "worker",
                "lease_seconds": 10.0,
                "now": 1.0,
            },
            "owner_agent_id",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "leader",
                "worker_id": "",
                "lease_seconds": 10.0,
                "now": 1.0,
            },
            "worker_id",
        ),
        (
            {
                "plan_id": "plan_1",
                "owner_agent_id": "leader",
                "worker_id": "worker",
                "lease_seconds": 0.0,
                "now": 1.0,
            },
            "lease_seconds",
        ),
    ],
)
def test_in_memory_plan_claim_store_rejects_invalid_claims(
    kwargs: dict[str, object],
    match: str,
) -> None:
    store = InMemoryPlanClaimStore()

    with pytest.raises(ValueError, match=match):
        store.claim_plan(**kwargs)


def test_planner_runtime_claims_schedulable_plans_with_owner_status_limit() -> None:
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="first_running",
            objective="First running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="first_step",
                    instruction="Run first.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="second_running",
            objective="Second running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="second_step",
                    instruction="Run second.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="draft_plan",
            objective="Draft plan.",
            owner_agent_id="leader",
            status="draft",
            steps=(
                PlanStep(
                    step_id="draft_step",
                    instruction="Run draft.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="other_owner",
            objective="Other owner plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other.",
                    status="pending",
                ),
            ),
        ),
    )

    first_claims = runtime.claim_schedulable_plans(
        owner_agent_id="leader",
        statuses=("running",),
        worker_id="scheduler_a",
        lease_seconds=20.0,
        limit=1,
    )
    second_worker_claims = runtime.claim_schedulable_plans(
        owner_agent_id="leader",
        statuses=("running",),
        worker_id="scheduler_b",
        lease_seconds=20.0,
        limit=2,
    )

    assert [result.status for result in first_claims] == ["claimed"]
    assert first_claims[0].claim is not None
    assert first_claims[0].claim.as_dict() == {
        "plan_id": "first_running",
        "owner_agent_id": "leader",
        "worker_id": "scheduler_a",
        "claimed_at": 10.0,
        "lease_expires_at": 30.0,
        "generation": 1,
    }
    assert [result.status for result in second_worker_claims] == [
        "busy",
        "claimed",
    ]
    assert second_worker_claims[0].existing_claim is not None
    assert second_worker_claims[0].existing_claim.worker_id == "scheduler_a"
    assert second_worker_claims[1].claim is not None
    assert second_worker_claims[1].claim.plan_id == "second_running"
    assert runtime.claim_store is not None
    assert runtime.claim_store.get_claim("draft_plan") is None
    assert runtime.claim_store.get_claim("other_owner") is None


def test_planner_runtime_claim_schedulable_plans_requires_claim_store() -> None:
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    with pytest.raises(RuntimeError, match="claim_store"):
        runtime.claim_schedulable_plans(
            worker_id="scheduler",
            lease_seconds=20.0,
        )


def test_planner_runtime_claimed_scheduler_tick_stops_when_claim_changes_before_save() -> None:
    class RacingPlanStore(InMemoryPlanStore):
        def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            clock.value = 11.0
            claim_store.claim_plan(
                plan_id=plan.plan_id,
                owner_agent_id=plan.owner_agent_id,
                worker_id="scheduler_b",
                lease_seconds=30.0,
                now=11.0,
            )
            return super().save_plan_if_claimed(
                plan,
                claim,
                expected_revision=expected_revision,
                now=11.0,
            )

    clock = ManualClock(10.0)
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    store = RacingPlanStore()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Claim-guard local scheduler mutation.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not save after claim is lost.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert plan.steps[0].status == "pending"
    assert coordinator.spawn_calls == []
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    current_claim = claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_b"


def test_planner_runtime_claimed_scheduler_requires_atomic_claim_guarded_save() -> None:
    class CompareOnlyPlanStore(InMemoryPlanStore):
        save_plan_if_claimed = None  # type: ignore[assignment]

    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    store = CompareOnlyPlanStore()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=lambda: 10.0,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Require atomic claim-guarded scheduler saves.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not dispatch through CAS fallback.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert plan.steps[0].status == "pending"
    assert coordinator.spawn_calls == []
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]


def test_planner_runtime_claimed_scheduler_stops_when_claim_expires_before_dispatch() -> None:
    class ExpiringAfterSavePlanStore(InMemoryPlanStore):
        def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            saved = super().save_plan_if_claimed(
                plan,
                claim,
                expected_revision=expected_revision,
                now=now,
            )
            if saved:
                clock.value = 12.0
            return saved

    clock = ManualClock(10.0)
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    store = ExpiringAfterSavePlanStore()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Do not dispatch after claim expiry.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not dispatch after lease expiry.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert [claim.status for claim in report.claims] == ["claimed"]
    assert report.tick_reports == ()
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    assert plan.steps[0].status == "assigned"
    assert plan.assignments[0].dispatch_status == "pending"
    assert coordinator.spawn_calls == []


def test_planner_runtime_claim_context_is_isolated_between_threads() -> None:
    class RecordingPlanStore(InMemoryPlanStore):
        def __init__(self) -> None:
            super().__init__()
            self.claims_by_thread: dict[str, PlanClaimRecord] = {}

        def save_plan_if_claimed(
            self,
            plan: PlanState,
            claim: PlanClaimRecord,
            *,
            expected_revision: int,
            now: float,
        ) -> bool:
            self.claims_by_thread[current_thread().name] = claim
            return True

        def save_plan_if_unchanged(
            self,
            plan: PlanState,
            *,
            expected_revision: int,
        ) -> bool:
            raise AssertionError("claimed scheduler save fell back to CAS")

    class CoordinatedPlannerRuntime(PlannerRuntime):
        def scheduler_tick(
            self,
            plan_id: str,
            *,
            default_template_id: str | None = None,
            retry_limit: int | None = None,
            dispatch_limit: int | None = None,
        ) -> PlanSchedulerTickReport:
            if current_thread().name == "scheduler-a":
                thread_a_context_ready.set()
                assert thread_b_saved.wait(timeout=5)
            else:
                assert thread_a_context_ready.wait(timeout=5)
            record = self._require_plan_record(plan_id)
            self._save_plan(
                record.plan.with_status("running", now=10.0),
                expected_revision=record.revision,
            )
            if current_thread().name == "scheduler-b":
                thread_b_saved.set()
                assert thread_a_saved.wait(timeout=5)
            else:
                thread_a_saved.set()
            return PlanSchedulerTickReport(
                plan_id=plan_id,
                retry_resets=(),
                dispatch=PlanDispatchReport(plan_id=plan_id),
            )

    store = RecordingPlanStore()
    runtime = CoordinatedPlannerRuntime(store=store, clock=lambda: 10.0)
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Keep overlapping scheduler claims isolated.",
            owner_agent_id="leader",
            status="running",
        ),
    )
    claim_a = PlanClaimRecord(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        claimed_at=1.0,
        lease_expires_at=31.0,
        generation=1,
    )
    claim_b = PlanClaimRecord(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_b",
        claimed_at=2.0,
        lease_expires_at=32.0,
        generation=2,
    )
    thread_a_context_ready = Event()
    thread_b_saved = Event()
    thread_a_saved = Event()
    errors: list[BaseException] = []

    def run_claim(claim: PlanClaimRecord) -> None:
        try:
            runtime._run_scheduler_tick_with_claim(
                claim,
                default_template_id=None,
                retry_limit=None,
                dispatch_limit=None,
            )
        except BaseException as error:
            errors.append(error)

    thread_a = Thread(target=run_claim, args=(claim_a,), name="scheduler-a")
    thread_b = Thread(target=run_claim, args=(claim_b,), name="scheduler-b")

    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert errors == []
    assert store.claims_by_thread["scheduler-a"] == claim_a
    assert store.claims_by_thread["scheduler-b"] == claim_b


def test_in_memory_plan_store_claim_guarded_save_requires_exact_live_claim() -> None:
    store = InMemoryPlanStore()
    claim_store = InMemoryPlanClaimStore()
    original = PlanState(
        plan_id="plan_1",
        objective="Claim-guard local plan store.",
        owner_agent_id="leader",
        status="running",
    )
    store.create_plan(original)
    record = store.get_plan_record("plan_1")
    assert record is not None
    claim = claim_store.claim_plan(
        plan_id="plan_1",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        now=10.0,
    ).claim
    assert claim is not None

    assert store.save_plan_if_claimed(
        original.with_status("completed", now=11.0),
        claim,
        expected_revision=record.revision,
        now=11.0,
    ) is False

    store.bind_claim_store(claim_store)
    assert store.save_plan_if_claimed(
        original.with_status("completed", now=11.0),
        claim,
        expected_revision=record.revision,
        now=11.0,
    ) is True

    fresh = store.get_plan_record("plan_1")
    assert fresh is not None
    assert fresh.plan.status == "completed"
    assert store.save_plan_if_claimed(
        fresh.plan.with_status("failed", now=31.0),
        claim,
        expected_revision=fresh.revision,
        now=31.0,
    ) is False


def test_planner_runtime_claimed_scheduler_tick_skips_busy_plans() -> None:
    coordinator = FakeCoordinator()
    claim_store = InMemoryPlanClaimStore()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="busy_plan",
            objective="Already leased work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="busy_step",
                    instruction="Should not be dispatched.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="free_plan",
            objective="Claimable work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="free_step",
                    instruction="Should be dispatched.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    claim_store.claim_plan(
        plan_id="busy_plan",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        default_template_id="reviewer",
    )

    busy_plan = runtime.get_plan("busy_plan")
    free_plan = runtime.get_plan("free_plan")
    assert isinstance(report, PlanClaimedSchedulerTickReport)
    assert report.worker_id == "scheduler_b"
    assert [claim.status for claim in report.claims] == ["busy", "claimed"]
    assert [tick.plan_id for tick in report.tick_reports] == ["free_plan"]
    assert report.skipped[0].plan_id == "busy_plan"
    assert report.skipped[0].reason == "busy"
    assert report.skipped[0].claim_result.existing_claim is not None
    assert report.skipped[0].claim_result.existing_claim.worker_id == "scheduler_a"
    assert report.released_plan_ids == ()
    assert busy_plan.steps[0].status == "pending"
    assert free_plan.steps[0].status == "assigned"
    assert len(coordinator.spawn_calls) == 1


def test_planner_runtime_claimed_scheduler_tick_dispatches_only_after_claim_guarded_save() -> None:
    class RacingClaimStore(InMemoryPlanClaimStore):
        def __init__(self) -> None:
            super().__init__()
            self._raced = False

        def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
            if plan_id == "plan_1" and not self._raced:
                self._raced = True
                clock.value = 11.0
                self.claim_plan(
                    plan_id="plan_1",
                    owner_agent_id="leader",
                    worker_id="scheduler_b",
                    lease_seconds=30.0,
                    now=11.0,
                )
            return super().get_claim(plan_id)

    clock = ManualClock(10.0)
    claim_store = RacingClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Race-safe scheduler work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not be saved by stale scheduler.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=1.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert [claim.status for claim in report.claims] == ["claimed"]
    assert report.tick_reports == ()
    assert [skip.reason for skip in report.skipped] == ["claim-lost"]
    assert plan.steps[0].status == "assigned"
    assert plan.steps[0].task_id is not None
    assert plan.assignments[0].dispatch_status == "pending"
    assert coordinator.spawn_calls == []
    current_claim = claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_b"


def test_planner_runtime_claimed_scheduler_tick_dispatches_only_after_revision_guarded_save() -> None:
    class RacingCoordinator(FakeCoordinator):
        def spawn(self, **kwargs: object) -> TaskHandle:
            current = store.get_plan("plan_1")
            assert current is not None
            store.save_plan(current.with_status("failed", now=11.0))
            return super().spawn(**kwargs)

    store = InMemoryPlanStore()
    claim_store = InMemoryPlanClaimStore()
    coordinator = RacingCoordinator()
    runtime = PlannerRuntime(
        store=store,
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=lambda: 10.0,
    )
    store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Race-safe scheduler work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Should not be saved over a concurrent update.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        default_template_id="reviewer",
    )

    plan = runtime.get_plan("plan_1")
    assert len(report.tick_reports) == 1
    assert report.skipped == ()
    assert plan.status == "failed"
    assert plan.steps[0].status == "assigned"
    assert plan.steps[0].task_id is not None
    assert coordinator.spawn_calls[0]["task_id"] == plan.steps[0].task_id
    current_claim = claim_store.get_claim("plan_1")
    assert current_claim is not None
    assert current_claim.worker_id == "scheduler_a"


def test_planner_runtime_claimed_scheduler_tick_can_release_claims_after_tick() -> None:
    clock = ManualClock(10.0)
    claim_store = InMemoryPlanClaimStore()
    coordinator = FakeCoordinator()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=coordinator,
        templates=(
            SubAgentTemplate(
                template_id="reviewer",
                name="Reviewer",
                role="Review code.",
            ),
        ),
        claim_store=claim_store,
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Claim, tick, and release.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Run first worker.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )

    first = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        default_template_id="reviewer",
        release_after_tick=True,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_2",
            objective="Later work.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_2",
                    instruction="Run second worker.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    clock.value = 11.0
    second = runtime.claimed_scheduler_tick(
        owner_agent_id="leader",
        worker_id="scheduler_b",
        lease_seconds=30.0,
        default_template_id="reviewer",
        release_after_tick=True,
    )

    assert first.released_plan_ids == ("plan_1",)
    assert claim_store.get_claim("plan_1") is None
    assert [tick.plan_id for tick in second.tick_reports] == ["plan_2"]
    assert second.claims[0].claim is not None
    assert second.claims[0].claim.worker_id == "scheduler_b"
    assert second.released_plan_ids == ("plan_2",)
    assert claim_store.get_claim("plan_2") is None


def test_planner_runtime_release_after_tick_supports_legacy_claim_stores() -> None:
    class LegacyReleaseClaimStore:
        def __init__(self, delegate: InMemoryPlanClaimStore) -> None:
            self.delegate = delegate

        def claim_plan(
            self,
            *,
            plan_id: str,
            owner_agent_id: str,
            worker_id: str,
            lease_seconds: float,
            now: float,
        ) -> PlanClaimResult:
            return self.delegate.claim_plan(
                plan_id=plan_id,
                owner_agent_id=owner_agent_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )

        def release_plan(self, *, plan_id: str, worker_id: str) -> bool:
            return self.delegate.release_plan(plan_id=plan_id, worker_id=worker_id)

        def get_claim(self, plan_id: str) -> PlanClaimRecord | None:
            return self.delegate.get_claim(plan_id)

    delegate = InMemoryPlanClaimStore()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=LegacyReleaseClaimStore(delegate),  # type: ignore[arg-type]
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="plan_1",
            objective="Release with a legacy claim store.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Run legacy release.",
                    status="pending",
                ),
            ),
        ),
    )

    report = runtime.claimed_scheduler_tick(
        worker_id="scheduler_a",
        lease_seconds=30.0,
        release_after_tick=True,
    )

    assert report.released_plan_ids == ("plan_1",)
    assert delegate.get_claim("plan_1") is None
