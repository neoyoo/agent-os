from __future__ import annotations

from agentos._json_values import thaw_json_value
from agentos.transports.a2a._operation_params_helpers import (
    _optional,
    _optional_int,
    _optional_object,
    _optional_string,
    _required_string,
    _tenant,
)
from agentos.transports.a2a._protojson import (
    proto_enum,
    proto_enum_json,
    proto_fields,
    proto_timestamp,
    proto_timestamp_json,
)
from agentos.transports.a2a._send_params_codec import _send_from_dict, _send_to_dict
from agentos.transports.a2a.message_types import A2ATaskState
from agentos.transports.a2a.operation_types import (
    A2ACancelTaskParams,
    A2ACreateTaskPushNotificationConfigParams,
    A2ADeleteTaskPushNotificationConfigParams,
    A2AGetExtendedAgentCardParams,
    A2AGetTaskParams,
    A2AGetTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsParams,
    A2AListTasksParams,
    A2AOperationParams,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
    A2ASubscribeToTaskParams,
)
from agentos.transports.a2a._push_codec import (
    push_config_from_dict,
    push_config_to_dict,
)


def params_to_dict(params: A2AOperationParams) -> dict[str, object] | None:
    if type(params) in {A2ASendMessageParams, A2ASendStreamingMessageParams}:
        payload = _send_to_dict(params)
    elif type(params) is A2AGetTaskParams:
        payload = _tenant(params.tenant)
        payload["id"] = params.id
        _optional(payload, "historyLength", params.history_length)
    elif type(params) is A2AListTasksParams:
        payload = _list_tasks_to_dict(params)
    elif type(params) is A2ACancelTaskParams:
        payload = _tenant(params.tenant)
        payload["id"] = params.id
        if params.metadata is not None:
            payload["metadata"] = thaw_json_value(params.metadata)
    elif type(params) is A2ASubscribeToTaskParams:
        payload = _tenant(params.tenant)
        payload["id"] = params.id
    elif type(params) is A2ACreateTaskPushNotificationConfigParams:
        payload = push_config_to_dict(params.config)
    elif type(params) in {
        A2AGetTaskPushNotificationConfigParams,
        A2ADeleteTaskPushNotificationConfigParams,
    }:
        payload = _tenant(params.tenant)
        payload.update({"taskId": params.task_id, "id": params.id})
    elif type(params) is A2AListTaskPushNotificationConfigsParams:
        payload = _tenant(params.tenant)
        payload["taskId"] = params.task_id
        _optional(payload, "pageSize", params.page_size)
        _optional(payload, "pageToken", params.page_token)
    elif type(params) is A2AGetExtendedAgentCardParams:
        return None if params.tenant is None else {"tenant": params.tenant}
    else:
        raise TypeError("operation params are invalid")
    return payload


def params_from_dict(method: str, value: object) -> A2AOperationParams:
    if method in {"SendMessage", "SendStreamingMessage"}:
        return _send_from_dict(method, value)
    if method == "GetTask":
        fields = proto_fields(
            value,
            {"tenant": "tenant", "id": "id", "historyLength": "history_length"},
            field_name="GetTask params",
        )
        return A2AGetTaskParams(
            tenant=_optional_string(fields.get("tenant"), "tenant"),
            id=_required_string(fields, "id"),
            history_length=_optional_int(
                fields.get("historyLength"), "historyLength", minimum=0
            ),
        )
    if method == "ListTasks":
        return _list_tasks_from_dict(value)
    if method == "CancelTask":
        fields = proto_fields(
            value,
            {"tenant": "tenant", "id": "id", "metadata": "metadata"},
            field_name="CancelTask params",
        )
        return A2ACancelTaskParams(
            tenant=_optional_string(fields.get("tenant"), "tenant"),
            id=_required_string(fields, "id"),
            metadata=_optional_object(fields.get("metadata"), "metadata"),
        )
    if method == "SubscribeToTask":
        fields = proto_fields(
            value, {"tenant": "tenant", "id": "id"}, field_name="SubscribeToTask params"
        )
        return A2ASubscribeToTaskParams(
            tenant=_optional_string(fields.get("tenant"), "tenant"),
            id=_required_string(fields, "id"),
        )
    if method == "CreateTaskPushNotificationConfig":
        return A2ACreateTaskPushNotificationConfigParams(push_config_from_dict(value))
    if method in {"GetTaskPushNotificationConfig", "DeleteTaskPushNotificationConfig"}:
        fields = _push_id_fields(value, method)
        cls = (
            A2AGetTaskPushNotificationConfigParams
            if method.startswith("Get")
            else A2ADeleteTaskPushNotificationConfigParams
        )
        return cls(
            tenant=_optional_string(fields.get("tenant"), "tenant"),
            task_id=_required_string(fields, "taskId"),
            id=_required_string(fields, "id"),
        )
    if method == "ListTaskPushNotificationConfigs":
        fields = proto_fields(
            value,
            {
                "tenant": "tenant",
                "taskId": "task_id",
                "pageSize": "page_size",
                "pageToken": "page_token",
            },
            field_name="ListTaskPushNotificationConfigs params",
        )
        return A2AListTaskPushNotificationConfigsParams(
            tenant=_optional_string(fields.get("tenant"), "tenant"),
            task_id=_required_string(fields, "taskId"),
            page_size=_optional_int(
                fields.get("pageSize"), "pageSize", minimum=1, maximum=100
            ),
            page_token=_optional_string(fields.get("pageToken"), "pageToken"),
        )
    if method == "GetExtendedAgentCard":
        if value is None:
            return A2AGetExtendedAgentCardParams()
        fields = proto_fields(
            value, {"tenant": "tenant"}, field_name="GetExtendedAgentCard params"
        )
        return A2AGetExtendedAgentCardParams(
            _optional_string(fields.get("tenant"), "tenant")
        )
    raise ValueError("unknown operation method")


def _list_tasks_to_dict(params: A2AListTasksParams) -> dict[str, object]:
    payload = _tenant(params.tenant)
    _optional(payload, "contextId", params.context_id)
    if params.status is not None:
        payload["status"] = proto_enum_json(params.status)
    _optional(payload, "pageSize", params.page_size)
    _optional(payload, "pageToken", params.page_token)
    _optional(payload, "historyLength", params.history_length)
    if params.status_timestamp_after is not None:
        payload["statusTimestampAfter"] = proto_timestamp_json(
            params.status_timestamp_after
        )
    _optional(payload, "includeArtifacts", params.include_artifacts)
    return payload


def _list_tasks_from_dict(value: object) -> A2AListTasksParams:
    fields = proto_fields(
        value,
        {
            "tenant": "tenant",
            "contextId": "context_id",
            "status": "status",
            "pageSize": "page_size",
            "pageToken": "page_token",
            "historyLength": "history_length",
            "statusTimestampAfter": "status_timestamp_after",
            "includeArtifacts": "include_artifacts",
        },
        field_name="ListTasks params",
    )
    status = fields.get("status")
    timestamp = fields.get("statusTimestampAfter")
    include = fields.get("includeArtifacts")
    if include is not None and type(include) is not bool:
        raise ValueError("includeArtifacts must be bool")
    return A2AListTasksParams(
        tenant=_optional_string(fields.get("tenant"), "tenant"),
        context_id=_optional_string(fields.get("contextId"), "contextId"),
        status=None if status is None else proto_enum(status, A2ATaskState, "status"),
        page_size=_optional_int(
            fields.get("pageSize"), "pageSize", minimum=1, maximum=100
        ),
        page_token=_optional_string(fields.get("pageToken"), "pageToken"),
        history_length=_optional_int(
            fields.get("historyLength"), "historyLength", minimum=0
        ),
        status_timestamp_after=None
        if timestamp is None
        else proto_timestamp(timestamp, "statusTimestampAfter"),
        include_artifacts=include,
    )


def _push_id_fields(value: object, method: str) -> dict[str, object]:
    return proto_fields(
        value,
        {"tenant": "tenant", "taskId": "task_id", "id": "id"},
        field_name=f"{method} params",
    )


__all__ = ["params_from_dict", "params_to_dict"]
