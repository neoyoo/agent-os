from agentos.compression import CompressionRuntime
from agentos.context import ContextRenderer, ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.persistence import MemoryPersistence, SessionSnapshot
from agentos.policies import BudgetPolicy
from agentos.providers import FakeProvider
from agentos.recall import RecallRuntime
from agentos.runtime import EventBus, ProviderRequestBuilder, QueryLoop, SessionState


def test_session_snapshot_restores_context_messages_compression_and_recall() -> None:
    event_log = EventLog()
    bus = EventBus(subscribers=[event_log])
    context = ContextRuntime(event_bus=bus, session_id="session_1")
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
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
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=[],
        ),
        provider=provider,
        compression_runtime=compression,
        event_bus=bus,
        session_state=SessionState(id="session_1"),
    )
    loop.run_turn("old detail")
    loop.run_turn("current task")
    rendered_before_save = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=messages,
        tools=[],
    ).build(context).system
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
    RecallRuntime(
        compression_index=restored_compression.index,
        message_runtime=restored_messages,
    ).recall_context("seg_1")

    request = ProviderRequestBuilder(
        context_renderer=ContextRenderer(),
        message_runtime=restored_messages,
        tools=[],
    ).build(restored_context)

    assert request.system == rendered_before_save
    assert request.messages[0]["content"] == "old detail"
    assert request.messages[-1]["content"] == "second answer"
    assert restored.session_state.new_turn("after restore").id == "turn_3"
    assert restored.event_records[0].event_type == "WorkingStateSchemaDeclaredEvent"
