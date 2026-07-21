from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos._json_values import freeze_json_mapping
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
from agentos.transports.run_stream import (
    RunStreamGapProjection,
    project_replay_item,
    project_stream_gap_projection,
)
from agentos.transports.websocket import (
    ErrorFrame,
    EventFrame,
    ReceiptFrame,
    ResumeCursor,
    StreamGapFrame,
    SubmitCommandReceiptData,
    SubmitRunReceiptData,
    SubscriptionReceiptData,
    encode_server_frame,
)


NOW = datetime(2026, 7, 21, 12, 30, tzinfo=UTC)
CURSOR = (
    "eyJwb3NpdGlvbiI6IjEyMy0wIiwic2NvcGUiOiI1NjI4MjQwYmFkMWI1YTZiZWFi"
    "ZGM4OGYzN2QyOTIyNTk3NDNmYTM5MjE2Yzg5YzBhNTRkNTg1ZjhjNzI2NDRiIiwidm"
    "Vyc2lvbiI6MX0"
)


def _projection(event: object):
    return project_replay_item(
        ReplayItem(
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
        ),
    )


@pytest.mark.parametrize(
    ("event", "kind", "payload"),
    [
        (LiveTurnStarted(), "turn_started", "{}"),
        (
            LiveStatusUpdate("provider", "working"),
            "status_update",
            '{"message":"working","stage":"provider"}',
        ),
        (
            LiveContextLoaded("attachment"),
            "context_loaded",
            '{"source":"attachment"}',
        ),
        (
            LiveSkillLoaded("drawing"),
            "skill_loaded",
            '{"skill_name":"drawing"}',
        ),
        (
            LivePlanUpdated("step", "updated"),
            "plan_updated",
            '{"status":"updated","summary":"step"}',
        ),
        (
            LiveContentDelta(0, "answer"),
            "content_delta",
            '{"index":0,"text":"answer"}',
        ),
        (
            LiveToolStatus("load_attachment", "call_1", "started"),
            "tool_started",
            '{"status":"started","tool_call_id":"call_1",'
            '"tool_name":"load_attachment"}',
        ),
        (
            LiveToolStatus("load_attachment", "call_1", "completed"),
            "tool_completed",
            '{"status":"completed","tool_call_id":"call_1",'
            '"tool_name":"load_attachment"}',
        ),
        (
            LiveToolStatus("load_attachment", "call_1", "failed"),
            "tool_failed",
            '{"status":"failed","tool_call_id":"call_1",'
            '"tool_name":"load_attachment"}',
        ),
        (LiveFinalResult("done"), "final_result", '{"content":"done"}'),
        (LiveTurnCompleted(), "turn_completed", "{}"),
        (
            LiveTurnWaiting("timer", "timer_1", NOW),
            "turn_waiting",
            '{"handle":"timer_1","kind":"timer",'
            '"not_before":"2026-07-21T12:30:00.000000Z"}',
        ),
        (LiveTurnFailed(), "turn_failed", "{}"),
        (LiveTurnCancelled(), "turn_cancelled", "{}"),
    ],
)
def test_all_live_event_kinds_have_exact_websocket_json(
    event: object,
    kind: str,
    payload: str,
) -> None:
    frame = EventFrame("session_1", "run_1", _projection(event))

    assert encode_server_frame(frame) == (
        f'{{"cursor":"{CURSOR}","data":{{"event":{payload},'
        '"event_sequence":3,"execution_attempt":2,'
        '"occurred_at":"2026-07-21T12:30:00.000000Z",'
        '"run_id":"run_1","session_id":"session_1",'
        f'"turn_id":"turn_1"}},"event_kind":"{kind}",'
        '"run_id":"run_1","session_id":"session_1","type":"event"}'
    )


def test_receipt_gap_and_error_frames_have_exact_sorted_json() -> None:
    assert encode_server_frame(
        ReceiptFrame(
            "req_1",
            "submit_run",
            SubmitRunReceiptData("s1", "r1", "req_1", 1, False),
        ),
    ) == (
        '{"data":{"aggregate_version":1,"duplicate":false,"run_id":"r1",'
        '"session_id":"s1","submission_id":"req_1"},'
        '"operation":"submit_run","request_id":"req_1","type":"receipt"}'
    )
    assert encode_server_frame(
        ReceiptFrame(
            "req_2",
            "submit_command",
            SubmitCommandReceiptData("r1", "req_2", "cancel", 2, True),
        ),
    ) == (
        '{"data":{"aggregate_version":2,"command_id":"req_2",'
        '"duplicate":true,"kind":"cancel","run_id":"r1"},'
        '"operation":"submit_command","request_id":"req_2",'
        '"type":"receipt"}'
    )
    assert encode_server_frame(
        ReceiptFrame(
            "req_3",
            "subscribe_run",
            SubscriptionReceiptData("s1", "r1"),
        ),
    ) == (
        '{"data":{"run_id":"r1","session_id":"s1"},'
        '"operation":"subscribe_run","request_id":"req_3",'
        '"type":"receipt"}'
    )
    assert encode_server_frame(
        ReceiptFrame(
            "req_4",
            "unsubscribe_run",
            SubscriptionReceiptData("s1", "r1"),
        ),
    ) == (
        '{"data":{"run_id":"r1","session_id":"s1"},'
        '"operation":"unsubscribe_run","request_id":"req_4",'
        '"type":"receipt"}'
    )
    gap = StreamGap("tenant_1", "s1", "r1", "1-0", "2-0", "trimmed")
    assert encode_server_frame(
        StreamGapFrame("s1", "r1", project_stream_gap_projection(gap)),
    ) == (
        '{"reason":"trimmed","run_id":"r1","session_id":"s1",'
        '"type":"stream_gap"}'
    )
    assert encode_server_frame(
        ErrorFrame("req_4", "invalid_request", "invalid request"),
    ) == (
        '{"code":"invalid_request","message":"invalid request",'
        '"request_id":"req_4","type":"error"}'
    )


def test_error_resource_fields_and_resume_cursors_are_exact_and_bounded() -> None:
    background = ErrorFrame(
        None,
        "stream_unavailable",
        "stream unavailable",
        session_id="s1",
        run_id="r1",
    )
    slow = ErrorFrame(
        None,
        "slow_consumer",
        "slow consumer",
        resume_cursors=(
            ResumeCursor("s2", "r2", "cursor_2"),
            ResumeCursor("s1", "r1", None),
        ),
    )

    assert encode_server_frame(background) == (
        '{"code":"stream_unavailable","message":"stream unavailable",'
        '"request_id":null,"run_id":"r1","session_id":"s1",'
        '"type":"error"}'
    )
    assert encode_server_frame(slow) == (
        '{"code":"slow_consumer","message":"slow consumer",'
        '"request_id":null,"resume_cursors":['
        '{"cursor":null,"run_id":"r1","session_id":"s1"},'
        '{"cursor":"cursor_2","run_id":"r2","session_id":"s2"}],'
        '"type":"error"}'
    )

    with pytest.raises(ValueError, match="request-bound error"):
        ErrorFrame(
            "req_1",
            "not_found",
            "not found",
            session_id="s1",
            run_id="r1",
        )
    with pytest.raises(ValueError, match="resource fields"):
        ErrorFrame(None, "not_found", "not found", session_id="s1")
    with pytest.raises(ValueError, match="slow_consumer"):
        ErrorFrame(
            None,
            "not_found",
            "not found",
            resume_cursors=(ResumeCursor("s1", "r1", None),),
        )


def test_receipt_operation_and_identity_cannot_drift_from_typed_data() -> None:
    with pytest.raises(ValueError, match="receipt data"):
        ReceiptFrame(
            "req_1",
            "submit_command",
            SubmitRunReceiptData("s1", "r1", "req_1", 1, False),
        )
    with pytest.raises(ValueError, match="request_id"):
        ReceiptFrame(
            "req_1",
            "submit_run",
            SubmitRunReceiptData("s1", "r1", "other", 1, False),
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        SubmitRunReceiptData("s1", "r1", "req_1", True, False)


def test_event_frame_requires_matching_shared_projection_scope() -> None:
    with pytest.raises(ValueError, match="projection scope"):
        EventFrame("other", "run_1", _projection(LiveTurnStarted()))


def test_gap_frame_rejects_forged_json_projection() -> None:
    forged = freeze_json_mapping(
        {
            "type": "error",
            "session_id": "other_session",
            "run_id": "other_run",
            "reason": "trimmed",
        },
    )

    with pytest.raises(TypeError, match="RunStreamGapProjection"):
        StreamGapFrame("s1", "r1", forged)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="projection scope"):
        StreamGapFrame(
            "s1",
            "r1",
            RunStreamGapProjection("other", "r1", "trimmed"),
        )
