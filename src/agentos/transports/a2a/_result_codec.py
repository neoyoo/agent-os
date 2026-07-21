from __future__ import annotations

from agentos.transports.a2a._card_codec import agent_card_from_dict, agent_card_to_dict
from agentos.transports.a2a._json_codec import require_string
from agentos.transports.a2a._message_codec import message_from_dict, message_to_dict
from agentos.transports.a2a._protojson import (
    proto_fields,
    proto_int32,
    repeated,
    required_field,
)
from agentos.transports.a2a._push_codec import (
    push_config_from_dict,
    push_config_to_dict,
)
from agentos.transports.a2a._task_codec import (
    artifact_update_from_dict,
    artifact_update_to_dict,
    status_update_from_dict,
    status_update_to_dict,
    task_from_dict,
    task_to_dict,
)
from agentos.transports.a2a.operation_types import (
    A2AEmptyResult,
    A2AListTaskPushNotificationConfigsResult,
    A2AListTasksResult,
    A2ASendMessageResult,
    A2AStreamResponse,
)


def result_to_dict(result: object) -> dict[str, object]:
    from agentos.transports.a2a.card_types import A2AAgentCard
    from agentos.transports.a2a.message_types import A2ATask
    from agentos.transports.a2a.push_types import A2ATaskPushNotificationConfig

    if type(result) is A2ATask:
        return task_to_dict(result)
    if type(result) is A2ASendMessageResult:
        return send_result_to_dict(result)
    if type(result) is A2AStreamResponse:
        return stream_response_to_dict(result)
    if type(result) is A2AListTasksResult:
        return list_tasks_to_dict(result)
    if type(result) is A2ATaskPushNotificationConfig:
        return push_config_to_dict(result)
    if type(result) is A2AListTaskPushNotificationConfigsResult:
        return list_push_configs_to_dict(result)
    if type(result) is A2AAgentCard:
        return agent_card_to_dict(result)
    if type(result) is A2AEmptyResult:
        return {}
    raise TypeError("operation result is invalid")


def result_from_dict(value: object) -> object:
    if type(value) is not dict:
        raise ValueError("operation result must be an object")
    if not value:
        return A2AEmptyResult()
    keys = set(value)
    if keys & {"statusUpdate", "status_update", "artifactUpdate", "artifact_update"}:
        return stream_response_from_dict(value)
    if keys & {"task", "message"}:
        return send_result_from_dict(value)
    if "tasks" in value:
        return list_tasks_from_dict(value)
    if "configs" in value:
        return list_push_configs_from_dict(value)
    if "supportedInterfaces" in value or "supported_interfaces" in value:
        return agent_card_from_dict(value)
    if "url" in value and ("taskId" in value or "task_id" in value):
        return push_config_from_dict(value)
    return task_from_dict(value)


def send_result_to_dict(result: A2ASendMessageResult) -> dict[str, object]:
    if result.task is not None:
        return {"task": task_to_dict(result.task)}
    assert result.message is not None
    return {"message": message_to_dict(result.message)}


def send_result_from_dict(value: object) -> A2ASendMessageResult:
    fields = proto_fields(
        value, {"task": "task", "message": "message"}, field_name="SendMessage response"
    )
    _require_oneof(fields, ("task", "message"))
    return A2ASendMessageResult(
        task=None if "task" not in fields else task_from_dict(fields["task"]),
        message=None
        if "message" not in fields
        else message_from_dict(fields["message"]),
    )


def stream_response_to_dict(result: A2AStreamResponse) -> dict[str, object]:
    if result.task is not None:
        return {"task": task_to_dict(result.task)}
    if result.message is not None:
        return {"message": message_to_dict(result.message)}
    if result.status_update is not None:
        return {"statusUpdate": status_update_to_dict(result.status_update)}
    assert result.artifact_update is not None
    return {"artifactUpdate": artifact_update_to_dict(result.artifact_update)}


def stream_response_from_dict(value: object) -> A2AStreamResponse:
    fields = proto_fields(
        value,
        {
            "task": "task",
            "message": "message",
            "statusUpdate": "status_update",
            "artifactUpdate": "artifact_update",
        },
        field_name="StreamResponse",
    )
    _require_oneof(fields, ("task", "message", "statusUpdate", "artifactUpdate"))
    return A2AStreamResponse(
        task=None if "task" not in fields else task_from_dict(fields["task"]),
        message=None
        if "message" not in fields
        else message_from_dict(fields["message"]),
        status_update=None
        if "statusUpdate" not in fields
        else status_update_from_dict(fields["statusUpdate"]),
        artifact_update=None
        if "artifactUpdate" not in fields
        else artifact_update_from_dict(fields["artifactUpdate"]),
    )


def list_tasks_to_dict(result: A2AListTasksResult) -> dict[str, object]:
    return {
        "tasks": [task_to_dict(task) for task in result.tasks],
        "nextPageToken": result.next_page_token,
        "pageSize": result.page_size,
        "totalSize": result.total_size,
    }


def list_tasks_from_dict(value: object) -> A2AListTasksResult:
    fields = proto_fields(
        value,
        {
            "tasks": "tasks",
            "nextPageToken": "next_page_token",
            "pageSize": "page_size",
            "totalSize": "total_size",
        },
        field_name="ListTasks response",
    )
    return A2AListTasksResult(
        tasks=tuple(
            task_from_dict(item)
            for item in repeated(required_field(fields, "tasks", "tasks"), "tasks")
        ),
        next_page_token=require_string(
            required_field(fields, "nextPageToken", "nextPageToken"),
            "nextPageToken",
            empty=True,
        ),
        page_size=proto_int32(
            required_field(fields, "pageSize", "pageSize"),
            "pageSize",
            minimum=0,
            maximum=100,
        ),
        total_size=proto_int32(
            required_field(fields, "totalSize", "totalSize"), "totalSize", minimum=0
        ),
    )


def list_push_configs_to_dict(
    result: A2AListTaskPushNotificationConfigsResult,
) -> dict[str, object]:
    return {
        "configs": [push_config_to_dict(item) for item in result.configs],
        "nextPageToken": result.next_page_token,
    }


def list_push_configs_from_dict(
    value: object,
) -> A2AListTaskPushNotificationConfigsResult:
    fields = proto_fields(
        value,
        {"configs": "configs", "nextPageToken": "next_page_token"},
        field_name="List push configs response",
    )
    return A2AListTaskPushNotificationConfigsResult(
        configs=tuple(
            push_config_from_dict(item)
            for item in repeated(fields.get("configs"), "configs")
        ),
        next_page_token=""
        if fields.get("nextPageToken") is None
        else require_string(fields["nextPageToken"], "nextPageToken", empty=True),
    )


def _require_oneof(fields: dict[str, object], names: tuple[str, ...]) -> None:
    if sum(name in fields and fields[name] is not None for name in names) != 1:
        raise ValueError("result wrapper must select exactly one payload")


__all__ = [
    "artifact_update_from_dict",
    "artifact_update_to_dict",
    "result_from_dict",
    "result_to_dict",
    "status_update_from_dict",
    "status_update_to_dict",
    "stream_response_from_dict",
    "stream_response_to_dict",
    "task_from_dict",
    "task_to_dict",
]
