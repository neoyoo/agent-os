from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentCoordinatorPlanStepDispatcher,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
)
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.planning import (
    InMemoryPlanStore,
    PlanAssignment,
    PlanState,
    PlanStep,
    PlannerRuntime,
    SubAgentTemplate,
)
from tests.multi.helpers import build_sync_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory
from tests.multi.test_postgres_task_store import FakeConnection
from tests.planning._async import async_test


@async_test
async def test_planner_recovery_accepts_postgres_duplicate_from_agent_coordinator() -> None:
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
    await store.create_plan(
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
        dispatcher=AgentCoordinatorPlanStepDispatcher(coordinator),
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
        report = await runtime.recover_pending_dispatches("plan_1")
    finally:
        coordinator.spawn_executor.shutdown()

    persisted = await runtime.get_plan("plan_1")
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
