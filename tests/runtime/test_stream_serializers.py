import json

from agentos.runtime import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamFailed,
    TurnStreamCompleted,
    event_to_json,
    event_to_sse,
)


def test_event_to_sse_serializes_content_delta() -> None:
    chunk = event_to_sse(AssistantContentDelta(index=1, text="hello"))

    assert chunk is not None
    assert chunk.startswith("event: content_delta\n")
    assert chunk.endswith("\n\n")
    assert json.loads(chunk.split("data: ", 1)[1]) == {
        "type": "content_delta",
        "index": 1,
        "text": "hello",
    }


def test_event_to_sse_can_hide_thinking() -> None:
    assert (
        event_to_sse(
            AssistantThinkingDelta(index=1, text="secret"),
            show_thinking=False,
        )
        is None
    )


def test_event_to_sse_serializes_tool_and_done() -> None:
    tool_chunk = event_to_sse(
        ToolStreamStarted(tool_name="read_file", tool_call_id="call_1"),
    )
    done_chunk = event_to_sse(TurnStreamCompleted(content="ok"))

    assert tool_chunk is not None
    assert tool_chunk.startswith("event: tool_started")
    assert done_chunk is not None
    assert done_chunk.startswith("event: done")


def test_event_to_json_serializes_event_type() -> None:
    payload = json.loads(event_to_json(AssistantContentDelta(index=1, text="hello")))

    assert payload == {
        "type": "content_delta",
        "index": 1,
        "text": "hello",
    }


def test_event_to_json_serializes_tool_failure_error_message() -> None:
    payload = json.loads(
        event_to_json(
            ToolStreamFailed(
                tool_name="read_file",
                tool_call_id="call_1",
                error=RuntimeError("permission denied"),
            ),
        ),
    )

    assert payload["type"] == "tool_failed"
    assert payload["error"] == "permission denied"


def test_event_to_sse_serializes_turn_failure_error_message() -> None:
    chunk = event_to_sse(TurnStreamFailed(error=RuntimeError("provider failed")))

    assert chunk is not None
    assert json.loads(chunk.split("data: ", 1)[1])["error"] == "provider failed"
