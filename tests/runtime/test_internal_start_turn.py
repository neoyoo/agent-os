from __future__ import annotations

from agentos.context import ContextRuntime
from agentos.events import EventBus, TurnStartedEvent, UserMessageAppendedEvent
from agentos.messages import MessageRuntime
from agentos.runtime.continuation import ContinuationRuntime
from agentos.runtime.execution import (
    AcceptedInternalStartInput,
    ApplyAcceptedInput,
    RestoreAcceptedTurn,
    RunExecutionCursor,
)
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import TurnStreamStarted
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle
from agentos.runtime.turn_preparation import prepare_execution_turn
from tests.planning._async import async_test


def _accepted() -> AcceptedInternalStartInput:
    return AcceptedInternalStartInput(
        run_id="run_1",
        submission_id="team_submission_1",
        source_kind="team_message",
        source_payload={
            "recipient_agent_id": "agent_2",
            "team_id": "team_1",
            "action": "team_read_messages",
            "message_id": "team_msg_<unsafe>",
        },
        turn_id="turn_stable",
    )


def _lifecycle() -> tuple[TurnLifecycle, MessageRuntime, ContinuationRuntime, EventBus]:
    messages = MessageRuntime()
    continuation = ContinuationRuntime()
    events = EventBus()
    return (
        TurnLifecycle(
            context_runtime=ContextRuntime(),
            message_runtime=messages,
            session_state=SessionState("session_1"),
            continuation_runtime=continuation,
            event_bus=events,
        ),
        messages,
        continuation,
        events,
    )


@async_test
async def test_internal_start_apply_and_restore_rebuild_identical_ephemeral_data() -> None:
    accepted = _accepted()
    apply_lifecycle, apply_messages, apply_continuation, apply_events = _lifecycle()

    turn, stream_events = await prepare_execution_turn(
        turns=apply_lifecycle,
        input=accepted,
        preparation=ApplyAcceptedInput(),
    )

    assert turn == TurnState("turn_stable", "")
    assert stream_events == (TurnStreamStarted(""),)
    assert apply_messages.store.all() == []
    assert apply_events.events == [
        TurnStartedEvent(
            session_id="session_1",
            turn_id="turn_stable",
            user_input="",
            is_continuation=True,
        ),
    ]
    assert not any(isinstance(event, UserMessageAppendedEvent) for event in apply_events.events)
    apply_xml = apply_continuation.inputs()[0].content[0].text  # type: ignore[union-attr]
    assert apply_xml == (
        '<continuation-data protocol="agentos.continuation" version="1.0"\n'
        '    origin="runtime" authority="context-data" persistence="ephemeral"\n'
        '    visibility="internal" source="internal-start" kind="team_message">\n'
        '  <payload-json>{&quot;action&quot;:&quot;team_read_messages&quot;,'
        '&quot;message_id&quot;:&quot;team_msg_&lt;unsafe&gt;&quot;,'
        '&quot;recipient_agent_id&quot;:&quot;agent_2&quot;,'
        '&quot;team_id&quot;:&quot;team_1&quot;}</payload-json>\n'
        '</continuation-data>\n'
    )

    restore_lifecycle, restore_messages, restore_continuation, restore_events = _lifecycle()
    restored, restored_events = await prepare_execution_turn(
        turns=restore_lifecycle,
        input=accepted,
        preparation=RestoreAcceptedTurn(
            RunExecutionCursor("turn_stable", "before_provider", 0),
        ),
    )

    assert restored == TurnState("turn_stable", "")
    assert restored_events == ()
    assert restore_messages.store.all() == []
    assert restore_events.events == []
    restore_xml = restore_continuation.inputs()[0].content[0].text  # type: ignore[union-attr]
    assert restore_xml == apply_xml
