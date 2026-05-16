from __future__ import annotations

from collections.abc import Callable

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
from agentos.observability import (
    InMemoryTracer,
    current_trace_ids,
    use_default_trace_propagator,
)
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


class RecordingRemoteExecutor:
    def __init__(self) -> None:
        self.requests: list[TaskRequest] = []

    def submit(
        self,
        target: AgentCard,
        request: TaskRequest,
        on_result: Callable[[TaskResult], None],
    ) -> object:
        self.requests.append(request)
        return object()


def build_remote_coordinator(executor: RecordingRemoteExecutor) -> AgentCoordinator:
    registry = InMemoryRegistry()
    inbox = AgentInbox()
    task_table = TaskTable()
    coordinator = AgentCoordinator(
        registry=registry,
        inbox=inbox,
        task_table=task_table,
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
        remote_task_executor=executor,
    )
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent.",
            capabilities=("coordinate",),
        ),
        build_agent_with_response("parent"),
    )
    registry.register(
        AgentCard(
            agent_id="remote",
            name="Remote",
            description="Remote.",
            capabilities=("remote-capability",),
            endpoint="https://agents.test/remote",
        ),
    )
    inbox.create_inbox("remote")
    return coordinator


def test_remote_dispatch_injects_current_trace_context() -> None:
    tracer = InMemoryTracer()
    executor = RecordingRemoteExecutor()
    coordinator = build_remote_coordinator(executor)

    with use_default_trace_propagator(tracer):
        with tracer.start_span("parent-dispatch") as span:
            coordinator.dispatch(
                instruction="remote work",
                required_capabilities=("remote-capability",),
                parent_agent_id="parent",
            )
            parent_ids = current_trace_ids()
            span.set_attribute("captured", True)

    request = executor.requests[0]
    assert request.trace_context is not None
    assert request.trace_context["traceparent"].startswith(
        f"00-{parent_ids.trace_id}-{parent_ids.span_id}-",
    )
    coordinator.spawn_executor.shutdown()


def test_remote_dispatch_leaves_trace_context_empty_without_active_span() -> None:
    executor = RecordingRemoteExecutor()
    coordinator = build_remote_coordinator(executor)

    coordinator.dispatch(
        instruction="remote work",
        required_capabilities=("remote-capability",),
        parent_agent_id="parent",
    )

    assert executor.requests[0].trace_context is None
    coordinator.spawn_executor.shutdown()


def test_spawn_executor_propagates_contextvars_to_worker_thread() -> None:
    tracer = InMemoryTracer()
    executor = SpawnExecutor(max_workers=1)

    with use_default_trace_propagator(tracer):
        with tracer.start_span("parent"):
            parent_ids = current_trace_ids()

            future = executor.submit(
                "task_1",
                lambda: _record_child_span(tracer),
            )
            result = future.result(timeout=2)

    assert result.artifacts["trace_id"] == parent_ids.trace_id
    assert result.artifacts["parent_span_id"] == parent_ids.span_id
    executor.shutdown()


def _record_child_span(tracer: InMemoryTracer) -> TaskResult:
    with tracer.start_span("child"):
        ids = current_trace_ids()
    child_record = tracer.records[-1]
    return TaskResult(
        task_id="task_1",
        status="completed",
        summary="done",
        artifacts={
            "trace_id": ids.trace_id,
            "parent_span_id": child_record.parent_span_id,
        },
    )
