import pytest

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
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

    envelopes = coordinator.inbox.collect("expert")
    assert len(envelopes) == 1
    assert envelopes[0].type == "task_request"
    assert envelopes[0].from_agent_id == "parent"
    assert isinstance(envelopes[0].payload, TaskRequest)
    assert envelopes[0].payload.instruction == "Review Python code"

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
