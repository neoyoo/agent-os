from agentos.runtime import (
    EventBus,
    SessionState,
    TurnStartedEvent,
)


def test_session_state_creates_ordered_turns() -> None:
    session = SessionState(id="session_test")
    session.start()

    first = session.new_turn("first user message")
    second = session.new_turn("second user message")

    assert session.status == "running"
    assert first.id == "turn_1"
    assert first.user_input == "first user message"
    assert second.id == "turn_2"
    assert second.user_input == "second user message"


def test_turn_state_tracks_tool_iterations_and_completion() -> None:
    session = SessionState(id="session_test")
    turn = session.new_turn("use a tool")

    turn.increment_tool_iteration()
    turn.complete()

    assert turn.tool_iterations == 1
    assert turn.status == "completed"


class RecordingSubscriber:
    def __init__(self) -> None:
        self.events: list[object] = []

    def record(self, event: object) -> None:
        self.events.append(event)


def test_event_bus_records_typed_events_and_notifies_subscribers_in_order() -> None:
    subscriber = RecordingSubscriber()
    bus = EventBus(subscribers=[subscriber])

    event = bus.emit(
        TurnStartedEvent(
            user_input="hello",
            session_id="session_test",
            turn_id="turn_1",
        ),
    )

    assert event == TurnStartedEvent(
        user_input="hello",
        session_id="session_test",
        turn_id="turn_1",
    )
    assert bus.events == [event]
    assert subscriber.events == [event]
