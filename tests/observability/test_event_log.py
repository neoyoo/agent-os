from agentos.compression import CompressionRuntime
from agentos.context import ContextRuntime, WorkingStateField
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.policies import BudgetPolicy
from agentos.recall import RecallRuntime
from agentos.runtime import (
    EventBus,
    ProviderResponseReceivedEvent,
    TurnStartedEvent,
)


def test_event_log_records_typed_events_in_order() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])

    bus.emit(TurnStartedEvent(session_id="s1", turn_id="turn_1", user_input="hello"))
    bus.emit(ProviderResponseReceivedEvent(session_id="s1", turn_id="turn_1"))

    assert [record.sequence for record in log.records] == [1, 2]
    assert [record.event_type for record in log.records] == [
        "TurnStartedEvent",
        "ProviderResponseReceivedEvent",
    ]
    assert log.records[0].payload["user_input"] == "hello"


def test_context_compression_and_recall_emit_traceable_events() -> None:
    log = EventLog()
    bus = EventBus(subscribers=[log])
    context = ContextRuntime(event_bus=bus, session_id="s1")
    messages = MessageRuntime()
    messages.append_user("old detail")
    messages.append_assistant("old answer")
    messages.append_user("current task")
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        event_bus=bus,
        session_id="s1",
    )
    context.declare_schema(
        [
            WorkingStateField(
                name="task_goal",
                type="str",
                purpose="当前任务目标和完成标准",
            ),
        ],
    )
    context.update_state("task_goal", "Trace runtime events.")
    segment = compression.maybe_compress()
    assert segment is not None
    RecallRuntime(
        compression_index=compression.index,
        message_runtime=messages,
        event_bus=bus,
        session_id="s1",
    ).recall_context(segment.id)

    event_types = [record.event_type for record in log.records]
    assert "WorkingStateSchemaDeclaredEvent" in event_types
    assert "WorkingStateUpdatedEvent" in event_types
    assert "CompressionCompletedEvent" in event_types
    assert "RecallContextInjectedEvent" in event_types
