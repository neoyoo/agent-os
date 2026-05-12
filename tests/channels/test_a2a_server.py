from __future__ import annotations

from agentos.multi import TaskRequest, TaskResult
from tests.multi.helpers import build_agent_with_response


class StaticRunner:
    def __init__(self) -> None:
        self.requests: list[TaskRequest] = []

    def run_task(self, request: TaskRequest) -> TaskResult:
        self.requests.append(request)
        return TaskResult(
            task_id=request.task_id,
            status="completed",
            summary="runner done",
            artifacts={"allowed": list(request.allowed_tool_names)},
            elapsed_seconds=0.5,
        )


def test_agent_a2a_task_runner_wraps_agent_run() -> None:
    from agentos.channels.a2a_server import AgentA2ATaskRunner

    runner = AgentA2ATaskRunner(build_agent_with_response("agent done"))

    result = runner.run_task(
        TaskRequest(
            task_id="task_1",
            instruction="do remote work",
            allowed_tool_names=("read_file",),
        ),
    )

    assert result.task_id == "task_1"
    assert result.status == "completed"
    assert result.summary == "agent done"


def test_a2a_server_adapter_handles_task_payload() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    runner = StaticRunner()
    adapter = A2AServerAdapter(runner)

    response = adapter.handle_task(
        {
            "task_id": "task_1",
            "instruction": "do remote work",
            "allowed_tool_names": ["read_file"],
            "timeout_seconds": 12,
        },
    )

    assert response == {
        "task_id": "task_1",
        "status": "completed",
        "summary": "runner done",
        "artifacts": {"allowed": ["read_file"]},
        "error": None,
        "elapsed_seconds": 0.5,
    }
    assert runner.requests == [
        TaskRequest(
            task_id="task_1",
            instruction="do remote work",
            allowed_tool_names=("read_file",),
            timeout_seconds=12,
        ),
    ]


def test_a2a_server_adapter_returns_failed_result_for_invalid_payload() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    response = A2AServerAdapter(StaticRunner()).handle_task({})

    assert response["task_id"] == ""
    assert response["status"] == "failed"
    assert "task_id" in str(response["error"])


def test_a2a_server_adapter_health_is_ok() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    assert A2AServerAdapter(StaticRunner()).handle_health() == {"status": "ok"}
