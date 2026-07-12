from __future__ import annotations

import json

import pytest

from agentos.capabilities import ToolRegistry
from agentos.multi import TaskHandle
from agentos.multi.planner import (
    AllowAllPlannerToolAuthorizationPolicy,
    InMemoryPlanClaimStore,
    InMemoryPlanStore,
    PlanNotFoundError,
    PlanRetryPolicy,
    PlanState,
    PlanStep,
    PlannerToolAuthorizationError,
    PlannerRuntime,
    PlannerTools,
    SubAgentTemplate,
)


class FakeCoordinator:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []

    def spawn(self, **kwargs: object) -> TaskHandle:
        self.spawn_calls.append(kwargs)
        return TaskHandle(
            task_id="task_spawn",
            mode="spawn",
            target_agent_id="subagent_1",
            status="queued",
        )

    def dispatch(self, **kwargs: object) -> TaskHandle:
        raise AssertionError("dispatch should not be called")


class StatusRuntimeSpy(PlannerRuntime):
    def __init__(self) -> None:
        super().__init__(store=InMemoryPlanStore(), clock=lambda: 10.0)
        self.get_plan_calls: list[tuple[str, str | None]] = []
        self.list_plan_calls: list[str | None] = []

    def get_plan(self, plan_id: str, *, owner_agent_id: str | None = None):
        self.get_plan_calls.append((plan_id, owner_agent_id))
        return super().get_plan(plan_id, owner_agent_id=owner_agent_id)

    def list_plans(self, owner_agent_id: str | None = None):
        self.list_plan_calls.append(owner_agent_id)
        return super().list_plans(owner_agent_id)


class ManualClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_planner_tools_register_external_tools() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore())

    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    names = [
        spec["function"]["name"]
        for spec in registry.provider_tool_specs()
    ]
    assert names == [
        "plan_create",
        "plan_gate_decomposition_proposal",
        "plan_create_from_decomposition",
        "plan_add_step",
        "plan_assign_step",
        "plan_ready_steps",
        "plan_schedulable_plans",
        "plan_claim_schedulable_plans",
        "plan_claimed_scheduler_tick",
        "plan_dispatch_ready_steps",
        "plan_scheduler_tick",
        "plan_fail_step",
        "plan_retryable_steps",
        "plan_retry_step",
        "plan_record_evidence",
        "plan_complete_step",
        "plan_status",
    ]


def test_planner_tools_default_policy_denies_scheduler_and_dispatch_tools() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        coordinator=FakeCoordinator(),
    )
    runtime.create_plan(
        objective="Review guarded planner tools.",
        owner_agent_id="leader",
        plan_id="plan_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    with pytest.raises(PlannerToolAuthorizationError, match="plan_claim_schedulable_plans"):
        registry.get("plan_claim_schedulable_plans").handler(
            {
                "worker_id": "scheduler",
                "lease_seconds": 30.0,
            },
        )
    with pytest.raises(PlannerToolAuthorizationError, match="plan_dispatch_ready_steps"):
        registry.get("plan_dispatch_ready_steps").handler({"plan_id": "plan_1"})
    with pytest.raises(PlannerToolAuthorizationError, match="plan_scheduler_tick"):
        registry.get("plan_scheduler_tick").handler({"plan_id": "plan_1"})


def test_planner_tools_allow_all_policy_allows_scheduler_tool_boundary() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(PlanStep(step_id="ready_step", instruction="Run work."),),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        registry.get("plan_claim_schedulable_plans").handler(
            {
                "worker_id": "scheduler_a",
                "lease_seconds": 20.0,
            },
        ),
    )

    assert payload["claims"][0]["status"] == "claimed"


def test_planner_tools_create_add_and_status_handlers() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    created = json.loads(
        registry.get("plan_create").handler(
            {
                "objective": "Review agent-os planner tools.",
            },
        ),
    )
    updated = json.loads(
        registry.get("plan_add_step").handler(
            {
                "plan_id": "plan_1",
                "instruction": "Review tool registration.",
                "required_capabilities": ["architecture-review"],
            },
        ),
    )
    one_plan = json.loads(
        registry.get("plan_status").handler({"plan_id": "plan_1"}),
    )
    owner_plans = json.loads(
        registry.get("plan_status").handler({}),
    )

    assert created["plan_id"] == "plan_1"
    assert created["owner_agent_id"] == "leader"
    assert updated["steps"][0]["step_id"] == "step_1"
    assert updated["steps"][0]["required_capabilities"] == ["architecture-review"]
    assert one_plan["plan_id"] == "plan_1"
    assert owner_plans["plans"][0]["plan_id"] == "plan_1"


def test_planner_tools_create_from_decomposition_handler() -> None:
    registry = ToolRegistry()
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
                role="Review plan.",
                capabilities=("review",),
            ),
        ),
        clock=lambda: 10.0,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    created = json.loads(
        registry.get("plan_create_from_decomposition").handler(
            {
                "objective": "Review AgentOS planner architecture.",
                "plan_id": "plan_decomposed",
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
                        "depends_on": ["collect"],
                        "required_capabilities": ["review"],
                        "template_id": "reviewer",
                    },
                ],
            },
        ),
    )

    assert created["plan_id"] == "plan_decomposed"
    assert created["steps"][0]["instruction"] == "Collect planner requirements."
    assert created["steps"][0]["required_capabilities"] == ["research"]
    assert created["steps"][0]["template_id"] == "researcher"
    assert created["steps"][1]["depends_on"] == ["collect"]
    assert created["steps"][1]["template_id"] == "reviewer"


def test_planner_tools_gate_decomposition_proposal_handler_is_read_only() -> None:
    registry = ToolRegistry()
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
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    report = json.loads(
        registry.get("plan_gate_decomposition_proposal").handler(
            {
                "proposal": {
                    "objective": "Review AgentOS planner architecture.",
                    "steps": [
                        {
                            "step_id": "collect",
                            "instruction": "Collect planner requirements.",
                            "template_id": "researcher",
                        },
                    ],
                },
                "policy": {
                    "max_steps": 2,
                    "require_template": True,
                    "allowed_template_ids": ["researcher"],
                },
                "metadata": {
                    "source": "unit-test",
                },
            },
        ),
    )

    assert report["accepted"] is True
    assert report["errors"] == []
    assert report["step_count"] == 1
    assert report["required_templates"] == ["researcher"]
    assert report["normalized_decomposition"]["objective"] == (
        "Review AgentOS planner architecture."
    )
    assert report["metadata"] == {"source": "unit-test"}
    assert runtime.list_plans() == []


def test_planner_tools_gate_decomposition_proposal_handler_reports_policy_errors() -> None:
    registry = ToolRegistry()
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
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    report = json.loads(
        registry.get("plan_gate_decomposition_proposal").handler(
            {
                "proposal": {
                    "objective": "Review AgentOS planner architecture.",
                    "steps": [
                        {
                            "step_id": "collect",
                            "instruction": "Collect planner requirements.",
                            "template_id": "researcher",
                        },
                    ],
                },
                "policy": {
                    "require_approval": True,
                },
            },
        ),
    )

    assert report["accepted"] is False
    assert report["requires_approval"] is True
    assert report["errors"] == ["decomposition approval is required"]
    assert report["normalized_decomposition"] is None
    assert runtime.list_plans() == []


def test_planner_tools_ready_steps_handler_respects_dependencies() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    plan = runtime.create_plan(
        objective="Review DAG planner tools.",
        owner_agent_id="leader",
        plan_id="plan_1",
    )
    runtime.store.save_plan(
        plan.__class__(
            plan_id=plan.plan_id,
            objective=plan.objective,
            owner_agent_id=plan.owner_agent_id,
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
                    instruction="Write summary.",
                    status="pending",
                    depends_on=("step_2",),
                ),
            ),
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        ),
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    ready = json.loads(
        registry.get("plan_ready_steps").handler({"plan_id": "plan_1"}),
    )

    assert [step["step_id"] for step in ready["ready_steps"]] == ["step_2"]
    assert ready["ready_steps"][0]["depends_on"] == ["step_1"]
    assert ready["ready_steps"][0]["template_id"] == "reviewer"


def test_planner_tools_schedulable_plans_handler_returns_owner_scoped_summaries() -> None:
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            updated_at=5.0,
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run leader work.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_draft",
            objective="Leader draft plan.",
            owner_agent_id="leader",
            status="draft",
            updated_at=6.0,
            steps=(
                PlanStep(
                    step_id="draft_step",
                    instruction="Run draft work.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="other_running",
            objective="Other owner running plan.",
            owner_agent_id="other",
            status="running",
            updated_at=7.0,
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other owner work.",
                    status="pending",
                ),
            ),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        registry.get("plan_schedulable_plans").handler(
            {
                "statuses": ["running"],
                "limit": 5,
            },
        ),
    )

    assert payload == {
        "plans": [
            {
                "plan_id": "leader_running",
                "owner_agent_id": "leader",
                "status": "running",
                "ready_step_ids": ["ready_step"],
                "retryable_step_ids": [],
                "reasons": ["ready-steps"],
                "updated_at": 5.0,
            },
        ],
    }


def test_planner_tools_schedulable_plans_handler_reports_due_retries() -> None:
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="retry_plan",
            objective="Retry due plan.",
            owner_agent_id="leader",
            status="running",
            updated_at=8.0,
            steps=(
                PlanStep(
                    step_id="retry_step",
                    instruction="Retry due work.",
                    status="failed",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
            ),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(registry.get("plan_schedulable_plans").handler({}))

    assert payload["plans"][0]["plan_id"] == "retry_plan"
    assert payload["plans"][0]["retryable_step_ids"] == ["retry_step"]
    assert payload["plans"][0]["reasons"] == ["due-retries"]


def test_planner_tools_claim_schedulable_plans_handler_returns_owner_scoped_claims() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run leader work.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="other_running",
            objective="Other owner running plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Run other owner work.",
                    status="pending",
                ),
            ),
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        registry.get("plan_claim_schedulable_plans").handler(
            {
                "worker_id": "scheduler_a",
                "lease_seconds": 20.0,
                "statuses": ["running"],
                "limit": 1,
            },
        ),
    )

    assert payload == {
        "claims": [
            {
                "status": "claimed",
                "claim": {
                    "plan_id": "leader_running",
                    "owner_agent_id": "leader",
                    "worker_id": "scheduler_a",
                    "claimed_at": 10.0,
                    "lease_expires_at": 30.0,
                    "generation": 1,
                },
                "existing_claim": None,
            },
        ],
    }
    assert runtime.claim_store is not None
    assert runtime.claim_store.get_claim("other_running") is None


def test_planner_tools_claim_schedulable_plans_handler_reports_busy_claims() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_running",
            objective="Leader running plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="ready_step",
                    instruction="Run leader work.",
                    status="pending",
                ),
            ),
        ),
    )
    runtime.claim_store.claim_plan(
        plan_id="leader_running",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=20.0,
        now=10.0,
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        registry.get("plan_claim_schedulable_plans").handler(
            {
                "worker_id": "scheduler_b",
                "lease_seconds": 20.0,
                "statuses": ["running"],
                "limit": 1,
            },
        ),
    )

    assert payload["claims"][0]["status"] == "busy"
    assert payload["claims"][0]["claim"] is None
    assert payload["claims"][0]["existing_claim"]["worker_id"] == "scheduler_a"


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        (
            {
                "worker_id": "",
                "lease_seconds": 20.0,
            },
            "worker_id",
        ),
        (
            {
                "worker_id": "scheduler",
                "lease_seconds": 0,
            },
            "lease_seconds",
        ),
    ],
)
def test_planner_tools_claim_schedulable_plans_handler_rejects_invalid_arguments(
    arguments: dict[str, object],
    match: str,
) -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(ValueError, match=match):
        registry.get("plan_claim_schedulable_plans").handler(arguments)


def test_planner_tools_claimed_scheduler_tick_handler_ticks_only_claimed_plans() -> None:
    registry = ToolRegistry()
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
        clock=lambda: 10.0,
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_busy",
            objective="Busy leader plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="busy_step",
                    instruction="Do not dispatch this.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="leader_free",
            objective="Free leader plan.",
            owner_agent_id="leader",
            status="running",
            steps=(
                PlanStep(
                    step_id="free_step",
                    instruction="Dispatch this.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    runtime.store.create_plan(
        PlanState(
            plan_id="other_free",
            objective="Other owner plan.",
            owner_agent_id="other",
            status="running",
            steps=(
                PlanStep(
                    step_id="other_step",
                    instruction="Do not select this.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
        ),
    )
    claim_store.claim_plan(
        plan_id="leader_busy",
        owner_agent_id="leader",
        worker_id="scheduler_a",
        lease_seconds=30.0,
        now=10.0,
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    payload = json.loads(
        registry.get("plan_claimed_scheduler_tick").handler(
            {
                "worker_id": "scheduler_b",
                "lease_seconds": 30.0,
                "default_template_id": "reviewer",
                "release_after_tick": True,
            },
        ),
    )

    assert payload["worker_id"] == "scheduler_b"
    assert [claim["status"] for claim in payload["claims"]] == [
        "busy",
        "claimed",
    ]
    assert payload["skipped"][0]["plan_id"] == "leader_busy"
    assert payload["skipped"][0]["reason"] == "busy"
    assert payload["tick_reports"][0]["plan_id"] == "leader_free"
    assert payload["released_plan_ids"] == ["leader_free"]
    assert runtime.get_plan("leader_busy").steps[0].status == "pending"
    assert runtime.get_plan("leader_free").steps[0].status == "assigned"
    assert runtime.get_plan("other_free").steps[0].status == "pending"
    assert claim_store.get_claim("leader_free") is None
    assert len(coordinator.spawn_calls) == 1


def test_planner_tools_claimed_scheduler_tick_handler_rejects_invalid_arguments() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        claim_store=InMemoryPlanClaimStore(),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(ValueError, match="worker_id"):
        registry.get("plan_claimed_scheduler_tick").handler(
            {
                "worker_id": "",
                "lease_seconds": 30.0,
            },
        )
    with pytest.raises(ValueError, match="lease_seconds"):
        registry.get("plan_claimed_scheduler_tick").handler(
            {
                "worker_id": "scheduler",
                "lease_seconds": 0.0,
            },
        )


def test_planner_tools_status_cannot_read_another_owner_plan() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        clock=lambda: 10.0,
    )
    runtime.create_plan(
        objective="Other owner's private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    with pytest.raises(PlanNotFoundError):
        registry.get("plan_status").handler({"plan_id": "other_plan"})


def test_planner_tools_cannot_mutate_another_owner_plan() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        coordinator=FakeCoordinator(),
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
    runtime.create_plan(
        objective="Other owner's private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )
    runtime.add_step("other_plan", instruction="Keep this step private.")
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(PlanNotFoundError):
        registry.get("plan_add_step").handler(
            {
                "plan_id": "other_plan",
                "instruction": "Mutate private plan.",
            },
        )
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_assign_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
                "template_id": "reviewer",
            },
        )
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_record_evidence").handler(
            {
                "plan_id": "other_plan",
                "step_ids": ["step_1"],
                "kind": "text",
                "summary": "Mutate evidence.",
            },
        )
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_complete_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
            },
        )
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_fail_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
                "error": "Mutate failure.",
            },
        )
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_retryable_steps").handler({"plan_id": "other_plan"})
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_retry_step").handler(
            {
                "plan_id": "other_plan",
                "step_id": "step_1",
            },
        )
    with pytest.raises(PlanNotFoundError):
        registry.get("plan_dispatch_ready_steps").handler({"plan_id": "other_plan"})

    private_plan = runtime.get_plan("other_plan")
    assert len(private_plan.steps) == 1
    assert private_plan.steps[0].status == "pending"
    assert private_plan.evidence == ()


def test_planner_tools_status_uses_planner_runtime_query_boundary() -> None:
    registry = ToolRegistry()
    runtime = StatusRuntimeSpy()
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    registry.get("plan_create").handler(
        {
            "objective": "Review planner status boundary.",
            "plan_id": "plan_1",
        },
    )

    registry.get("plan_status").handler({"plan_id": "plan_1"})
    registry.get("plan_status").handler({})

    assert runtime.get_plan_calls == [("plan_1", "leader")]
    assert runtime.list_plan_calls == ["leader"]


def test_planner_tools_assign_record_evidence_and_complete_handlers() -> None:
    registry = ToolRegistry()
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
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    registry.get("plan_create").handler({"objective": "Review planner tools."})
    registry.get("plan_add_step").handler(
        {
            "plan_id": "plan_1",
            "instruction": "Review implementation.",
            "template_id": "reviewer",
        },
    )

    assigned = json.loads(
        registry.get("plan_assign_step").handler(
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "template_id": "reviewer",
            },
        ),
    )
    evidence = json.loads(
        registry.get("plan_record_evidence").handler(
            {
                "plan_id": "plan_1",
                "step_ids": ["step_1"],
                "kind": "text",
                "summary": "Planner tools preserve runtime boundary.",
                "uri": "memory://evidence/1",
                "producer_agent_id": "subagent_1",
                "metadata": {"source": "unit-test"},
            },
        ),
    )
    completed = json.loads(
        registry.get("plan_complete_step").handler(
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "evidence_ids": ["evidence_1"],
            },
        ),
    )

    assert assigned["steps"][0]["status"] == "assigned"
    assert assigned["assignments"][0]["task_id"] == "task_1"
    assert coordinator.spawn_calls[0]["task_id"] == "task_1"
    assert coordinator.spawn_calls[0]["allowed_tool_names"] == ("read_file",)
    assert evidence["evidence_id"] == "evidence_1"
    assert evidence["metadata"] == {"source": "unit-test"}
    assert completed["steps"][0]["status"] == "completed"
    assert completed["status"] == "completed"


def test_planner_tools_dispatch_ready_steps_handler_returns_report() -> None:
    registry = ToolRegistry()
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
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)
    registry.get("plan_create").handler({"objective": "Review planner dispatch."})
    registry.get("plan_add_step").handler(
        {
            "plan_id": "plan_1",
            "instruction": "Review ready dispatch.",
            "required_capabilities": ["review"],
        },
    )

    report = json.loads(
        registry.get("plan_dispatch_ready_steps").handler(
            {
                "plan_id": "plan_1",
                "default_template_id": "reviewer",
                "limit": 1,
            },
        ),
    )

    assert report == {
        "plan_id": "plan_1",
        "assigned": [
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "template_id": "reviewer",
                "task_id": "task_1",
                "target_agent_id": "subagent_1",
                "created_at": 10.0,
                "dispatch_status": "submitted",
                "submitted_at": 10.0,
                "dispatch_error": None,
            },
        ],
        "skipped": [],
    }
    persisted = runtime.get_plan("plan_1")
    assert persisted.steps[0].status == "assigned"
    assert coordinator.spawn_calls[0]["task_id"] == "task_1"
    assert coordinator.spawn_calls[0]["allowed_tool_names"] == ("read_file",)


def test_planner_tools_scheduler_tick_handler_returns_retry_and_dispatch_report() -> None:
    registry = ToolRegistry()
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
    plan = runtime.create_plan(
        objective="Run scheduler tick.",
        owner_agent_id="leader",
        plan_id="plan_1",
    )
    runtime.store.save_plan(
        plan.__class__(
            plan_id="plan_1",
            objective="Run scheduler tick.",
            owner_agent_id="leader",
            steps=(
                PlanStep(
                    step_id="step_1",
                    instruction="Retry due step.",
                    status="failed",
                    template_id="reviewer",
                    attempts=1,
                    next_retry_at=10.0,
                    retry_status="scheduled",
                ),
                PlanStep(
                    step_id="step_2",
                    instruction="Independent step.",
                    status="pending",
                    template_id="reviewer",
                ),
            ),
            created_at=10.0,
            updated_at=10.0,
        ),
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    report = json.loads(
        registry.get("plan_scheduler_tick").handler(
            {
                "plan_id": "plan_1",
                "default_template_id": "reviewer",
                "retry_limit": 1,
                "dispatch_limit": 2,
            },
        ),
    )

    assert report["plan_id"] == "plan_1"
    assert report["retry_resets"] == [
        {
            "plan_id": "plan_1",
            "step_id": "step_1",
            "attempts": 1,
        },
    ]
    assert report["dispatch"]["plan_id"] == "plan_1"
    assert report["dispatch"]["skipped"] == []
    assert [assignment["step_id"] for assignment in report["dispatch"]["assigned"]] == [
        "step_1",
        "step_2",
    ]
    for assignment in report["dispatch"]["assigned"]:
        assert assignment["plan_id"] == "plan_1"
        assert assignment["template_id"] == "reviewer"
        assert assignment["task_id"].startswith("task_")
        assert assignment["target_agent_id"].startswith("subagent_")
        assert assignment["created_at"] == 10.0
    assert [call["task_id"] for call in coordinator.spawn_calls] == [
        assignment["task_id"]
        for assignment in report["dispatch"]["assigned"]
    ]


def test_planner_tools_scheduler_tick_cannot_mutate_another_owner_plan() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    runtime.create_plan(
        objective="Other owner's private plan.",
        owner_agent_id="other",
        plan_id="other_plan",
    )
    PlannerTools(
        runtime=runtime,
        owner_agent_id="leader",
        authorization_policy=AllowAllPlannerToolAuthorizationPolicy(),
    ).register(registry)

    with pytest.raises(PlanNotFoundError):
        registry.get("plan_scheduler_tick").handler({"plan_id": "other_plan"})


def test_planner_tools_record_evidence_rejects_unknown_kind() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    registry.get("plan_create").handler(
        {
            "objective": "Review evidence validation.",
            "plan_id": "plan_1",
        },
    )

    with pytest.raises(ValueError, match="unsupported evidence kind"):
        registry.get("plan_record_evidence").handler(
            {
                "plan_id": "plan_1",
                "kind": "unknown",
                "summary": "Invalid kind.",
            },
        )


def test_planner_tools_evidence_kind_schema_is_enumerated() -> None:
    registry = ToolRegistry()
    runtime = PlannerRuntime(store=InMemoryPlanStore(), clock=lambda: 10.0)
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)

    parameters = registry.get("plan_record_evidence").parameters

    assert parameters["properties"]["kind"]["enum"] == [
        "text",
        "artifact",
        "task_result",
        "team_message",
        "external",
    ]


def test_planner_tools_fail_retryable_and_retry_handlers() -> None:
    registry = ToolRegistry()
    clock = ManualClock(10.0)
    runtime = PlannerRuntime(
        store=InMemoryPlanStore(),
        retry_policy=PlanRetryPolicy(max_attempts=3, backoff_seconds=5.0),
        clock=clock,
        id_factory=lambda prefix: f"{prefix}_1",
    )
    PlannerTools(runtime=runtime, owner_agent_id="leader").register(registry)
    registry.get("plan_create").handler(
        {
            "objective": "Review planner recovery tools.",
            "plan_id": "plan_1",
        },
    )
    registry.get("plan_add_step").handler(
        {
            "plan_id": "plan_1",
            "instruction": "Run worker.",
        },
    )

    clock.value = 20.0
    failed = json.loads(
        registry.get("plan_fail_step").handler(
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
                "error": "worker crashed",
            },
        ),
    )
    clock.value = 25.0
    retryable = json.loads(
        registry.get("plan_retryable_steps").handler({"plan_id": "plan_1"}),
    )
    retried = json.loads(
        registry.get("plan_retry_step").handler(
            {
                "plan_id": "plan_1",
                "step_id": "step_1",
            },
        ),
    )

    assert failed["steps"][0]["status"] == "failed"
    assert failed["steps"][0]["error"] == "worker crashed"
    assert failed["steps"][0]["attempts"] == 1
    assert failed["steps"][0]["retry_status"] == "scheduled"
    assert failed["steps"][0]["next_retry_at"] == 25.0
    assert [step["step_id"] for step in retryable["retryable_steps"]] == ["step_1"]
    assert retryable["retryable_steps"][0]["attempts"] == 1
    assert retried["steps"][0]["status"] == "pending"
    assert retried["steps"][0]["error"] is None
    assert retried["steps"][0]["attempts"] == 1
    assert retried["steps"][0]["retry_status"] is None
