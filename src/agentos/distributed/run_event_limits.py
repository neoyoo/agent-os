from __future__ import annotations

from dataclasses import fields
from datetime import datetime
import json

from agentos.distributed.errors import RunEventTooLargeError
from agentos.distributed.models import RunEventEnvelope


MAX_RUN_EVENT_JSON_BYTES = 256 * 1024


def canonical_run_event_json(envelope: RunEventEnvelope) -> bytes:
    """Serialize one live event using the canonical persistence envelope."""

    payload = canonical_run_event_payload(envelope)
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_run_event_payload(envelope: RunEventEnvelope) -> dict[str, object]:
    if type(envelope) is not RunEventEnvelope:
        raise TypeError("envelope must be RunEventEnvelope")
    event_payload: dict[str, object] = {}
    for field in fields(envelope.event):
        value = getattr(envelope.event, field.name)
        event_payload[field.name] = (
            value.isoformat() if isinstance(value, datetime) else value
        )
    return {
        "event": event_payload,
        "event_kind": envelope.event_kind,
        "event_sequence": envelope.event_sequence,
        "execution_attempt": envelope.execution_attempt,
        "occurred_at": envelope.occurred_at.isoformat(),
        "run_id": envelope.run_id,
        "session_id": envelope.session_id,
        "tenant_id": envelope.tenant_id,
        "turn_id": envelope.turn_id,
    }


def require_run_event_size(envelope: RunEventEnvelope) -> None:
    if len(canonical_run_event_json(envelope)) > MAX_RUN_EVENT_JSON_BYTES:
        raise RunEventTooLargeError()


__all__ = [
    "MAX_RUN_EVENT_JSON_BYTES",
    "canonical_run_event_json",
    "canonical_run_event_payload",
    "require_run_event_size",
]
