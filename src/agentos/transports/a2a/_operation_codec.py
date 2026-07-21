from __future__ import annotations

from agentos.transports.a2a._json_codec import require_fields, require_string
from agentos.transports.a2a._operation_params_codec import (
    params_from_dict,
    params_to_dict,
)
from agentos.transports.a2a.operation_types import (
    A2AOperationRequest,
)


class A2AInvalidRequest(ValueError):
    pass


class A2AMethodNotFound(ValueError):
    pass


class A2AInvalidParams(ValueError):
    pass


_METHODS = frozenset(
    {
        "SendMessage",
        "SendStreamingMessage",
        "GetTask",
        "ListTasks",
        "CancelTask",
        "SubscribeToTask",
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
        "GetExtendedAgentCard",
    },
)


def operation_request_to_dict(request: A2AOperationRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "jsonrpc": request.jsonrpc,
        "id": request.request_id,
        "method": request.method,
    }
    params = params_to_dict(request.params)
    if params is not None:
        payload["params"] = params
    return payload


def operation_request_from_dict(value: object) -> A2AOperationRequest:
    try:
        payload = require_fields(
            value,
            required=frozenset({"jsonrpc", "id", "method"}),
            optional=frozenset({"params"}),
            field_name="operation request",
        )
        if payload["jsonrpc"] != "2.0":
            raise ValueError("jsonrpc must be 2.0")
        request_id = _request_id(payload["id"])
        method = require_string(payload["method"], "method")
    except (TypeError, ValueError) as error:
        raise A2AInvalidRequest from error
    if method not in _METHODS:
        raise A2AMethodNotFound
    try:
        params = params_from_dict(method, payload.get("params"))
    except (TypeError, ValueError) as error:
        raise A2AInvalidParams from error
    return A2AOperationRequest(request_id=request_id, params=params)


def _request_id(value: object) -> str | int:
    if type(value) is str and value:
        return value
    if type(value) is int:
        return value
    raise ValueError("request id is invalid")


from agentos.transports.a2a._response_codec import (  # noqa: E402
    operation_response_from_dict,
    operation_response_to_dict,
)
from agentos.transports.a2a._result_codec import (  # noqa: E402
    artifact_update_from_dict,
    artifact_update_to_dict,
    status_update_from_dict,
    status_update_to_dict,
    task_from_dict,
    task_to_dict,
)


__all__ = [
    "A2AInvalidParams",
    "A2AInvalidRequest",
    "A2AMethodNotFound",
    "artifact_update_from_dict",
    "artifact_update_to_dict",
    "operation_request_from_dict",
    "operation_request_to_dict",
    "operation_response_from_dict",
    "operation_response_to_dict",
    "status_update_from_dict",
    "status_update_to_dict",
    "task_from_dict",
    "task_to_dict",
]
