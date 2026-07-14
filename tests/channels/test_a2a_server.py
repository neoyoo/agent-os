from __future__ import annotations

import asyncio

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

    async def run_task(self, request: TaskRequest) -> TaskResult:
        self.requests.append(request)
        return TaskResult(
            task_id=request.task_id,
            status="completed",
            summary="runner done",
            artifacts={
                "allowed": list(request.allowed_tool_names),
                "required": list(request.required_capabilities),
            },
            elapsed_seconds=0.5,
        )


class ValueErrorRunner:
    async def run_task(self, request: TaskRequest) -> TaskResult:
        raise ValueError("secret backend detail")


class TracedRunner:
    def __init__(self, tracer: InMemoryTracer) -> None:
        self.tracer = tracer
        self.trace_id: str | None = None
        self.parent_span_id: str | None = None

    async def run_task(self, request: TaskRequest) -> TaskResult:
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

    result = asyncio.run(
        runner.run_task(
            TaskRequest(
                task_id="task_1",
                instruction="do remote work",
                allowed_tool_names=("read_file",),
            ),
        ),
    )

    assert result.task_id == "task_1"
    assert result.status == "completed"
    assert result.summary == "agent done"


def test_a2a_server_adapter_handles_task_payload() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    runner = StaticRunner()
    adapter = A2AServerAdapter(runner)

    response = asyncio.run(
        adapter.handle_task(
            {
                "task_id": "task_1",
                "instruction": "do remote work",
                "required_capabilities": ["search"],
                "allowed_tool_names": ["read_file"],
                "timeout_seconds": 12,
            },
        ),
    )

    assert response == {
        "task_id": "task_1",
        "status": "completed",
        "summary": "runner done",
        "artifacts": {"allowed": ["read_file"], "required": ["search"]},
        "error": None,
        "elapsed_seconds": 0.5,
    }
    assert runner.requests == [
        TaskRequest(
            task_id="task_1",
            instruction="do remote work",
            required_capabilities=("search",),
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
        asyncio.run(
            A2AServerAdapter(runner).handle_task(
                {"task_id": "task_1", "instruction": "do remote work"},
                headers=headers,
            ),
        )

    assert runner.trace_id == parent_ids.trace_id
    assert runner.parent_span_id == parent_ids.span_id


def test_a2a_server_adapter_returns_failed_result_for_invalid_payload() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    response = asyncio.run(A2AServerAdapter(StaticRunner()).handle_task({}))

    assert response["task_id"] == ""
    assert response["status"] == "failed"
    assert "task_id" in str(response["error"])


def test_a2a_server_adapter_requires_capabilities_to_be_a_list() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    response = asyncio.run(
        A2AServerAdapter(StaticRunner()).handle_task(
            {
                "task_id": "task_1",
                "instruction": "do remote work",
                "required_capabilities": "search",
            },
        ),
    )

    assert response["task_id"] == "task_1"
    assert response["status"] == "failed"
    assert "required_capabilities" in str(response["error"])


def test_a2a_server_adapter_redacts_runner_value_errors() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    response = asyncio.run(
        A2AServerAdapter(ValueErrorRunner()).handle_task(
            {"task_id": "task_1", "instruction": "do remote work"},
        ),
    )

    assert response["task_id"] == "task_1"
    assert response["status"] == "failed"
    assert response["error"] == "internal error"


def test_a2a_server_adapter_health_is_ok() -> None:
    from agentos.channels.a2a_server import A2AServerAdapter

    assert A2AServerAdapter(StaticRunner()).handle_health() == {"status": "ok"}


def test_agent_a2a_task_runner_preserves_waiting_metadata() -> None:
    from agentos.channels.a2a_server import AgentA2ATaskRunner
    from agentos.runtime import AgentWaiting, WaitReason

    class WaitingAgent:
        async def run(self, input: str) -> AgentWaiting:
            return AgentWaiting(
                run_id="run_1",
                reason=WaitReason("human_input", "approval_1", "confirm"),
            )

    result = asyncio.run(
        AgentA2ATaskRunner(WaitingAgent()).run_task(  # type: ignore[arg-type]
            TaskRequest(task_id="task_1", instruction="do remote work"),
        ),
    )

    assert result.status == "running"
    assert result.summary == "task waiting"
    assert result.artifacts == {
        "runId": "run_1",
        "waitReason": {
            "kind": "human_input",
            "handle": "approval_1",
            "detail": "confirm",
        },
    }
