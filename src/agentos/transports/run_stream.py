from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
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
    live_event_kind,
)
from agentos.transports._run_stream_cursor import (
    CURSOR_VERSION,
    MAX_CURSOR_BYTES,
    RunStreamCursorError,
    decode_cursor,
    encode_cursor,
)


_TERMINAL_TYPES = (
    LiveTurnCompleted,
    LiveTurnWaiting,
    LiveTurnFailed,
    LiveTurnCancelled,
)


@dataclass(frozen=True, slots=True)
class RunStreamEventProjection:
    """Allowlisted JSON projection of one live event."""

    kind: str
    data: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class RunStreamReplayProjection:
    """Public cursor and payload projected from one replay item."""

    cursor: str
    event: RunStreamEventProjection
    data: FrozenJsonObject


@dataclass(frozen=True, slots=True)
class RunStreamGapProjection:
    """Allowlisted wire-neutral reason for one replay gap."""

    session_id: str
    run_id: str
    reason: Literal["trimmed", "unavailable"]

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.session_id, self.run_id)):
            raise ValueError("gap scope is invalid")
        if self.reason not in {"trimmed", "unavailable"}:
            raise ValueError("gap reason is invalid")


def project_live_event(event: LiveRunEvent) -> RunStreamEventProjection:
    """Project an allowlisted live event to a JSON-safe object."""

    event_type = type(event)
    if event_type in {
        LiveTurnStarted,
        LiveTurnCompleted,
        LiveTurnFailed,
        LiveTurnCancelled,
    }:
        data: dict[str, object] = {}
    elif event_type is LiveStatusUpdate:
        data = {"stage": event.stage, "message": event.message}
    elif event_type is LiveContextLoaded:
        data = {"source": event.source}
    elif event_type is LiveSkillLoaded:
        data = {"skill_name": event.skill_name}
    elif event_type is LivePlanUpdated:
        data = {"summary": event.summary, "status": event.status}
    elif event_type is LiveContentDelta:
        data = {"index": event.index, "text": event.text}
    elif event_type is LiveToolStatus:
        data = {
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "status": event.status,
        }
    elif event_type is LiveFinalResult:
        data = {"content": event.content}
    elif event_type is LiveTurnWaiting:
        data = {
            "kind": event.kind,
            "handle": event.handle,
            "not_before": (
                event.not_before.isoformat() if event.not_before is not None else None
            ),
        }
    else:
        raise TypeError("event must be an allowlisted LiveRunEvent")
    return RunStreamEventProjection(
        kind=live_event_kind(event),
        data=freeze_json_mapping(data),
    )


def project_replay_item(item: ReplayItem) -> RunStreamReplayProjection:
    """Project one replay item to the complete SSE/WebSocket payload."""

    if type(item) is not ReplayItem:
        raise TypeError("item must be ReplayItem")
    envelope = item.event
    event = project_live_event(envelope.event)
    event_data = event.data
    if type(envelope.event) is LiveTurnWaiting:
        event_data = freeze_json_mapping(
            {
                "kind": envelope.event.kind,
                "handle": envelope.event.handle,
                "not_before": _wire_datetime(envelope.event.not_before),
            },
        )
    payload = freeze_json_mapping(
        {
            "session_id": envelope.session_id,
            "run_id": envelope.run_id,
            "turn_id": envelope.turn_id,
            "execution_attempt": envelope.execution_attempt,
            "event_sequence": envelope.event_sequence,
            "occurred_at": _wire_datetime(envelope.occurred_at),
            "event": event_data,
        },
    )
    cursor = encode_cursor(
        tenant_id=envelope.tenant_id,
        session_id=envelope.session_id,
        run_id=envelope.run_id,
        position=item.cursor,
    )
    return RunStreamReplayProjection(cursor=cursor, event=event, data=payload)


def project_stream_gap(gap: StreamGap) -> FrozenJsonObject:
    """Project a replay gap to shared JSON without exposing storage cursors."""

    projection = project_stream_gap_projection(gap)
    return freeze_json_mapping({"reason": projection.reason})


def project_stream_gap_projection(gap: StreamGap) -> RunStreamGapProjection:
    """Project a replay gap to a strict transport-neutral value."""

    if type(gap) is not StreamGap:
        raise TypeError("gap must be StreamGap")
    return RunStreamGapProjection(gap.session_id, gap.run_id, gap.reason)


def is_terminal_event(event: LiveRunEvent) -> bool:
    """Return whether an event closes the current run subscription."""

    return type(event) in _TERMINAL_TYPES


def _wire_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = [
    "CURSOR_VERSION",
    "MAX_CURSOR_BYTES",
    "RunStreamCursorError",
    "RunStreamEventProjection",
    "RunStreamGapProjection",
    "RunStreamReplayProjection",
    "decode_cursor",
    "encode_cursor",
    "is_terminal_event",
    "project_live_event",
    "project_replay_item",
    "project_stream_gap",
    "project_stream_gap_projection",
]
