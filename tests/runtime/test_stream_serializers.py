import json

from agentos.runtime import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    PlanUpdated,
    SkillLoaded,
    StatusUpdate,
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


def test_event_to_json_serializes_observable_interaction_events() -> None:
    events = [
        StatusUpdate(stage="context", message="正在装载上下文。"),
        ContextLoaded(summary="已装载 1 条消息。"),
        SkillLoaded(skill_name="quote-skill", resource="SKILL.md", summary="loaded"),
        PlanUpdated(summary="先看图再加载 skill。", status="created"),
        FinalResult(content="完成"),
    ]

    payloads = [json.loads(event_to_json(event) or "{}") for event in events]

    assert [payload["type"] for payload in payloads] == [
        "status_update",
        "context_loaded",
        "skill_loaded",
        "plan_updated",
        "final_result",
    ]
    assert payloads[0]["message"] == "正在装载上下文。"
    assert payloads[2]["skill_name"] == "quote-skill"


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


def test_event_to_sse_serializes_turn_failure_with_non_deepcopyable_error() -> None:
    class NonDeepcopyableError(RuntimeError):
        def __deepcopy__(self, memo: object) -> object:
            raise TypeError("cannot deepcopy provider error")

    chunk = event_to_sse(TurnStreamFailed(error=NonDeepcopyableError("401 Unauthorized")))

    assert chunk is not None
    payload = json.loads(chunk.split("data: ", 1)[1])
    assert payload["type"] == "TurnStreamFailed"
    assert payload["error"] == "401 Unauthorized"
