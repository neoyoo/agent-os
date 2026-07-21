from __future__ import annotations

import hashlib

from agentos.transports.a2a._jcs import jcs_bytes
from agentos.transports.a2a._validation import require_identifier


_IDENTITY_METHODS = frozenset(
    {
        "CancelTask",
        "CreateTaskPushNotificationConfig",
        "DeleteTaskPushNotificationConfig",
    },
)


def a2a_session_id(*, tenant_id: str, message_id: str) -> str:
    require_identifier(tenant_id, "tenant_id")
    require_identifier(message_id, "message_id")
    digest = hashlib.sha256(f"{tenant_id}\n{message_id}".encode("utf-8"))
    return "a2a_" + digest.hexdigest()


def a2a_artifact_upload_id(*, message_id: str, part_index: int) -> str:
    require_identifier(message_id, "message_id")
    if type(part_index) is not int or part_index < 0:
        raise ValueError("part_index must be a non-negative integer")
    digest = hashlib.sha256(f"{message_id}\n{part_index}".encode("utf-8"))
    return "a2a_artifact_" + digest.hexdigest()


def a2a_inline_push_identity(*, message_id: str, task_id: str) -> str:
    require_identifier(message_id, "message_id")
    require_identifier(task_id, "task_id")
    canonical = jcs_bytes(
        {"messageId": message_id, "taskId": task_id, "version": 1},
    )
    return "a2a_inline_push_" + hashlib.sha256(canonical).hexdigest()


def a2a_operation_identity(
    *,
    method: str,
    request_id: str | int,
    task_id: str,
    config_id: str | None,
) -> str:
    if method not in _IDENTITY_METHODS:
        raise ValueError("method does not use a persistent A2A operation identity")
    if type(request_id) is str:
        if not request_id:
            raise ValueError("request_id must not be empty")
    elif type(request_id) is not int:
        raise TypeError("request_id must be str or integer")
    require_identifier(task_id, "task_id")
    if method == "CancelTask" and config_id is not None:
        raise ValueError("CancelTask config_id must be None")
    if method == "DeleteTaskPushNotificationConfig" and config_id is None:
        raise ValueError("Delete push config identity requires config_id")
    if config_id is not None:
        require_identifier(config_id, "config_id")
    canonical = jcs_bytes(
        {
            "method": method,
            "requestId": request_id,
            "resource": {"taskId": task_id, "configId": config_id},
            "version": 1,
        },
    )
    return "a2a_op_" + hashlib.sha256(canonical).hexdigest()


__all__ = [
    "a2a_artifact_upload_id",
    "a2a_inline_push_identity",
    "a2a_operation_identity",
    "a2a_session_id",
]
