from __future__ import annotations

import json
from typing import Any, cast

from agentos.multi.types import (
    AgentEnvelope,
    AgentEnvelopeType,
    CoordinationMode,
    TaskRecord,
    TaskRequest,
    TaskResult,
    TaskStatus,
)


JsonDict = dict[str, Any]
_TASK_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "completed", "failed", "cancelled", "timeout"},
)
_COORDINATION_MODES: frozenset[str] = frozenset({"spawn", "dispatch"})
_ENVELOPE_TYPES: frozenset[str] = frozenset({"task_request", "task_result"})


def task_request_to_dict(request: TaskRequest) -> JsonDict:
    """序列化 TaskRequest。"""

    return {
        "task_id": request.task_id,
        "instruction": request.instruction,
        "allowed_tool_names": list(request.allowed_tool_names),
        "timeout_seconds": request.timeout_seconds,
        "trace_context": (
            None if request.trace_context is None else dict(request.trace_context)
        ),
    }


def task_request_from_dict(data: JsonDict) -> TaskRequest:
    """反序列化 TaskRequest。"""

    trace_context = data.get("trace_context")
    return TaskRequest(
        task_id=str(data["task_id"]),
        instruction=str(data["instruction"]),
        allowed_tool_names=tuple(
            str(name) for name in data.get("allowed_tool_names", [])
        ),
        timeout_seconds=float(data.get("timeout_seconds", 300)),
        trace_context=(
            None
            if trace_context is None
            else {str(key): str(value) for key, value in dict(trace_context).items()}
        ),
    )


def task_result_to_dict(result: TaskResult) -> JsonDict:
    """序列化 TaskResult。"""

    return {
        "task_id": result.task_id,
        "status": _task_status(result.status),
        "summary": result.summary,
        "artifacts": _json_safe_dict(
            result.artifacts,
            error_message="artifacts must be JSON serializable",
        ),
        "error": result.error,
        "elapsed_seconds": result.elapsed_seconds,
    }


def task_result_from_dict(data: JsonDict) -> TaskResult:
    """反序列化 TaskResult。"""

    return TaskResult(
        task_id=str(data["task_id"]),
        status=_task_status(data["status"]),
        summary=str(data["summary"]),
        artifacts=_json_safe_dict(
            dict(data.get("artifacts", {})),
            error_message="artifacts must be JSON serializable",
        ),
        error=None if data.get("error") is None else str(data["error"]),
        elapsed_seconds=float(data.get("elapsed_seconds", 0)),
    )


def task_record_to_dict(record: TaskRecord) -> JsonDict:
    """序列化 TaskRecord。"""

    return {
        "task_id": record.task_id,
        "mode": _coordination_mode(record.mode),
        "parent_agent_id": record.parent_agent_id,
        "target_agent_id": record.target_agent_id,
        "request": task_request_to_dict(record.request),
        "status": _task_status(record.status),
        "created_at": record.created_at,
        "deadline_at": record.deadline_at,
        "result": None if record.result is None else task_result_to_dict(record.result),
        "late_result": (
            None
            if record.late_result is None
            else task_result_to_dict(record.late_result)
        ),
        "completed_at": record.completed_at,
        "consumed_at": record.consumed_at,
        "worker_id": record.worker_id,
        "lease_expires_at": record.lease_expires_at,
        "attempt": record.attempt,
        "updated_at": record.updated_at,
        "version": record.version,
        "cancel_requested_at": record.cancel_requested_at,
        "result_notified_at": record.result_notified_at,
    }


def task_record_from_dict(data: JsonDict) -> TaskRecord:
    """反序列化 TaskRecord。"""

    return TaskRecord(
        task_id=str(data["task_id"]),
        mode=_coordination_mode(data["mode"]),
        parent_agent_id=str(data["parent_agent_id"]),
        target_agent_id=str(data["target_agent_id"]),
        request=task_request_from_dict(data["request"]),
        status=_task_status(data["status"]),
        created_at=float(data["created_at"]),
        deadline_at=float(data["deadline_at"]),
        result=(
            None if data.get("result") is None else task_result_from_dict(data["result"])
        ),
        late_result=(
            None
            if data.get("late_result") is None
            else task_result_from_dict(data["late_result"])
        ),
        completed_at=(
            None if data.get("completed_at") is None else float(data["completed_at"])
        ),
        consumed_at=(
            None if data.get("consumed_at") is None else float(data["consumed_at"])
        ),
        worker_id=None if data.get("worker_id") is None else str(data["worker_id"]),
        lease_expires_at=(
            None
            if data.get("lease_expires_at") is None
            else float(data["lease_expires_at"])
        ),
        attempt=int(data.get("attempt", 0)),
        updated_at=None if data.get("updated_at") is None else float(data["updated_at"]),
        version=int(data.get("version", 0)),
        cancel_requested_at=(
            None
            if data.get("cancel_requested_at") is None
            else float(data["cancel_requested_at"])
        ),
        result_notified_at=(
            None
            if data.get("result_notified_at") is None
            else float(data["result_notified_at"])
        ),
    )


def envelope_to_dict(envelope: AgentEnvelope) -> JsonDict:
    """序列化 AgentEnvelope。"""

    envelope_type = _envelope_type(envelope.type)
    if envelope_type == "task_request":
        if not isinstance(envelope.payload, TaskRequest):
            raise TypeError("task_request envelope payload must be TaskRequest")
        payload = task_request_to_dict(envelope.payload)
    else:
        if not isinstance(envelope.payload, TaskResult):
            raise TypeError("task_result envelope payload must be TaskResult")
        payload = task_result_to_dict(envelope.payload)
    return {
        "envelope_id": envelope.envelope_id,
        "from_agent_id": envelope.from_agent_id,
        "to_agent_id": envelope.to_agent_id,
        "type": envelope_type,
        "payload": payload,
        "created_at": envelope.created_at,
        "correlation_id": envelope.correlation_id,
    }


def envelope_from_dict(data: JsonDict) -> AgentEnvelope:
    """反序列化 AgentEnvelope。"""

    envelope_type = _envelope_type(data["type"])
    payload: TaskRequest | TaskResult
    if envelope_type == "task_request":
        if not _is_task_request_payload(data["payload"]):
            raise TypeError("task_request envelope payload must be TaskRequest data")
        payload = task_request_from_dict(data["payload"])
    else:
        if not _is_task_result_payload(data["payload"]):
            raise TypeError("task_result envelope payload must be TaskResult data")
        payload = task_result_from_dict(data["payload"])
    return AgentEnvelope(
        envelope_id=str(data["envelope_id"]),
        from_agent_id=str(data["from_agent_id"]),
        to_agent_id=str(data["to_agent_id"]),
        type=envelope_type,
        payload=payload,
        created_at=float(data["created_at"]),
        correlation_id=(
            None if data.get("correlation_id") is None else str(data["correlation_id"])
        ),
    )


def _task_status(value: object) -> TaskStatus:
    status = str(value)
    if status not in _TASK_STATUSES:
        raise ValueError(f"invalid task status: {status}")
    return cast(TaskStatus, status)


def _coordination_mode(value: object) -> CoordinationMode:
    mode = str(value)
    if mode not in _COORDINATION_MODES:
        raise ValueError(f"invalid coordination mode: {mode}")
    return cast(CoordinationMode, mode)


def _envelope_type(value: object) -> AgentEnvelopeType:
    envelope_type = str(value)
    if envelope_type not in _ENVELOPE_TYPES:
        raise ValueError(f"invalid envelope type: {envelope_type}")
    return cast(AgentEnvelopeType, envelope_type)


def _json_safe_dict(value: dict[str, object], *, error_message: str) -> JsonDict:
    try:
        return cast(
            JsonDict,
            json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False)),
        )
    except (TypeError, ValueError) as error:
        raise TypeError(error_message) from error


def _is_task_request_payload(value: object) -> bool:
    return _payload_has_keys(value, ("task_id", "instruction")) and not _payload_has_keys(
        value,
        ("status", "summary"),
    )


def _is_task_result_payload(value: object) -> bool:
    return _payload_has_keys(value, ("task_id", "status", "summary")) and not (
        isinstance(value, dict) and "instruction" in value
    )


def _payload_has_keys(value: object, keys: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    return all(key in value for key in keys)
