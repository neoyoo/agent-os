import time

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentEnvelope,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    SubagentInitRequest,
    TaskRecord,
    TaskRequest,
    TaskTable,
)
from tests.multi.helpers import build_agent_with_response


class StaticSubagentFactory:
    def __init__(self) -> None:
        self.requests: list[SubagentInitRequest] = []

    def create_subagent(self, request: SubagentInitRequest):
        self.requests.append(request)
        return build_agent_with_response("child result")


def wait_for_result(coordinator: AgentCoordinator, agent_id: str):
    deadline = time.time() + 2
    while time.time() < deadline:
        results = coordinator.collect_results(agent_id)
        if results:
            return results
        time.sleep(0.01)
    raise AssertionError("timed out waiting for result")


def wait_for_task_status(task_table: TaskTable, task_id: str, status: str) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        record = task_table.get(task_id)
        if record is not None and record.status == status:
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {status}")


def test_coordinator_spawn_runs_ephemeral_subagent_and_returns_result() -> None:
    registry = InMemoryRegistry()
    inbox = AgentInbox()
    task_table = TaskTable()
    executor = SpawnExecutor(max_workers=1)
    factory = StaticSubagentFactory()
    coordinator = AgentCoordinator(
        registry=registry,
        inbox=inbox,
        task_table=task_table,
        spawn_executor=executor,
        subagent_factory=factory,
    )
    parent = build_agent_with_response("parent")
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        parent,
    )

    handle = coordinator.spawn(
        instruction="Review this",
        allowed_tool_names=(),
        parent_agent_id="parent",
    )

    assert handle.mode == "spawn"
    assert handle.status == "queued"
    assert handle.target_agent_id.startswith("subagent_")
    assert factory.requests[0].context_strategy == "isolated"

    results = wait_for_result(coordinator, "parent")

    assert results[0].task_id == handle.task_id
    assert results[0].status == "completed"
    assert results[0].summary == "child result"
    assert registry.resolve(handle.target_agent_id) is None
    assert task_table.get(handle.task_id).status == "completed"  # type: ignore[union-attr]

    executor.shutdown()


def test_coordinator_cancel_queued_task_marks_cancelled() -> None:
    registry = InMemoryRegistry()
    inbox = AgentInbox()
    task_table = TaskTable()
    executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=registry,
        inbox=inbox,
        task_table=task_table,
        spawn_executor=executor,
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
    handle = coordinator.spawn(
        instruction="Review this",
        allowed_tool_names=(),
        parent_agent_id="parent",
    )

    assert coordinator.cancel(handle.task_id) is True

    record = task_table.get(handle.task_id)
    assert record is not None
    assert record.status in {"cancelled", "completed"}

    executor.shutdown()


def test_coordinator_collect_results_marks_due_timeouts() -> None:
    registry = InMemoryRegistry()
    inbox = AgentInbox()
    task_table = TaskTable()
    executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=registry,
        inbox=inbox,
        task_table=task_table,
        spawn_executor=executor,
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
    task_table.create(
        TaskRecord(
            task_id="task_due",
            mode="spawn",
            parent_agent_id="parent",
            target_agent_id="subagent_due",
            request=TaskRequest(
                task_id="task_due",
                instruction="Slow work",
                timeout_seconds=1,
            ),
            status="queued",
            created_at=1.0,
            deadline_at=2.0,
        ),
    )

    results = coordinator.collect_results("parent")

    assert results[0].task_id == "task_due"
    assert results[0].status == "timeout"
    assert task_table.get("task_due").status == "timeout"  # type: ignore[union-attr]

    executor.shutdown()


def test_collect_results_uses_task_table_when_parent_inbox_was_full() -> None:
    registry = InMemoryRegistry()
    inbox = AgentInbox(max_pending_envelopes=1)
    task_table = TaskTable()
    executor = SpawnExecutor(max_workers=1)
    coordinator = AgentCoordinator(
        registry=registry,
        inbox=inbox,
        task_table=task_table,
        spawn_executor=executor,
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
    inbox.send(
        AgentEnvelope(
            envelope_id="env_noise",
            from_agent_id="noise",
            to_agent_id="parent",
            type="task_request",
            payload=TaskRequest(
                task_id="task_noise",
                instruction="Noise",
            ),
            created_at=time.time(),
        ),
    )

    handle = coordinator.spawn(
        instruction="Review this",
        allowed_tool_names=(),
        parent_agent_id="parent",
    )
    wait_for_task_status(task_table, handle.task_id, "completed")

    results = coordinator.collect_results("parent")

    assert [result.task_id for result in results] == [handle.task_id]
    assert results[0].summary == "child result"
    assert coordinator.collect_results("parent") == []

    executor.shutdown()
