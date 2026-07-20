from __future__ import annotations

from dataclasses import fields
from datetime import datetime
import json

from agentos.distributed.errors import DeliveryUnavailableError
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
    RequestScope,
    RunEventEnvelope,
    live_event_kind,
)
from agentos.distributed.redis._client import field_text


_EVENT_TYPES = {
    "turn_started": LiveTurnStarted,
    "status_update": LiveStatusUpdate,
    "context_loaded": LiveContextLoaded,
    "skill_loaded": LiveSkillLoaded,
    "plan_updated": LivePlanUpdated,
    "content_delta": LiveContentDelta,
    "tool_started": LiveToolStatus,
    "tool_completed": LiveToolStatus,
    "tool_failed": LiveToolStatus,
    "final_result": LiveFinalResult,
    "turn_completed": LiveTurnCompleted,
    "turn_waiting": LiveTurnWaiting,
    "turn_failed": LiveTurnFailed,
    "turn_cancelled": LiveTurnCancelled,
}


def encode_envelope(envelope: RunEventEnvelope) -> dict[str, object]:
    payload: dict[str, object] = {}
    for field in fields(envelope.event):
        value = getattr(envelope.event, field.name)
        payload[field.name] = value.isoformat() if isinstance(value, datetime) else value
    return {
        "tenant_id": envelope.tenant_id,
        "session_id": envelope.session_id,
        "run_id": envelope.run_id,
        "turn_id": envelope.turn_id,
        "execution_attempt": str(envelope.execution_attempt),
        "event_sequence": str(envelope.event_sequence),
        "event_kind": envelope.event_kind,
        "event_payload": json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        "occurred_at": envelope.occurred_at.isoformat(),
    }


def decode_scoped_envelope(
    value: object,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> RunEventEnvelope:
    envelope = _decode_envelope(value)
    if (
        envelope.tenant_id != scope.tenant_id
        or envelope.session_id != session_id
        or envelope.run_id != run_id
    ):
        raise DeliveryUnavailableError()
    return envelope


def _decode_envelope(value: object) -> RunEventEnvelope:
    try:
        kind = _required_field(value, "event_kind")
        payload = json.loads(_required_field(value, "event_payload"))
        if not isinstance(payload, dict):
            raise TypeError
        if kind == "turn_waiting" and payload.get("not_before") is not None:
            payload["not_before"] = datetime.fromisoformat(payload["not_before"])
        event = _EVENT_TYPES[kind](**payload)
        if live_event_kind(event) != kind:
            raise ValueError
        return RunEventEnvelope(
            tenant_id=_required_field(value, "tenant_id"),
            session_id=_required_field(value, "session_id"),
            run_id=_required_field(value, "run_id"),
            turn_id=_required_field(value, "turn_id"),
            execution_attempt=int(_required_field(value, "execution_attempt")),
            event_sequence=int(_required_field(value, "event_sequence")),
            event=event,
            occurred_at=datetime.fromisoformat(_required_field(value, "occurred_at")),
        )
    except (KeyError, TypeError, ValueError):
        raise DeliveryUnavailableError() from None


def _required_field(value: object, name: str) -> str:
    result = field_text(value, name)
    if result is None:
        raise ValueError
    return result
