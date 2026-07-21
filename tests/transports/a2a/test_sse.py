from __future__ import annotations

from datetime import UTC, datetime
import json

from agentos.distributed.models import (
    LiveContentDelta,
    LiveTurnCompleted,
    LiveTurnWaiting,
    RunEventEnvelope,
    StreamGap,
)
from agentos.transports.a2a.message_types import (
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
)
from agentos.transports.a2a.operation_types import A2AStreamResponse
from agentos.transports.a2a.sse import (
    encode_a2a_event,
    encode_a2a_gap,
    encode_a2a_heartbeat,
    encode_a2a_initial_response,
    is_a2a_terminal_event,
)


def _envelope(event: object) -> RunEventEnvelope:
    return RunEventEnvelope(
        tenant_id="tenant_secret",
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        execution_attempt=2,
        event_sequence=3,
        event=event,  # type: ignore[arg-type]
        occurred_at=datetime(2026, 7, 21, 8, 30, tzinfo=UTC),
    )


def test_a2a_sse_uses_scoped_cursor_and_official_stream_wrapper() -> None:
    frame = encode_a2a_event(
        "rpc_1",
        "opaque-scoped-cursor",
        _envelope(LiveContentDelta(index=0, text="hello")),
    )

    assert frame.startswith("id: opaque-scoped-cursor\ndata: ")
    assert "event:" not in frame
    payload = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
    assert payload["result"]["statusUpdate"]["taskId"] == "run_1"
    assert payload["result"]["statusUpdate"]["status"]["state"] == (
        "TASK_STATE_WORKING"
    )
    assert '"final"' not in frame
    assert '"agentosEventKind":"content_delta"' in frame
    assert "tenant_secret" not in frame
    assert frame.endswith("\n\n")


def test_streaming_initial_snapshot_has_wrapper_and_no_sse_id() -> None:
    frame = encode_a2a_initial_response(
        "rpc_1",
        A2AStreamResponse(
            task=A2ATask(
                id="run_1",
                context_id="session_1",
                status=A2ATaskStatus(A2ATaskState.TASK_STATE_SUBMITTED),
            ),
        ),
    )

    assert frame.startswith("data: ")
    assert "id:" not in frame
    payload = json.loads(frame.removeprefix("data: ").strip())
    assert payload["result"]["task"]["id"] == "run_1"


def test_heartbeat_and_gap_do_not_invent_cursor_or_private_error_code() -> None:
    gap = StreamGap(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        requested_cursor="old-cursor",
        oldest_available_cursor="new-cursor",
        reason="trimmed",
    )

    assert encode_a2a_heartbeat() == ": heartbeat\n\n"
    frame = encode_a2a_gap(gap, request_id="rpc_1", opaque_request_id="opaque")
    assert frame.startswith("data: ")
    assert "event:" not in frame
    assert '"code":-32603' in frame
    assert '"message":"Internal error"' in frame
    assert '"reason":"STREAM_GAP"' in frame
    assert "old-cursor" not in frame


def test_terminal_and_interrupted_events_close_current_stream() -> None:
    assert is_a2a_terminal_event(_envelope(LiveTurnCompleted()))
    assert is_a2a_terminal_event(_envelope(LiveTurnWaiting("human_input", "wait_1")))
    assert not is_a2a_terminal_event(_envelope(LiveContentDelta(0, "x")))


def test_waiting_event_keeps_existing_datetime_wire_format() -> None:
    frame = encode_a2a_event(
        "rpc_1",
        "opaque-scoped-cursor",
        _envelope(
            LiveTurnWaiting(
                "timer",
                "timer_1",
                datetime(2026, 7, 21, 12, 30, tzinfo=UTC),
            ),
        ),
    )

    assert '"not_before":"2026-07-21T12:30:00+00:00"' in frame
