from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from agentos._json_values import FrozenJsonObject
from agentos.transports.a2a._validation import (
    freeze_metadata,
    freeze_string_tuple,
    require_identifier,
)
from agentos.transports.a2a.message_types import A2AMessage, A2ATaskState
from agentos.transports.a2a.push_types import A2ATaskPushNotificationConfig


@dataclass(frozen=True, slots=True)
class A2ASendMessageConfiguration:
    accepted_output_modes: tuple[str, ...] = ()
    task_push_notification_config: A2ATaskPushNotificationConfig | None = None
    history_length: int | None = None
    return_immediately: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "accepted_output_modes",
            freeze_string_tuple(self.accepted_output_modes, "accepted_output_modes"),
        )
        if (
            self.task_push_notification_config is not None
            and type(self.task_push_notification_config)
            is not A2ATaskPushNotificationConfig
        ):
            raise TypeError("task push config is invalid")
        _optional_nonnegative(self.history_length, "history_length")
        if type(self.return_immediately) is not bool:
            raise TypeError("return_immediately must be bool")


@dataclass(frozen=True, slots=True)
class A2ASendMessageParams:
    message: A2AMessage
    tenant: str | None = None
    configuration: A2ASendMessageConfiguration | None = None
    metadata: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _validate_send(self)


@dataclass(frozen=True, slots=True)
class A2ASendStreamingMessageParams:
    message: A2AMessage
    tenant: str | None = None
    configuration: A2ASendMessageConfiguration | None = None
    metadata: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _validate_send(self)


@dataclass(frozen=True, slots=True)
class A2AGetTaskParams:
    id: str
    tenant: str | None = None
    history_length: int | None = None

    def __post_init__(self) -> None:
        _identifier_pair(self.id, self.tenant)
        _optional_nonnegative(self.history_length, "history_length")


@dataclass(frozen=True, slots=True)
class A2AListTasksParams:
    tenant: str | None = None
    context_id: str | None = None
    status: A2ATaskState | int | None = None
    page_size: int | None = None
    page_token: str | None = None
    history_length: int | None = None
    status_timestamp_after: datetime | None = None
    include_artifacts: bool | None = None

    def __post_init__(self) -> None:
        for value, name in ((self.tenant, "tenant"), (self.context_id, "context_id")):
            if value is not None:
                require_identifier(value, name)
        if (
            self.status is not None
            and type(self.status) is not int
            and not isinstance(self.status, A2ATaskState)
        ):
            raise TypeError("status must be A2ATaskState, integer, or None")
        _optional_page_size(self.page_size)
        if self.page_token is not None:
            require_identifier(self.page_token, "page_token")
        _optional_nonnegative(self.history_length, "history_length")
        if self.status_timestamp_after is not None and (
            type(self.status_timestamp_after) is not datetime
            or self.status_timestamp_after.tzinfo is None
        ):
            raise ValueError("status_timestamp_after must be timezone-aware")
        if (
            self.include_artifacts is not None
            and type(self.include_artifacts) is not bool
        ):
            raise TypeError("include_artifacts must be bool or None")


@dataclass(frozen=True, slots=True)
class A2ACancelTaskParams:
    id: str
    tenant: str | None = None
    metadata: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _identifier_pair(self.id, self.tenant)
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class A2ASubscribeToTaskParams:
    id: str
    tenant: str | None = None

    def __post_init__(self) -> None:
        _identifier_pair(self.id, self.tenant)


@dataclass(frozen=True, slots=True)
class A2ACreateTaskPushNotificationConfigParams:
    config: A2ATaskPushNotificationConfig

    def __post_init__(self) -> None:
        if type(self.config) is not A2ATaskPushNotificationConfig:
            raise TypeError("config must be A2ATaskPushNotificationConfig")


@dataclass(frozen=True, slots=True)
class A2AGetTaskPushNotificationConfigParams:
    task_id: str
    id: str
    tenant: str | None = None

    def __post_init__(self) -> None:
        _push_identifiers(self)


@dataclass(frozen=True, slots=True)
class A2AListTaskPushNotificationConfigsParams:
    task_id: str
    tenant: str | None = None
    page_size: int | None = None
    page_token: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.task_id, "task_id")
        if self.tenant is not None:
            require_identifier(self.tenant, "tenant")
        _optional_page_size(self.page_size)
        if self.page_token is not None:
            require_identifier(self.page_token, "page_token")


@dataclass(frozen=True, slots=True)
class A2ADeleteTaskPushNotificationConfigParams:
    task_id: str
    id: str
    tenant: str | None = None

    def __post_init__(self) -> None:
        _push_identifiers(self)


@dataclass(frozen=True, slots=True)
class A2AGetExtendedAgentCardParams:
    tenant: str | None = None

    def __post_init__(self) -> None:
        if self.tenant is not None:
            require_identifier(self.tenant, "tenant")


def _validate_send(value: object) -> None:
    message = value.message  # type: ignore[attr-defined]
    if type(message) is not A2AMessage or message.role.name != "ROLE_USER":
        raise ValueError("Send message must have role ROLE_USER")
    tenant = value.tenant  # type: ignore[attr-defined]
    if tenant is not None:
        require_identifier(tenant, "tenant")
    configuration = value.configuration  # type: ignore[attr-defined]
    if (
        configuration is not None
        and type(configuration) is not A2ASendMessageConfiguration
    ):
        raise TypeError("configuration is invalid")
    metadata = value.metadata  # type: ignore[attr-defined]
    if metadata is not None:
        object.__setattr__(value, "metadata", freeze_metadata(metadata))


def _identifier_pair(identifier: str, tenant: str | None) -> None:
    require_identifier(identifier, "id")
    if tenant is not None:
        require_identifier(tenant, "tenant")


def _push_identifiers(value: object) -> None:
    require_identifier(value.task_id, "task_id")  # type: ignore[attr-defined]
    require_identifier(value.id, "push config id")  # type: ignore[attr-defined]
    if value.tenant is not None:  # type: ignore[attr-defined]
        require_identifier(value.tenant, "tenant")  # type: ignore[attr-defined]


def _optional_nonnegative(value: int | None, field_name: str) -> None:
    if value is not None and (type(value) is not int or not 0 <= value < 2**31):
        raise ValueError(f"{field_name} must be a non-negative int32 or None")


def _optional_page_size(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 1 <= value <= 100):
        raise ValueError("page_size must be between 1 and 100")


A2AOperationParams = (
    A2ASendMessageParams
    | A2ASendStreamingMessageParams
    | A2AGetTaskParams
    | A2AListTasksParams
    | A2ACancelTaskParams
    | A2ASubscribeToTaskParams
    | A2ACreateTaskPushNotificationConfigParams
    | A2AGetTaskPushNotificationConfigParams
    | A2AListTaskPushNotificationConfigsParams
    | A2ADeleteTaskPushNotificationConfigParams
    | A2AGetExtendedAgentCardParams
)
