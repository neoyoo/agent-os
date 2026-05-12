from __future__ import annotations

import pytest

from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    InMemoryRegistry,
    SpawnExecutor,
    TaskRequest,
    TaskResult,
    TaskTable,
)
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


class FakeA2AAdapter:
    def __init__(self, result: TaskResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[AgentCard, TaskRequest]] = []

    def send_task(self, card: AgentCard, request: TaskRequest) -> TaskResult:
        self.calls.append((card, request))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def remote_card() -> AgentCard:
    return AgentCard(
        agent_id="remote",
        name="Remote",
        description="Remote expert.",
        capabilities=("code-review",),
        endpoint="https://agents.test/remote",
    )


def test_remote_task_executor_calls_a2a_adapter_and_callback() -> None:
    from agentos.multi.remote import RemoteTaskExecutor

    request = TaskRequest(task_id="task_1", instruction="review")
    remote_result = TaskResult(
        task_id="task_1",
        status="completed",
        summary="remote done",
    )
    adapter = FakeA2AAdapter(result=remote_result)
    executor = RemoteTaskExecutor(a2a_adapter=adapter, max_workers=1)
    results: list[TaskResult] = []

    executor.submit(remote_card(), request, results.append)
    executor.shutdown()

    assert adapter.calls == [(remote_card(), request)]
    assert results == [remote_result]


def test_remote_task_executor_maps_adapter_exception_to_failed_result() -> None:
    from agentos.multi.remote import RemoteTaskExecutor

    request = TaskRequest(task_id="task_1", instruction="review")
    executor = RemoteTaskExecutor(
        a2a_adapter=FakeA2AAdapter(error=TimeoutError("remote timeout")),
        max_workers=1,
    )
    results: list[TaskResult] = []

    executor.submit(remote_card(), request, results.append)
    executor.shutdown()

    assert len(results) == 1
    assert results[0].task_id == "task_1"
    assert results[0].status == "failed"
    assert results[0].error == "remote timeout"


class ImmediateRemoteExecutor:
    def __init__(self, result_status: str = "completed") -> None:
        self.calls: list[tuple[AgentCard, TaskRequest]] = []
        self.result_status = result_status

    def submit(self, card, request, on_result):
        self.calls.append((card, request))
        on_result(
            TaskResult(
                task_id=request.task_id,
                status=self.result_status,
                summary=f"remote {self.result_status}",
                error=None if self.result_status == "completed" else "remote failed",
            ),
        )


class HoldingRemoteExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentCard, TaskRequest]] = []
        self.callbacks = []

    def submit(self, card, request, on_result):
        self.calls.append((card, request))
        self.callbacks.append(on_result)


def build_remote_coordinator(remote_executor) -> AgentCoordinator:
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
        remote_task_executor=remote_executor,
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
    coordinator.registry.register(remote_card())
    return coordinator


def test_coordinator_dispatches_endpoint_card_through_remote_executor() -> None:
    remote_executor = ImmediateRemoteExecutor()
    coordinator = build_remote_coordinator(remote_executor)

    handle = coordinator.dispatch(
        instruction="Review remotely",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    assert handle.mode == "dispatch"
    assert handle.target_agent_id == "remote"
    assert remote_executor.calls[0][0] == remote_card()
    assert remote_executor.calls[0][1].instruction == "Review remotely"
    assert coordinator.collect_results("parent") == [
        TaskResult(
            task_id=handle.task_id,
            status="completed",
            summary="remote completed",
        ),
    ]
    coordinator.spawn_executor.shutdown()


def test_coordinator_requires_explicit_remote_executor_for_endpoint_card(monkeypatch) -> None:
    import agentos.multi.remote as remote_module

    class ImplicitRemoteExecutor:
        def submit(self, card, request, on_result):
            return None

    monkeypatch.setattr(remote_module, "RemoteTaskExecutor", ImplicitRemoteExecutor)
    coordinator = build_remote_coordinator(remote_executor=None)

    with pytest.raises(RuntimeError, match="remote_task_executor"):
        coordinator.dispatch(
            instruction="Review remotely",
            required_capabilities=("code-review",),
            parent_agent_id="parent",
        )

    assert coordinator.active_tasks() == []
    coordinator.spawn_executor.shutdown()


def test_coordinator_remote_failure_becomes_failed_task_result() -> None:
    remote_executor = ImmediateRemoteExecutor(result_status="failed")
    coordinator = build_remote_coordinator(remote_executor)

    handle = coordinator.dispatch(
        instruction="Review remotely",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )

    assert coordinator.collect_results("parent") == [
        TaskResult(
            task_id=handle.task_id,
            status="failed",
            summary="remote failed",
            error="remote failed",
        ),
    ]
    coordinator.spawn_executor.shutdown()


def test_coordinator_stores_late_remote_result_after_cancel() -> None:
    remote_executor = HoldingRemoteExecutor()
    coordinator = build_remote_coordinator(remote_executor)

    handle = coordinator.dispatch(
        instruction="Review remotely",
        required_capabilities=("code-review",),
        parent_agent_id="parent",
    )
    assert coordinator.cancel(handle.task_id)
    remote_executor.callbacks[0](
        TaskResult(
            task_id=handle.task_id,
            status="completed",
            summary="late remote result",
        ),
    )

    record = coordinator.task_table.get(handle.task_id)
    assert record is not None
    assert record.status == "cancelled"
    assert record.late_result == TaskResult(
        task_id=handle.task_id,
        status="completed",
        summary="late remote result",
    )
    coordinator.spawn_executor.shutdown()
