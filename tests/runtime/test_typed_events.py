from agentos.runtime import (
    AgentContinuationFailedEvent,
    AgentInboxBackpressureEvent,
    AgentTaskCancelledEvent,
    AgentTaskCompletedEvent,
    AgentTaskDispatchedEvent,
    AgentTaskFailedEvent,
    AgentTaskLateResultReceivedEvent,
    EventBus,
    SubagentSpawnedEvent,
    TurnStartedEvent,
)


def test_event_bus_records_typed_event_objects() -> None:
    bus = EventBus()

    event = bus.emit(
        TurnStartedEvent(
            session_id="session_test",
            turn_id="turn_1",
            user_input="hello",
        ),
    )

    assert event == TurnStartedEvent(
        session_id="session_test",
        turn_id="turn_1",
        user_input="hello",
    )
    assert bus.events == [event]
    assert not hasattr(event, "type")


def test_event_bus_has_no_hook_dispatch_boundary() -> None:
    fields = EventBus.__dataclass_fields__

    assert "hook_manager" not in fields
    assert "dispatch_errors" not in fields


class FailingSubscriber:
    def record(self, event: object) -> None:
        raise RuntimeError("boom")


def test_event_bus_records_subscriber_failures_without_raising() -> None:
    bus = EventBus(subscribers=[FailingSubscriber()])

    event = bus.emit(
        TurnStartedEvent(
            session_id="session_test",
            turn_id="turn_1",
            user_input="hello",
        ),
    )

    assert bus.events == [event]
    assert bus.subscriber_errors == ["boom"]


def test_phase8_multi_agent_events_are_typed_event_objects() -> None:
    bus = EventBus()

    events = [
        SubagentSpawnedEvent(
            parent_agent_id="parent",
            child_agent_id="child",
            task_id="task_1",
        ),
        AgentTaskDispatchedEvent(
            from_agent_id="parent",
            to_agent_id="expert",
            task_id="task_2",
        ),
        AgentTaskCompletedEvent(
            agent_id="child",
            task_id="task_1",
            status="completed",
            elapsed_seconds=0.5,
        ),
        AgentTaskFailedEvent(
            agent_id="child",
            task_id="task_3",
            error="boom",
        ),
        AgentTaskCancelledEvent(agent_id="child", task_id="task_4"),
        AgentInboxBackpressureEvent(
            agent_id="parent",
            pending_count=100,
            max_pending_envelopes=100,
        ),
        AgentTaskLateResultReceivedEvent(
            agent_id="child",
            task_id="task_5",
            final_status="timeout",
        ),
        AgentContinuationFailedEvent(
            parent_agent_id="parent",
            error="provider failed",
        ),
    ]

    for event in events:
        bus.emit(event)

    assert bus.events == events
    assert all(not hasattr(event, "type") for event in events)
