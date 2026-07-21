from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos.distributed.models import (
    LiveContentDelta,
    LiveTurnCompleted,
    LiveTurnWaiting,
    ReplayItem,
    RunEventEnvelope,
    StreamGap,
)
from agentos.transports.run_stream import (
    RunStreamGapProjection,
    decode_cursor,
    is_terminal_event,
    project_live_event,
    project_replay_item,
    project_stream_gap,
    project_stream_gap_projection,
)


NOW = datetime(2026, 7, 21, 12, 30, tzinfo=UTC)
CURSOR = (
    "eyJwb3NpdGlvbiI6IjEyMy0wIiwic2NvcGUiOiI1NjI4MjQwYmFkMWI1YTZiZWFi"
    "ZGM4OGYzN2QyOTIyNTk3NDNmYTM5MjE2Yzg5YzBhNTRkNTg1ZjhjNzI2NDRiIiwidm"
    "Vyc2lvbiI6MX0"
)


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


def test_live_and_replay_projection_share_allowlisted_event() -> None:
    item = _replay(LiveContentDelta(0, "answer"))

    live = project_live_event(item.event.event)
    replay = project_replay_item(item)

    assert live.kind == "content_delta"
    assert live.data == {"index": 0, "text": "answer"}
    assert replay.cursor == CURSOR
    assert replay.event == live
    assert replay.data == {
        "session_id": "session_1",
        "run_id": "run_1",
        "turn_id": "turn_1",
        "execution_attempt": 2,
        "event_sequence": 3,
        "occurred_at": "2026-07-21T12:30:00.000000Z",
        "event": {"index": 0, "text": "answer"},
    }
    assert decode_cursor(
        replay.cursor,
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
    ) == "123-0"


def test_replay_preserves_sse_time_while_live_projection_preserves_a2a_time() -> None:
    waiting = LiveTurnWaiting("timer", "timer_1", NOW)

    live = project_live_event(waiting)
    replay = project_replay_item(_replay(waiting))

    assert live.data["not_before"] == "2026-07-21T12:30:00+00:00"
    assert replay.data["event"] == {
        "kind": "timer",
        "handle": "timer_1",
        "not_before": "2026-07-21T12:30:00.000000Z",
    }


def test_gap_and_terminal_projection_are_wire_neutral() -> None:
    gap = StreamGap(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        requested_cursor="1-0",
        oldest_available_cursor="2-0",
        reason="trimmed",
    )

    assert project_stream_gap(gap) == {"reason": "trimmed"}
    assert project_stream_gap_projection(gap) == RunStreamGapProjection(
        "session_1",
        "run_1",
        "trimmed",
    )
    assert is_terminal_event(LiveTurnCompleted())
    assert not is_terminal_event(LiveContentDelta(0, "answer"))


def test_projection_rejects_non_allowlisted_values() -> None:
    with pytest.raises(TypeError, match="allowlisted LiveRunEvent"):
        project_live_event(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ReplayItem"):
        project_replay_item(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="StreamGap"):
        project_stream_gap(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="StreamGap"):
        project_stream_gap_projection(object())  # type: ignore[arg-type]


def test_gap_projection_rejects_non_protocol_reason() -> None:
    with pytest.raises(ValueError, match="gap reason"):
        RunStreamGapProjection(
            "session_1",
            "run_1",
            "forged",  # type: ignore[arg-type]
        )
