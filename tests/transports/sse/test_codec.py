from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from agentos.distributed.models import (
    LiveContentDelta,
    LiveContextLoaded,
    LiveFinalResult,
    LivePlanUpdated,
    LiveSkillLoaded,
    LiveStatusUpdate,
    LiveToolStatus,
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnStarted,
    LiveTurnWaiting,
    ReplayItem,
    RunEventEnvelope,
    StreamGap,
)
from agentos.transports.sse.codec import (
    encode_gap,
    encode_heartbeat,
    encode_replay_event,
    is_terminal_event,
)
from agentos.transports.sse.cursors import decode_cursor


NOW = datetime(2026, 7, 21, 12, 30, tzinfo=UTC)


def _replay(event: object) -> ReplayItem:
    return ReplayItem(
        "123-0",
        RunEventEnvelope(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            execution_attempt=2,
            event_sequence=3,
            event=event,  # type: ignore[arg-type]
            occurred_at=NOW,
        ),
    )


def _parse_frame(frame: str) -> tuple[str | None, str, dict[str, object]]:
    lines = frame.rstrip("\n").splitlines()
    values = {
        key: value.lstrip()
        for key, value in (line.split(":", 1) for line in lines)
    }
    return values.get("id"), values["event"], json.loads(values["data"])


@pytest.mark.parametrize(
    ("event", "kind", "payload"),
    [
        (LiveTurnStarted(), "turn_started", {}),
        (LiveStatusUpdate("provider", "working"), "status_update", {"stage": "provider", "message": "working"}),
        (LiveContextLoaded("attachment"), "context_loaded", {"source": "attachment"}),
        (LiveSkillLoaded("drawing"), "skill_loaded", {"skill_name": "drawing"}),
        (LivePlanUpdated("step", "updated"), "plan_updated", {"summary": "step", "status": "updated"}),
        (LiveContentDelta(0, "answer"), "content_delta", {"index": 0, "text": "answer"}),
        (
            LiveToolStatus("load_attachment", "call_1", "started"),
            "tool_started",
            {"tool_name": "load_attachment", "tool_call_id": "call_1", "status": "started"},
        ),
        (LiveFinalResult("done"), "final_result", {"content": "done"}),
        (LiveTurnCompleted(), "turn_completed", {}),
        (
            LiveTurnWaiting("timer", "timer_1", NOW),
            "turn_waiting",
            {
                "kind": "timer",
                "handle": "timer_1",
                "not_before": "2026-07-21T12:30:00.000000Z",
            },
        ),
        (LiveTurnFailed(), "turn_failed", {}),
        (LiveTurnCancelled(), "turn_cancelled", {}),
    ],
)
def test_all_live_run_events_have_explicit_stable_frames(
    event: object,
    kind: str,
    payload: dict[str, object],
) -> None:
    frame = encode_replay_event(_replay(event))
    cursor, actual_kind, data = _parse_frame(frame)

    assert frame.endswith("\n\n")
    assert actual_kind == kind
    assert data == {
        "session_id": "session_1",
        "run_id": "run_1",
        "turn_id": "turn_1",
        "execution_attempt": 2,
        "event_sequence": 3,
        "occurred_at": "2026-07-21T12:30:00.000000Z",
        "event": payload,
    }
    assert cursor is not None
    assert decode_cursor(
        cursor,
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
    ) == "123-0"
    assert "tenant_1" not in frame


def test_replay_event_has_byte_level_golden() -> None:
    frame = encode_replay_event(_replay(LiveContentDelta(0, "answer")))

    assert frame == (
        "id: eyJwb3NpdGlvbiI6IjEyMy0wIiwic2NvcGUiOiI1NjI4MjQwYmFkMWI1YTZiZWFi"
        "ZGM4OGYzN2QyOTIyNTk3NDNmYTM5MjE2Yzg5YzBhNTRkNTg1ZjhjNzI2NDRiIiwidm"
        "Vyc2lvbiI6MX0\n"
        "event: content_delta\n"
        "data: {\"event\":{\"index\":0,\"text\":\"answer\"},\"event_sequence\":3,"
        "\"execution_attempt\":2,\"occurred_at\":\"2026-07-21T12:30:00.000000Z\","
        "\"run_id\":\"run_1\",\"session_id\":\"session_1\",\"turn_id\":\"turn_1\"}\n\n"
    )


def test_frame_json_escapes_untrusted_line_breaks() -> None:
    frame = encode_replay_event(_replay(LiveContentDelta(0, "line one\nevent: forged")))

    assert "\nevent: forged\n" not in frame
    assert "line one\\nevent: forged" in frame


def test_heartbeat_and_gap_have_no_cursor() -> None:
    gap = StreamGap(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        requested_cursor="1-0",
        oldest_available_cursor="2-0",
        reason="trimmed",
    )

    assert encode_heartbeat() == ": heartbeat\n\n"
    assert encode_gap(gap) == 'event: stream_gap\ndata: {"reason":"trimmed"}\n\n'
    assert "id:" not in encode_gap(gap)


@pytest.mark.parametrize(
    ("event", "terminal"),
    [
        (LiveFinalResult("done"), False),
        (LiveTurnCompleted(), True),
        (LiveTurnWaiting("human_input", "approval_1"), True),
        (LiveTurnFailed(), True),
        (LiveTurnCancelled(), True),
    ],
)
def test_only_turn_outcomes_are_terminal(event: object, terminal: bool) -> None:
    assert is_terminal_event(event) is terminal  # type: ignore[arg-type]
