import json

from agentos.capabilities import ToolRegistry
from agentos.multi import AgentCoordinationTools, TaskHandle, TaskResult


class FakeCoordinator:
    def __init__(self) -> None:
        self.spawn_calls: list[dict[str, object]] = []
        self.dispatch_calls: list[dict[str, object]] = []
        self.cancelled: list[str] = []

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
            target_agent_id="expert",
            status="queued",
        )

    def active_tasks(self, agent_id: str | None = None) -> list[TaskHandle]:
        return [
            TaskHandle(
                task_id="task_active",
                mode="dispatch",
                target_agent_id="expert",
                status="running",
            ),
        ]

    def collect_results(self, agent_id: str) -> list[TaskResult]:
        return [
            TaskResult(
                task_id="task_done",
                status="completed",
                summary="done",
            ),
        ]

    def cancel(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return True


def test_coordination_tools_register_external_tools() -> None:
    registry = ToolRegistry()

    AgentCoordinationTools(
        coordinator=FakeCoordinator(),  # type: ignore[arg-type]
        parent_agent_id="parent",
    ).register(registry)

    names = [
        spec["function"]["name"]
        for spec in registry.provider_tool_specs()
    ]
    assert names == [
        "spawn_subagent",
        "dispatch_to_expert",
        "check_agent_tasks",
        "cancel_agent_task",
    ]


def test_coordination_tool_handlers_call_coordinator_and_return_json() -> None:
    registry = ToolRegistry()
    coordinator = FakeCoordinator()
    AgentCoordinationTools(
        coordinator=coordinator,  # type: ignore[arg-type]
        parent_agent_id="parent",
    ).register(registry)

    spawn_result = json.loads(
        registry.get("spawn_subagent").handler(
            {
                "instruction": "Review this",
                "allowed_tool_names": ["read_file"],
                "timeout_seconds": 10,
            },
        ),
    )
    dispatch_result = json.loads(
        registry.get("dispatch_to_expert").handler(
            {
                "instruction": "Review Python",
                "required_capabilities": ["python"],
            },
        ),
    )
    status_result = json.loads(
        registry.get("check_agent_tasks").handler({}),
    )
    cancel_result = json.loads(
        registry.get("cancel_agent_task").handler({"task_id": "task_active"}),
    )

    assert spawn_result == {
        "task_id": "task_spawn",
        "mode": "spawn",
        "target_agent_id": "subagent_1",
        "status": "queued",
    }
    assert coordinator.spawn_calls[0]["parent_agent_id"] == "parent"
    assert coordinator.spawn_calls[0]["allowed_tool_names"] == ("read_file",)
    assert dispatch_result["task_id"] == "task_dispatch"
    assert coordinator.dispatch_calls[0]["required_capabilities"] == ("python",)
    assert status_result["active_tasks"][0]["task_id"] == "task_active"
    assert status_result["results"][0]["summary"] == "done"
    assert cancel_result == {"task_id": "task_active", "cancelled": True}
    assert coordinator.cancelled == ["task_active"]
