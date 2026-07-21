from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.transports.a2a.card_types import A2AAgentCard
from agentos.transports.a2a._operation_params import (
    A2ACancelTaskParams,
    A2ACreateTaskPushNotificationConfigParams,
    A2ADeleteTaskPushNotificationConfigParams,
    A2AGetExtendedAgentCardParams,
    A2AGetTaskParams,
    A2AGetTaskPushNotificationConfigParams,
    A2AListTaskPushNotificationConfigsParams,
    A2AListTasksParams,
    A2AOperationParams,
    A2ASendMessageConfiguration,
    A2ASendMessageParams,
    A2ASendStreamingMessageParams,
    A2ASubscribeToTaskParams,
)
from agentos.transports.a2a.message_types import (
    A2AMessage,
    A2ATask,
    A2ATaskArtifactUpdateEvent,
    A2ATaskStatusUpdateEvent,
)
from agentos.transports.a2a.push_types import A2ATaskPushNotificationConfig


A2ARequestId = str | int

_METHOD_BY_PARAMS = {
    A2ASendMessageParams: "SendMessage",
    A2ASendStreamingMessageParams: "SendStreamingMessage",
    A2AGetTaskParams: "GetTask",
    A2AListTasksParams: "ListTasks",
    A2ACancelTaskParams: "CancelTask",
    A2ASubscribeToTaskParams: "SubscribeToTask",
    A2ACreateTaskPushNotificationConfigParams: "CreateTaskPushNotificationConfig",
    A2AGetTaskPushNotificationConfigParams: "GetTaskPushNotificationConfig",
    A2AListTaskPushNotificationConfigsParams: "ListTaskPushNotificationConfigs",
    A2ADeleteTaskPushNotificationConfigParams: "DeleteTaskPushNotificationConfig",
    A2AGetExtendedAgentCardParams: "GetExtendedAgentCard",
}


@dataclass(frozen=True, slots=True)
class A2ASendMessageResult:
    task: A2ATask | None = None
    message: A2AMessage | None = None

    def __post_init__(self) -> None:
        _oneof((self.task, A2ATask), (self.message, A2AMessage))


@dataclass(frozen=True, slots=True)
class A2AStreamResponse:
    task: A2ATask | None = None
    message: A2AMessage | None = None
    status_update: A2ATaskStatusUpdateEvent | None = None
    artifact_update: A2ATaskArtifactUpdateEvent | None = None

    def __post_init__(self) -> None:
        _oneof(
            (self.task, A2ATask),
            (self.message, A2AMessage),
            (self.status_update, A2ATaskStatusUpdateEvent),
            (self.artifact_update, A2ATaskArtifactUpdateEvent),
        )


@dataclass(frozen=True, slots=True)
class A2AListTasksResult:
    tasks: tuple[A2ATask, ...]
    next_page_token: str
    page_size: int
    total_size: int

    def __post_init__(self) -> None:
        tasks = tuple(self.tasks)
        if any(type(task) is not A2ATask for task in tasks):
            raise TypeError("tasks contains an invalid value")
        if type(self.next_page_token) is not str:
            raise TypeError("next_page_token must be str")
        if type(self.page_size) is not int or not 0 <= self.page_size <= 100:
            raise ValueError("page_size is invalid")
        if type(self.total_size) is not int or self.total_size < 0:
            raise ValueError("total_size is invalid")
        object.__setattr__(self, "tasks", tasks)


@dataclass(frozen=True, slots=True)
class A2AListTaskPushNotificationConfigsResult:
    configs: tuple[A2ATaskPushNotificationConfig, ...]
    next_page_token: str

    def __post_init__(self) -> None:
        configs = tuple(self.configs)
        if any(type(config) is not A2ATaskPushNotificationConfig for config in configs):
            raise TypeError("configs contains an invalid value")
        if type(self.next_page_token) is not str:
            raise TypeError("next_page_token must be str")
        object.__setattr__(self, "configs", configs)


@dataclass(frozen=True, slots=True)
class A2AEmptyResult:
    pass


_ERROR_MESSAGES = {
    -32700: "Invalid JSON payload",
    -32600: "Request payload validation error",
    -32601: "Method not found",
    -32602: "Invalid parameters",
    -32603: "Internal error",
    -32001: "Task not found",
    -32002: "Task cannot be canceled",
    -32003: "Push notifications are not supported",
    -32004: "Operation is not supported",
    -32005: "Content type is not supported",
    -32006: "Invalid agent response",
    -32007: "Extended Agent Card is not configured",
    -32008: "Extension support is required",
    -32009: "A2A protocol version is not supported",
}


@dataclass(frozen=True, slots=True)
class A2AOperationError:
    code: int
    data: tuple[FrozenJsonObject | Mapping[str, object], ...] | None = None

    def __post_init__(self) -> None:
        if type(self.code) is not int or self.code not in _ERROR_MESSAGES:
            raise ValueError("unsupported A2A error code")
        if self.data is not None:
            details = tuple(freeze_json_mapping(item) for item in self.data)
            if any(type(item.get("@type")) is not str for item in details):
                raise ValueError("error details must contain @type")
            object.__setattr__(self, "data", details)

    @property
    def message(self) -> str:
        return _ERROR_MESSAGES[self.code]


@dataclass(frozen=True, slots=True)
class A2AOperationRequest:
    request_id: A2ARequestId
    params: A2AOperationParams
    jsonrpc: str = "2.0"

    def __post_init__(self) -> None:
        _require_request_id(self.request_id)
        if self.jsonrpc != "2.0":
            raise ValueError("jsonrpc must be 2.0")
        if type(self.params) not in _METHOD_BY_PARAMS:
            raise TypeError("operation params are invalid")

    @property
    def method(self) -> str:
        return _METHOD_BY_PARAMS[type(self.params)]


@dataclass(frozen=True, slots=True)
class A2AOperationResponse:
    request_id: A2ARequestId | None
    result: object | None = None
    error: A2AOperationError | None = None
    jsonrpc: str = "2.0"

    def __post_init__(self) -> None:
        if self.request_id is not None:
            _require_request_id(self.request_id)
        if self.jsonrpc != "2.0":
            raise ValueError("jsonrpc must be 2.0")
        if (self.result is None) == (self.error is None):
            raise ValueError("response must contain exactly one result or error")
        if self.result is not None and self.request_id is None:
            raise ValueError("success response requires a request id")
        if self.result is not None and type(self.result) not in {
            A2AAgentCard,
            A2AEmptyResult,
            A2AListTaskPushNotificationConfigsResult,
            A2AListTasksResult,
            A2ASendMessageResult,
            A2AStreamResponse,
            A2ATask,
            A2ATaskPushNotificationConfig,
        }:
            raise TypeError("response result is not an official A2A result type")
        if self.error is not None and type(self.error) is not A2AOperationError:
            raise TypeError("response error is invalid")


def _oneof(*values: tuple[object | None, type[object]]) -> None:
    present = tuple(
        (value, expected) for value, expected in values if value is not None
    )
    if len(present) != 1 or type(present[0][0]) is not present[0][1]:
        raise ValueError("oneof must contain exactly one correctly typed value")


def _require_request_id(value: object) -> None:
    if type(value) is str and value:
        return
    if type(value) is int:
        return
    raise TypeError("request id must be a non-empty str or integer")


__all__ = [
    "A2ACancelTaskParams",
    "A2ACreateTaskPushNotificationConfigParams",
    "A2ADeleteTaskPushNotificationConfigParams",
    "A2AEmptyResult",
    "A2AGetExtendedAgentCardParams",
    "A2AGetTaskParams",
    "A2AGetTaskPushNotificationConfigParams",
    "A2AListTaskPushNotificationConfigsParams",
    "A2AListTaskPushNotificationConfigsResult",
    "A2AListTasksParams",
    "A2AListTasksResult",
    "A2AOperationError",
    "A2AOperationParams",
    "A2AOperationRequest",
    "A2AOperationResponse",
    "A2ARequestId",
    "A2ASendMessageConfiguration",
    "A2ASendMessageParams",
    "A2ASendMessageResult",
    "A2ASendStreamingMessageParams",
    "A2AStreamResponse",
    "A2ASubscribeToTaskParams",
]
