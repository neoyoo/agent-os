import time
from threading import Event, Thread

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.events import AgentContinuationFailedEvent, EventBus
from agentos.messages import MessageRuntime
from agentos.multi import (
    AgentCard,
    AgentCoordinator,
    AgentInbox,
    AgentTaskNoticeStore,
    InMemoryRegistry,
    LocalContinuationTrigger,
    SpawnExecutor,
    TaskRecord,
    TaskRequest,
    TaskTable,
)
from agentos.multi import AgentCoordinationTools
from agentos.providers import FakeProvider, ProviderResponse, ProviderToolCall
from agentos.runtime import Agent, ProviderRequestBuilder
from tests.multi.helpers import build_agent_with_response
from tests.multi.test_coordinator_spawn import StaticSubagentFactory


class RecordingTrigger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def on_task_completed(self, parent_agent_id: str, task_id: str) -> None:
        self.calls.append((parent_agent_id, task_id))


class RaisingTrigger:
    def on_task_completed(self, parent_agent_id: str, task_id: str) -> None:
        raise RuntimeError("trigger unavailable")


class BlockingProvider:
    def __init__(self) -> None:
        self.requests = []
        self.first_started = Event()
        self.release_first = Event()

    def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            self.first_started.set()
            self.release_first.wait(timeout=1)
            return ProviderResponse(content="user turn")
        return ProviderResponse(content="continuation")


class DelegatingParentProvider:
    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="spawn_call",
                        name="spawn_subagent",
                        arguments={"instruction": "Review this"},
                    ),
                ],
            )
        if len(self.requests) == 2:
            return ProviderResponse(content="spawned")
        if len(self.requests) == 3:
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="check_call",
                        name="check_agent_tasks",
                        arguments={},
                    ),
                ],
            )
        if len(self.requests) == 4:
            return ProviderResponse(content="collected child result")
        raise RuntimeError("unexpected provider request")


def build_parent_agent(provider, notice_store: AgentTaskNoticeStore) -> Agent:
    messages = MessageRuntime()
    return Agent(
        query_loop_kwargs={
            "context_runtime": ContextRuntime(),
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=[],
            ),
            "provider": provider,
            "turn_notice_provider": notice_store.provider_for("parent"),
        },
    )


def build_parent_agent_with_coordination_tools(
    provider,
    notice_store: AgentTaskNoticeStore,
    coordinator: AgentCoordinator,
) -> Agent:
    context = ContextRuntime()
    messages = MessageRuntime()
    tool_registry = ToolRegistry()
    AgentCoordinationTools(
        coordinator=coordinator,
        parent_agent_id="parent",
    ).register(tool_registry)
    router = ToolCallRouter(tool_registry=tool_registry, context_runtime=context)
    return Agent(
        query_loop_kwargs={
            "context_runtime": context,
            "message_runtime": messages,
            "request_builder": ProviderRequestBuilder(
                context_renderer=ContextRenderer(),
                message_runtime=messages,
                tools=router.tool_specs(),
            ),
            "provider": provider,
            "tool_call_router": router,
            "turn_notice_provider": notice_store.provider_for("parent"),
        },
    )


def wait_for_calls(trigger: RecordingTrigger) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        if trigger.calls:
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for trigger call")


def wait_for_request_count(provider: DelegatingParentProvider, count: int) -> None:
    deadline = time.time() + 2
    while time.time() < deadline:
        if len(provider.requests) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {count} provider requests")


def test_coordinator_invokes_continuation_trigger_on_spawn_completion() -> None:
    trigger = RecordingTrigger()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
        continuation_trigger=trigger,
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
        parent_agent_id="parent",
    )
    wait_for_calls(trigger)

    assert trigger.calls == [("parent", handle.task_id)]

    coordinator.spawn_executor.shutdown()


def test_local_continuation_trigger_runs_parent_continuation_when_idle() -> None:
    notice_store = AgentTaskNoticeStore()
    parent = build_parent_agent(
        FakeProvider([ProviderResponse(content="continued")]),
        notice_store,
    )
    trigger = LocalContinuationTrigger(
        agents={"parent": parent},
        notice_store=notice_store,
    )

    trigger.on_task_completed("parent", "task_1")
    assert trigger.wait_idle("parent", timeout=1)

    request = parent.query_loop.provider.requests[0]  # type: ignore[attr-defined]
    assert "# Runtime Notice" in request.system
    assert "task_1" in request.system

    trigger.shutdown()


def test_local_continuation_trigger_records_and_emits_parent_failures() -> None:
    notice_store = AgentTaskNoticeStore()
    parent = build_parent_agent(FakeProvider([]), notice_store)
    event_bus = EventBus()
    trigger = LocalContinuationTrigger(
        agents={"parent": parent},
        notice_store=notice_store,
        event_bus=event_bus,
    )

    trigger.on_task_completed("parent", "task_1")
    assert trigger.wait_idle("parent", timeout=1)

    errors = trigger.continuation_errors("parent")
    failure_events = [
        event
        for event in event_bus.events
        if isinstance(event, AgentContinuationFailedEvent)
    ]
    assert len(errors) == 1
    assert errors[0].parent_agent_id == "parent"
    assert "FakeProvider has no responses left" in errors[0].error
    assert len(failure_events) == 1
    assert failure_events[0].parent_agent_id == "parent"
    assert "FakeProvider has no responses left" in failure_events[0].error

    trigger.shutdown()


def test_coordinator_trigger_failure_does_not_change_completed_expert_result() -> None:
    event_bus = EventBus()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
        event_bus=event_bus,
        continuation_trigger=RaisingTrigger(),
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
            capabilities=("python",),
        ),
        build_agent_with_response("expert result"),
    )
    handle = coordinator.dispatch(
        instruction="Review this",
        required_capabilities=("python",),
        parent_agent_id="parent",
    )
    envelope = coordinator.inbox.collect("expert")[0].envelope

    result = coordinator.execute_expert_envelope(envelope)

    failure_events = [
        event
        for event in event_bus.events
        if isinstance(event, AgentContinuationFailedEvent)
    ]
    assert result is not None
    assert result.status == "completed"
    assert result.summary == "expert result"
    assert coordinator.task_table.get(handle.task_id).status == "completed"  # type: ignore[union-attr]
    assert coordinator.collect_results("parent")[0].status == "completed"
    assert len(failure_events) == 1
    assert failure_events[0].parent_agent_id == "parent"
    assert failure_events[0].task_id == handle.task_id
    assert "trigger unavailable" in failure_events[0].error

    coordinator.spawn_executor.shutdown()


def test_coordinator_trigger_failure_does_not_break_cancelled_task() -> None:
    event_bus = EventBus()
    task_table = TaskTable()
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=task_table,
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
        event_bus=event_bus,
        continuation_trigger=RaisingTrigger(),
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
            task_id="task_cancel",
            mode="spawn",
            parent_agent_id="parent",
            target_agent_id="child",
            request=TaskRequest(
                task_id="task_cancel",
                instruction="Cancel this",
            ),
            status="queued",
            created_at=1.0,
            deadline_at=9999999999.0,
        ),
    )

    cancelled = coordinator.cancel("task_cancel")

    failure_events = [
        event
        for event in event_bus.events
        if isinstance(event, AgentContinuationFailedEvent)
    ]
    assert cancelled is True
    assert task_table.get("task_cancel").status == "cancelled"  # type: ignore[union-attr]
    assert coordinator.collect_results("parent")[0].status == "cancelled"
    assert len(failure_events) == 1
    assert failure_events[0].task_id == "task_cancel"
    assert "trigger unavailable" in failure_events[0].error

    coordinator.spawn_executor.shutdown()


def test_local_continuation_trigger_queues_while_user_turn_is_running() -> None:
    notice_store = AgentTaskNoticeStore()
    provider = BlockingProvider()
    parent = build_parent_agent(provider, notice_store)
    trigger = LocalContinuationTrigger(
        agents={"parent": parent},
        notice_store=notice_store,
    )
    user_thread = Thread(target=lambda: parent.run("hello"))

    user_thread.start()
    assert provider.first_started.wait(timeout=1)
    trigger.on_task_completed("parent", "task_1")
    time.sleep(0.05)

    assert len(provider.requests) == 1

    provider.release_first.set()
    user_thread.join(timeout=1)
    assert trigger.wait_idle("parent", timeout=1)

    assert len(provider.requests) == 2
    assert provider.requests[0].messages == [{"role": "user", "content": "hello"}]
    assert "# Runtime Notice" in provider.requests[1].system
    assert "task_1" in provider.requests[1].system

    trigger.shutdown()


def test_spawn_completion_triggers_continuation_and_result_collection_e2e() -> None:
    notice_store = AgentTaskNoticeStore()
    local_agents: dict[str, Agent] = {}
    trigger = LocalContinuationTrigger(
        agents=local_agents,
        notice_store=notice_store,
    )
    coordinator = AgentCoordinator(
        registry=InMemoryRegistry(),
        inbox=AgentInbox(),
        task_table=TaskTable(),
        spawn_executor=SpawnExecutor(max_workers=1),
        subagent_factory=StaticSubagentFactory(),
        continuation_trigger=trigger,
    )
    provider = DelegatingParentProvider()
    parent = build_parent_agent_with_coordination_tools(
        provider,
        notice_store,
        coordinator,
    )
    local_agents["parent"] = parent
    coordinator.attach_agent(
        AgentCard(
            agent_id="parent",
            name="Parent",
            description="Parent agent.",
            capabilities=("coordinate",),
        ),
        parent,
    )

    result = parent.run("delegate this")
    wait_for_request_count(provider, 4)
    assert trigger.wait_idle("parent", timeout=1)

    assert result.content == "spawned"
    assert "# Runtime Notice" in provider.requests[2].system
    assert "Call check_agent_tasks to retrieve results." in provider.requests[2].system
    check_request_messages = provider.requests[3].messages
    assert any(
        message["role"] == "tool" and "child result" in str(message["content"])
        for message in check_request_messages
    )
    assert parent.query_loop.message_runtime.materialize_provider_messages()[-1] == {
        "role": "assistant",
        "content": "collected child result",
    }

    trigger.shutdown()
    coordinator.spawn_executor.shutdown()
