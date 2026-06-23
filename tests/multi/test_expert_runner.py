import time

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    ExpertAgentRunner,
    InMemoryRegistry,
    SpawnExecutor,
    TaskTable,
)
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


class RecordingInbox(AgentInbox):
    def __init__(self) -> None:
        super().__init__()
        self.acked: list[tuple[str, str]] = []

    def ack(self, agent_id: str, delivery_id: str) -> bool:
        self.acked.append((agent_id, delivery_id))
        return super().ack(agent_id, delivery_id)


class RejectTerminalTaskTable(TaskTable):
    def mark_completed(self, *args: object, **kwargs: object) -> bool:
        return False


def wait_for_parent_result(coordinator: AgentCoordinator):
    deadline = time.time() + 2
    while time.time() < deadline:
        results = coordinator.collect_results("parent")
        if results:
            return results[0]
        time.sleep(0.01)
    raise AssertionError("timed out waiting for expert result")


def test_expert_runner_processes_one_task_request_and_returns_result() -> None:
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
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
            capabilities=("code-review",),
        ),
        build_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    runner = ExpertAgentRunner(coordinator=coordinator, agent_id="expert")

    assert runner.run_once(timeout=0.1) is True

    result = wait_for_parent_result(coordinator)
    assert result.task_id == handle.task_id
    assert result.status == "completed"
    assert result.summary == "expert result"
    assert coordinator.task_table.get(handle.task_id).status == "completed"  # type: ignore[union-attr]

    coordinator.spawn_executor.shutdown()


def test_expert_runner_claims_task_before_execution_and_acks_after_terminal_save() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
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
            capabilities=("code-review",),
        ),
        build_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is True

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.worker_id == "expert-worker-1"
    assert stored.attempt == 1
    assert len(inbox.acked) == 1
    assert inbox.acked[0][0] == "expert"

    coordinator.spawn_executor.shutdown()


def test_expert_runner_does_not_ack_when_terminal_save_is_rejected() -> None:
    inbox = RecordingInbox()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=inbox,
        task_table=RejectTerminalTaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
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
            capabilities=("code-review",),
        ),
        build_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    runner = ExpertAgentRunner(
        coordinator=coordinator,
        agent_id="expert",
        worker_id="expert-worker-1",
        capabilities=("code-review",),
        lease_ttl_seconds=30.0,
    )

    assert runner.run_once(timeout=0.1) is False

    stored = coordinator.task_table.get(handle.task_id)
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_id == "expert-worker-1"
    assert stored.attempt == 1
    assert inbox.acked == []
    assert coordinator.collect_results("parent") == []

    coordinator.spawn_executor.shutdown()
