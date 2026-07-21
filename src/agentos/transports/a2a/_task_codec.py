from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._message_codec import (
    artifact_from_dict,
    artifact_to_dict,
    message_from_dict,
    message_to_dict,
)
from agentos.transports.a2a._protojson import (
    proto_enum,
    proto_enum_json,
    proto_fields,
    proto_timestamp,
    proto_timestamp_json,
    repeated,
    required_field,
)
from agentos.transports.a2a.message_types import (
    A2ATask,
    A2ATaskArtifactUpdateEvent,
    A2ATaskState,
    A2ATaskStatus,
    A2ATaskStatusUpdateEvent,
)


def task_status_to_dict(status: A2ATaskStatus) -> dict[str, object]:
    payload: dict[str, object] = {"state": proto_enum_json(status.state)}
    if status.message is not None:
        payload["message"] = message_to_dict(status.message)
    if status.timestamp is not None:
        payload["timestamp"] = proto_timestamp_json(status.timestamp)
    return payload


def task_status_from_dict(value: object) -> A2ATaskStatus:
    fields = proto_fields(
        value,
        {"state": "state", "message": "message", "timestamp": "timestamp"},
        field_name="task status",
    )
    message = fields.get("message")
    timestamp = fields.get("timestamp")
    return A2ATaskStatus(
        state=proto_enum(
            required_field(fields, "state", "task status state"),
            A2ATaskState,
            "task status state",
        ),
        message=None if message is None else message_from_dict(message),
        timestamp=None
        if timestamp is None
        else proto_timestamp(timestamp, "task timestamp"),
    )


def task_to_dict(task: A2ATask) -> dict[str, object]:
    payload: dict[str, object] = {"id": task.id}
    if task.context_id is not None:
        payload["contextId"] = task.context_id
    payload["status"] = task_status_to_dict(task.status)
    if task.artifacts is not None:
        payload["artifacts"] = [artifact_to_dict(item) for item in task.artifacts]
    if task.history is not None:
        payload["history"] = [message_to_dict(item) for item in task.history]
    if task.metadata is not None:
        payload["metadata"] = thaw_json_value(task.metadata)
    return payload


def task_from_dict(value: object) -> A2ATask:
    fields = proto_fields(
        value,
        {
            "id": "id",
            "contextId": "context_id",
            "status": "status",
            "artifacts": "artifacts",
            "history": "history",
            "metadata": "metadata",
        },
        field_name="task",
    )
    artifacts = fields.get("artifacts")
    history = fields.get("history")
    metadata = fields.get("metadata")
    return A2ATask(
        id=require_string(required_field(fields, "id", "task id"), "task id"),
        context_id=_optional_string(fields.get("contextId"), "contextId"),
        status=task_status_from_dict(required_field(fields, "status", "task status")),
        artifacts=None
        if artifacts is None
        else tuple(
            artifact_from_dict(item) for item in repeated(artifacts, "artifacts")
        ),
        history=None
        if history is None
        else tuple(message_from_dict(item) for item in repeated(history, "history")),
        metadata=_optional_object(metadata, "task metadata"),
    )


def status_update_to_dict(event: A2ATaskStatusUpdateEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "taskId": event.task_id,
        "contextId": event.context_id,
        "status": task_status_to_dict(event.status),
    }
    if event.metadata is not None:
        payload["metadata"] = thaw_json_value(event.metadata)
    return payload


def status_update_from_dict(value: object) -> A2ATaskStatusUpdateEvent:
    fields = proto_fields(
        value,
        {
            "taskId": "task_id",
            "contextId": "context_id",
            "status": "status",
            "metadata": "metadata",
        },
        field_name="status update",
    )
    return A2ATaskStatusUpdateEvent(
        task_id=require_string(required_field(fields, "taskId", "taskId"), "taskId"),
        context_id=require_string(
            required_field(fields, "contextId", "contextId"), "contextId"
        ),
        status=task_status_from_dict(required_field(fields, "status", "status")),
        metadata=_optional_object(fields.get("metadata"), "status metadata"),
    )


def artifact_update_to_dict(event: A2ATaskArtifactUpdateEvent) -> dict[str, object]:
    payload: dict[str, object] = {
        "taskId": event.task_id,
        "contextId": event.context_id,
        "artifact": artifact_to_dict(event.artifact),
    }
    if event.append:
        payload["append"] = True
    if event.last_chunk:
        payload["lastChunk"] = True
    if event.metadata is not None:
        payload["metadata"] = thaw_json_value(event.metadata)
    return payload


def artifact_update_from_dict(value: object) -> A2ATaskArtifactUpdateEvent:
    fields = proto_fields(
        value,
        {
            "taskId": "task_id",
            "contextId": "context_id",
            "artifact": "artifact",
            "append": "append",
            "lastChunk": "last_chunk",
            "metadata": "metadata",
        },
        field_name="artifact update",
    )
    return A2ATaskArtifactUpdateEvent(
        task_id=require_string(required_field(fields, "taskId", "taskId"), "taskId"),
        context_id=require_string(
            required_field(fields, "contextId", "contextId"), "contextId"
        ),
        artifact=artifact_from_dict(required_field(fields, "artifact", "artifact")),
        append=_optional_bool(fields.get("append"), "append"),
        last_chunk=_optional_bool(fields.get("lastChunk"), "lastChunk"),
        metadata=_optional_object(fields.get("metadata"), "artifact update metadata"),
    )


def _optional_bool(value: object, field_name: str) -> bool:
    if value is None:
        return False
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be bool")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return require_string(value, field_name)


def _optional_object(value: object, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    return value


__all__ = [
    "artifact_update_from_dict",
    "artifact_update_to_dict",
    "status_update_from_dict",
    "status_update_to_dict",
    "task_from_dict",
    "task_status_from_dict",
    "task_status_to_dict",
    "task_to_dict",
]
