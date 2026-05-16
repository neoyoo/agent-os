from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskRecord,
    TaskRequest,
    TaskResult,
    TaskTable,
)
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


def test_coordinator_accepts_task_store_and_message_queue_boundaries() -> None:
    registry = InMemoryRegistry()
    task_store = TaskTable()
    message_queue = AgentInbox()
    coordinator = AgentCoordinator(
        registry=registry,
        message_queue=message_queue,
        task_store=task_store,
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    parent = AgentCard(
        agent_id="parent",
        name="Parent",
        description="Parent",
        capabilities=("parent",),
    )
    expert = AgentCard(
        agent_id="expert",
        name="Expert",
        description="Expert",
        capabilities=("worker",),
        max_concurrent_tasks=1,
    )

    coordinator.attach_agent(parent, build_agent_with_response("parent"))
    coordinator.attach_agent(expert, build_agent_with_response("expert"))
    handle = coordinator.dispatch(
        instruction="Do work",
        required_capabilities=("worker",),
        parent_agent_id="parent",
    )

    assert task_store.get(handle.task_id) is not None
    delivery = message_queue.collect("expert")[0]
    assert isinstance(delivery.envelope.payload, TaskRequest)
    assert delivery.envelope.payload.task_id == handle.task_id

    coordinator.spawn_executor.shutdown()


def test_coordinator_running_cancel_writes_cancel_intent() -> None:
    task_store = TaskTable()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        message_queue=AgentInbox(),
        task_store=task_store,
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent",
            capabilities=("parent",),
        ),
        build_agent_with_response("parent"),
    )
    task_store.create(
        TaskRecord(
            task_id="task_1",
            mode="dispatch",
            parent_agent_id="parent",
            target_agent_id="worker",
            request=TaskRequest(task_id="task_1", instruction="Do work"),
            status="running",
            created_at=1.0,
            deadline_at=30.0,
            worker_id="worker-instance-1",
            lease_expires_at=20.0,
            attempt=1,
        ),
    )

    assert coordinator.cancel("task_1") is True

    record = task_store.get("task_1")
    assert record is not None
    assert record.status == "running"
    assert record.cancel_requested_at is not None
    assert coordinator.collect_results("parent") == []

    assert task_store.ack_cancelled(
        "task_1",
        TaskResult(task_id="task_1", status="cancelled", summary="cancelled"),
        now=4.0,
        worker_id="worker-instance-1",
        attempt=1,
    )

    coordinator.spawn_executor.shutdown()
