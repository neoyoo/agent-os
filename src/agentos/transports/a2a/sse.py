from __future__ import annotations

from dataclasses import fields
from datetime import datetime

from agentos.distributed.models import (
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnWaiting,
    RunEventEnvelope,
    StreamGap,
)
from agentos.transports.a2a._json_codec import compact_json_bytes
from agentos.transports.a2a.message_types import (
    A2ATaskState,
    A2ATaskStatus,
    A2ATaskStatusUpdateEvent,
)
from agentos.transports.a2a.operation_types import (
    A2AOperationError,
    A2AOperationResponse,
    A2ARequestId,
    A2AStreamResponse,
)
from agentos.transports.a2a.serialization import operation_response_to_dict


_CLOSING_EVENT_TYPES = frozenset(
    {LiveTurnCompleted, LiveTurnWaiting, LiveTurnFailed, LiveTurnCancelled},
)


def encode_a2a_initial_response(
    request_id: A2ARequestId,
    result: A2AStreamResponse,
) -> str:
    response = A2AOperationResponse(request_id=request_id, result=result)
    return _frame(operation_response_to_dict(response))


def encode_a2a_event(
    request_id: A2ARequestId,
    cursor: str,
    envelope: RunEventEnvelope,
) -> str:
    response = A2AOperationResponse(
        request_id=request_id,
        result=A2AStreamResponse(status_update=_status_update(envelope)),
    )
    return _frame(operation_response_to_dict(response), event_id=cursor)


def encode_a2a_gap(
    gap: StreamGap,
    *,
    request_id: A2ARequestId,
    opaque_request_id: str,
) -> str:
    if type(gap) is not StreamGap:
        raise TypeError("gap must be StreamGap")
    error = A2AOperationError(
        -32603,
        data=(
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "STREAM_GAP",
                "domain": "a2a-protocol.org",
                "metadata": {"requestId": opaque_request_id},
            },
        ),
    )
    response = A2AOperationResponse(request_id=request_id, error=error)
    return _frame(operation_response_to_dict(response))


def encode_a2a_heartbeat() -> str:
    return ": heartbeat\n\n"


def is_a2a_terminal_event(envelope: RunEventEnvelope) -> bool:
    return type(envelope.event) in _CLOSING_EVENT_TYPES


def _status_update(envelope: RunEventEnvelope) -> A2ATaskStatusUpdateEvent:
    event = envelope.event
    event_type = type(event)
    state = A2ATaskState.TASK_STATE_WORKING
    if event_type is LiveTurnWaiting:
        state = (
            A2ATaskState.TASK_STATE_INPUT_REQUIRED
            if event.kind == "human_input"
            else A2ATaskState.TASK_STATE_WORKING
        )
    elif event_type is LiveTurnCompleted:
        state = A2ATaskState.TASK_STATE_COMPLETED
    elif event_type is LiveTurnFailed:
        state = A2ATaskState.TASK_STATE_FAILED
    elif event_type is LiveTurnCancelled:
        state = A2ATaskState.TASK_STATE_CANCELED
    return A2ATaskStatusUpdateEvent(
        task_id=envelope.run_id,
        context_id=envelope.session_id,
        status=A2ATaskStatus(state),
        metadata={
            "agentosEventKind": envelope.event_kind,
            "agentosEvent": _event_payload(event),
        },
    )


def _event_payload(event: object) -> dict[str, object]:
    payload: dict[str, object] = {}
    for field in fields(event):
        value = getattr(event, field.name)
        payload[field.name] = (
            value.isoformat() if isinstance(value, datetime) else value
        )
    return payload


def _frame(data: dict[str, object], *, event_id: str | None = None) -> str:
    lines = []
    if event_id is not None:
        if "\n" in event_id or "\r" in event_id:
            raise ValueError("event id is invalid")
        lines.append(f"id: {event_id}")
    lines.append(f"data: {compact_json_bytes(data).decode('utf-8')}")
    return "\n".join(lines) + "\n\n"


__all__ = [
    "encode_a2a_event",
    "encode_a2a_gap",
    "encode_a2a_heartbeat",
    "encode_a2a_initial_response",
    "is_a2a_terminal_event",
]
