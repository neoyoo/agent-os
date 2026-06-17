import pytest

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentEnvelope,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskRecord,
    TaskRequest,
    TaskTable,
)
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


def build_coordinator() -> AgentCoordinator:
    return AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )


def attach_parent_and_expert(coordinator: AgentCoordinator) -> None:
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert",
            name="Expert",
            description="Expert agent.",
            capabilities=("code-review", "python"),
            max_concurrent_tasks=1,
        ),
        build_agent_with_response("expert result"),
    )


def test_dispatch_sends_task_request_to_available_expert() -> None:
    coordinator = build_coordinator()
    attach_parent_and_expert(coordinator)

    handle = coordinator.dispatch(
        instruction="Review Python code",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    assert handle.mode == "dispatch"
    assert handle.status == "queued"
    assert handle.target_agent_id == "expert"

    deliveries = coordinator.inbox.collect("expert")
    assert len(deliveries) == 1
    envelope = deliveries[0].envelope
    assert envelope.type == "task_request"
    assert envelope.from_agent_id == "parent"
    assert isinstance(envelope.payload, TaskRequest)
    assert envelope.payload.instruction == "Review Python code"

    coordinator.spawn_executor.shutdown()


def test_dispatch_accepts_reserved_task_id() -> None:
    coordinator = build_coordinator()
    attach_parent_and_expert(coordinator)

    handle = coordinator.dispatch(
        instruction="Review Python code",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
        task_id="task_reserved",
    )

    assert handle.task_id == "task_reserved"
    deliveries = coordinator.inbox.collect("expert")
    assert len(deliveries) == 1
    envelope = deliveries[0].envelope
    assert isinstance(envelope.payload, TaskRequest)
    assert envelope.payload.task_id == "task_reserved"

    coordinator.spawn_executor.shutdown()


def test_dispatch_can_target_specific_matching_expert() -> None:
    coordinator = build_coordinator()
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        build_agent_with_response("parent"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert_a",
            name="Expert A",
            description="General reviewer.",
            capabilities=("code-review",),
            max_concurrent_tasks=1,
        ),
        build_agent_with_response("expert a result"),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="expert_b",
            name="Expert B",
            description="Specific reviewer.",
            capabilities=("code-review",),
            max_concurrent_tasks=1,
        ),
        build_agent_with_response("expert b result"),
    )

    handle = coordinator.dispatch(
        instruction="Review the planner boundary.",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
        target_agent_id="expert_b",
    )

    assert handle.target_agent_id == "expert_b"
    assert coordinator.inbox.collect("expert_a") == []
    deliveries = coordinator.inbox.collect("expert_b")
    assert len(deliveries) == 1

    coordinator.spawn_executor.shutdown()


def test_dispatch_raises_when_matching_expert_is_saturated() -> None:
    coordinator = build_coordinator()
    attach_parent_and_expert(coordinator)

    coordinator.dispatch(
        instruction="Review first",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    with pytest.raises(RuntimeError, match="no available agent"):
        coordinator.dispatch(
            instruction="Review second",
            required_capabilities=("code-review",),
            parent_agent_id="parent",
        )

    coordinator.spawn_executor.shutdown()


def test_execute_expert_envelope_rejects_wrong_recipient() -> None:
    coordinator = build_coordinator()
    attach_parent_and_expert(coordinator)
    request = TaskRequest(task_id="task_1", instruction="Review Python code")
    coordinator.task_table.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="expert",
            request=request,
            status="queued",
            created_at=1.0,
            deadline_at=9_999_999_999.0,
        ),
    )
    envelope = AgentEnvelope(
        envelope_id="env_wrong_recipient",
        from_agent_id="parent",
        to_agent_id="other_expert",
        type="task_request",
        payload=request,
        created_at=1.0,
        correlation_id="task_1",
    )

    result = coordinator.execute_expert_envelope(envelope)

    record = coordinator.task_table.get("task_1")
    assert result is None
    assert record is not None
    assert record.status == "queued"
    assert record.result is None
    assert coordinator.collect_results("parent") == []

    coordinator.spawn_executor.shutdown()
