from agentos.runtime import (
    EventBus,
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
