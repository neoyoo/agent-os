from __future__ import annotations

from datetime import UTC, datetime
import json

from agentos.distributed.models import (
    LiveContentDelta,
    LiveContextLoaded,
    LiveFinalResult,
    LivePlanUpdated,
    LiveRunEvent,
    LiveSkillLoaded,
    LiveStatusUpdate,
    LiveToolStatus,
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnStarted,
    LiveTurnWaiting,
    ReplayItem,
    StreamGap,
)
from agentos.transports.sse.cursors import encode_cursor
from agentos.transports.sse.frames import SseCommentFrame, SseEventFrame


_TERMINAL_TYPES = (
    LiveTurnCompleted,
    LiveTurnWaiting,
    LiveTurnFailed,
    LiveTurnCancelled,
)


def encode_replay_event(item: ReplayItem) -> str:
    """把 typed replay item 编码为 scoped SSE frame。"""

    if type(item) is not ReplayItem:
        raise TypeError("item must be ReplayItem")
    envelope = item.event
    data = {
        "session_id": envelope.session_id,
        "run_id": envelope.run_id,
        "turn_id": envelope.turn_id,
        "execution_attempt": envelope.execution_attempt,
        "event_sequence": envelope.event_sequence,
        "occurred_at": _datetime(envelope.occurred_at),
        "event": _event_payload(envelope.event),
    }
    frame = SseEventFrame(
        event=envelope.event_kind,
        data=_json(data),
        event_id=encode_cursor(
            tenant_id=envelope.tenant_id,
            session_id=envelope.session_id,
            run_id=envelope.run_id,
            position=item.cursor,
        ),
    )
    return frame.render()


def encode_heartbeat() -> str:
    """编码无 cursor、无 data 的 heartbeat comment。"""

    return SseCommentFrame("heartbeat").render()


def encode_gap(gap: StreamGap) -> str:
    """编码单次 gap frame，不伪造可继续 cursor。"""

    if type(gap) is not StreamGap:
        raise TypeError("gap must be StreamGap")
    return SseEventFrame(
        event="stream_gap",
        data=_json({"reason": gap.reason}),
    ).render()


def is_terminal_event(event: LiveRunEvent) -> bool:
    """只把四类 Turn outcome 识别为 stream terminal。"""

    return type(event) in _TERMINAL_TYPES


def _event_payload(event: LiveRunEvent) -> dict[str, object]:
    event_type = type(event)
    if event_type in {LiveTurnStarted, LiveTurnCompleted, LiveTurnFailed, LiveTurnCancelled}:
        return {}
    if event_type is LiveStatusUpdate:
        return {"stage": event.stage, "message": event.message}
    if event_type is LiveContextLoaded:
        return {"source": event.source}
    if event_type is LiveSkillLoaded:
        return {"skill_name": event.skill_name}
    if event_type is LivePlanUpdated:
        return {"summary": event.summary, "status": event.status}
    if event_type is LiveContentDelta:
        return {"index": event.index, "text": event.text}
    if event_type is LiveToolStatus:
        return {
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "status": event.status,
        }
    if event_type is LiveFinalResult:
        return {"content": event.content}
    if event_type is LiveTurnWaiting:
        return {
            "kind": event.kind,
            "handle": event.handle,
            "not_before": _datetime(event.not_before),
        }
    raise TypeError("event must be an allowlisted LiveRunEvent")


def _json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = [
    "encode_gap",
    "encode_heartbeat",
    "encode_replay_event",
    "is_terminal_event",
]
