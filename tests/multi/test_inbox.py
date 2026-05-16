import pytest

from agentos.events import AgentInboxBackpressureEvent, EventBus
from agentos.multi import AgentEnvelope, AgentInbox
from agentos.multi.inbox import AgentInboxFullError, AgentInboxMissingError
from agentos.multi.types import TaskResult


def result_envelope(envelope_id: str = "env_1") -> AgentEnvelope:
    return AgentEnvelope(
        envelope_id=envelope_id,
        from_agent_id="child",
        to_agent_id="parent",
        type="task_result",
        payload=TaskResult(
            task_id="task_1",
            status="completed",
            summary="done",
        ),
        created_at=1.0,
    )


def test_inbox_send_collect_and_wait_without_polling() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("parent")

    assert inbox.wait("parent", timeout=0.01) is False

    envelope = result_envelope()
    inbox.send(envelope)

    assert inbox.has_pending("parent")
    assert inbox.wait("parent", timeout=0.01) is True
    assert inbox.collect_envelopes("parent") == [envelope]
    assert not inbox.has_pending("parent")
    assert inbox.wait("parent", timeout=0.01) is False


def test_inbox_fails_closed_for_missing_inbox() -> None:
    inbox = AgentInbox()

    with pytest.raises(AgentInboxMissingError, match="missing inbox"):
        inbox.send(result_envelope())

    with pytest.raises(AgentInboxMissingError, match="missing inbox"):
        inbox.collect("parent")


def test_inbox_remove_makes_future_send_fail_closed() -> None:
    inbox = AgentInbox()
    inbox.create_inbox("parent")
    inbox.remove_inbox("parent")

    with pytest.raises(AgentInboxMissingError):
        inbox.send(result_envelope())


def test_inbox_backpressure_rejects_send_and_emits_event() -> None:
    bus = EventBus()
    inbox = AgentInbox(max_pending_envelopes=1, event_bus=bus)
    inbox.create_inbox("parent")
    inbox.send(result_envelope("env_1"))

    with pytest.raises(AgentInboxFullError, match="inbox is full"):
        inbox.send(result_envelope("env_2"))

    assert bus.events == [
        AgentInboxBackpressureEvent(
            agent_id="parent",
            pending_count=1,
            max_pending_envelopes=1,
        ),
    ]
    assert [delivery.envelope.envelope_id for delivery in inbox.collect("parent")] == [
        "env_1",
    ]
