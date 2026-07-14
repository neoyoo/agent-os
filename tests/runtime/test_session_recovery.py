import asyncio

from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime, ContextSnapshotRenderer, WorkingStateField
from agentos.context.projection import project_context_state
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.persistence import MemoryPersistence, SessionSnapshot
from agentos.policies import BudgetPolicy
from agentos.providers import FakeProvider
from agentos.recall import RecallRuntime
from agentos.runtime import Agent, EventBus, ProviderRequestBuilder, QueryLoop, SessionState
from agentos.tokens import HeuristicTokenCounter
from tests._context_protocol_fixtures import default_context_renderer


class RuntimeProjectionProvider:
    def __init__(self, runtime: ContextRuntime) -> None:
        self.runtime = runtime

    def projections(self):  # type: ignore[no-untyped-def]
        return project_context_state(self.runtime.snapshot())


def _request_builder(
    context: ContextRuntime,
    messages: MessageRuntime,
) -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        context_renderer=default_context_renderer(),
        message_runtime=messages,
        tools=[],
        snapshot_renderer=ContextSnapshotRenderer(HeuristicTokenCounter()),
        context_projections=RuntimeProjectionProvider(context),
    )


def test_session_snapshot_restores_context_messages_compression_and_recall() -> None:
    event_log = EventLog()
    bus = EventBus(subscribers=[event_log])
    context = ContextRuntime(event_bus=bus, session_id="session_1")
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="string",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Recover session.")
    messages = MessageRuntime()
    provider = FakeProvider(["first answer", "second answer"])
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        event_bus=bus,
        session_id="session_1",
    )
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=_request_builder(context, messages),
        provider=provider,
        compression_runtime=compression,
        event_bus=bus,
        session_state=SessionState(id="session_1"),
    )
    agent = Agent(loop)

    async def run_turns() -> None:
        await agent.run("old detail")
        await agent.run("current task")

    asyncio.run(run_turns())
    rendered_before_save = _request_builder(context, messages).build().request.system
    snapshot = SessionSnapshot(
        session_state=loop.session_state,
        context_state=context.snapshot(),
        message_runtime=messages,
        compression_index=compression.index,
        next_segment_number=compression.next_segment_number(),
        event_records=tuple(event_log.records),
    )
    persistence = MemoryPersistence()
    persistence.save(snapshot)

    restored = persistence.load("session_1")
    restored_context = ContextRuntime(state=restored.context_state)
    restored_messages = restored.message_runtime
    restored_compression = CompressionRuntime(
        context_runtime=restored_context,
        message_runtime=restored_messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        index=restored.compression_index,
        next_segment_number=restored.next_segment_number,
    )
    recalled = RecallRuntime(
        compression_index=restored_compression.index,
        message_runtime=restored_messages,
    ).recall_context("seg_1")

    request = _request_builder(restored_context, restored_messages).build().request

    assert restored_context.snapshot().working_state["task_goal"] == "Recover session."
    assert request.system == rendered_before_save
    assert "Recover session." not in request.system
    assert [message.content for message in recalled] == ["old detail", "first answer"]
    assert request.messages[-2].content[0].text == "current task"  # type: ignore[union-attr]
    assert request.messages[-1].content[0].text == "second answer"  # type: ignore[union-attr]
    assert restored.session_state.new_turn("after restore").id == "turn_3"
    assert restored.event_records[0].event_type == "WorkingStateSchemaDeclaredEvent"
