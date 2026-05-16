from __future__ import annotations

from agentos.multi import TaskRequest, TaskResult
from agentos.observability import (
    InMemoryTracer,
    current_trace_ids,
    inject_trace_headers,
    use_default_trace_propagator,
)
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


class TracedRunner:
    def __init__(self, tracer: InMemoryTracer) -> None:
        self.tracer = tracer
        self.trace_id: str | None = None
        self.parent_span_id: str | None = None

    def run_task(self, request: TaskRequest) -> TaskResult:
        with self.tracer.start_span("remote-task"):
            self.trace_id = current_trace_ids().trace_id
        self.parent_span_id = self.tracer.records[-1].parent_span_id
        return TaskResult(
            task_id=request.task_id,
            status="completed",
            summary="traced",
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


def test_a2a_server_adapter_extracts_incoming_trace_headers() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    tracer = InMemoryTracer()
    headers: dict[str, str] = {}
    with use_default_trace_propagator(tracer):
        with tracer.start_span("parent"):
            parent_ids = current_trace_ids()
            inject_trace_headers(headers)

        runner = TracedRunner(tracer)
        A2AServerAdapter(runner).handle_task(
            {"task_id": "task_1", "instruction": "do remote work"},
            headers=headers,
        )

    assert runner.trace_id == parent_ids.trace_id
    assert runner.parent_span_id == parent_ids.span_id


def test_a2a_server_adapter_returns_failed_result_for_invalid_payload() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    response = A2AServerAdapter(StaticRunner()).handle_task({})

    assert response["task_id"] == ""
    assert response["status"] == "failed"
    assert "task_id" in str(response["error"])


def test_a2a_server_adapter_health_is_ok() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    assert A2AServerAdapter(StaticRunner()).handle_health() == {"status": "ok"}
