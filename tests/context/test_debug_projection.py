import pytest

from agentos.compression import CompressionIndex
from agentos.context import ContextRenderer, ContextState
from agentos.context.debug_projection import render_debug_projection
from agentos.messages import MessageRuntime
from agentos.observability.events import EventLog
from agentos.runtime import EventBus, TurnStartedEvent


def test_debug_projection_exposes_runtime_metadata_explicitly() -> None:
    messages = MessageRuntime()
    message = messages.append_user("hello")
    index = CompressionIndex()
    index.record("seg_1", [message.id])
    log = EventLog()
    EventBus(subscribers=[log]).emit(
        TurnStartedEvent(
            session_id="session_1",
            turn_id="turn_1",
            user_input="hello",
        ),
    )

    rendered = render_debug_projection(
        context_state=ContextState(working_state={"task_goal": "Debug."}),
        message_runtime=messages,
        compression_index=index,
        event_log=log,
        debug=True,
    )

    assert "session_id" in rendered
    assert "message_id" in rendered
    assert "compression_id" in rendered
    assert "seg_1" in rendered


def test_default_renderer_does_not_call_debug_projection() -> None:
    rendered = ContextRenderer().render(
        ContextState(working_state={"task_goal": "Debug."}),
    )

    assert "session_id" not in rendered
    assert "message_id" not in rendered
    assert "compression_id" not in rendered


def test_debug_projection_requires_explicit_debug_flag() -> None:
    with pytest.raises(ValueError, match="debug=True"):
        render_debug_projection(
            context_state=ContextState(),
            message_runtime=MessageRuntime(),
            compression_index=CompressionIndex(),
            event_log=EventLog(),
        )
