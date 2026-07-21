from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from agentos._json_values import (
    FrozenJsonObject,
    FrozenJsonValue,
    freeze_json_value,
)
from agentos.transports.a2a._protojson import A2A_UNSET
from agentos.transports.a2a._validation import (
    freeze_metadata,
    freeze_string_tuple,
    require_identifier,
    require_text,
)


class A2ARole(IntEnum):
    ROLE_UNSPECIFIED = 0
    ROLE_USER = 1
    ROLE_AGENT = 2


class A2ATaskState(IntEnum):
    TASK_STATE_UNSPECIFIED = 0
    TASK_STATE_SUBMITTED = 1
    TASK_STATE_WORKING = 2
    TASK_STATE_COMPLETED = 3
    TASK_STATE_FAILED = 4
    TASK_STATE_CANCELED = 5
    TASK_STATE_INPUT_REQUIRED = 6
    TASK_STATE_REJECTED = 7
    TASK_STATE_AUTH_REQUIRED = 8


@dataclass(frozen=True, slots=True)
class A2APart:
    text: str | object = A2A_UNSET
    raw: bytes | object = A2A_UNSET
    url: str | object = A2A_UNSET
    data: FrozenJsonValue | object = A2A_UNSET
    metadata: FrozenJsonObject | Mapping[str, object] | None = None
    filename: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        contents = (self.text, self.raw, self.url, self.data)
        if sum(value is not A2A_UNSET for value in contents) != 1:
            raise ValueError("Part must select exactly one content field")
        if self.text is not A2A_UNSET:
            require_text(self.text, "part text")
        if self.raw is not A2A_UNSET and type(self.raw) is not bytes:
            raise TypeError("part raw must be bytes")
        if self.url is not A2A_UNSET:
            require_text(self.url, "part url", allow_empty=False)
        if self.data is not A2A_UNSET:
            object.__setattr__(self, "data", freeze_json_value(self.data))
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        for value, name in (
            (self.filename, "part filename"),
            (self.media_type, "part media_type"),
        ):
            if value is not None:
                require_text(value, name, allow_empty=False)


@dataclass(frozen=True, slots=True)
class A2AMessage:
    message_id: str
    role: A2ARole
    parts: tuple[A2APart, ...]
    context_id: str | None = None
    task_id: str | None = None
    metadata: FrozenJsonObject | Mapping[str, object] | None = None
    extensions: tuple[str, ...] = ()
    reference_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.message_id, "message_id")
        if self.role not in {A2ARole.ROLE_USER, A2ARole.ROLE_AGENT}:
            raise ValueError("message role must be ROLE_USER or ROLE_AGENT")
        object.__setattr__(
            self, "parts", _typed_tuple(self.parts, A2APart, "parts", required=True)
        )
        for value, name in ((self.context_id, "context_id"), (self.task_id, "task_id")):
            if value is not None:
                require_identifier(value, name)
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        object.__setattr__(
            self, "extensions", freeze_string_tuple(self.extensions, "extensions")
        )
        object.__setattr__(
            self,
            "reference_task_ids",
            freeze_string_tuple(self.reference_task_ids, "reference_task_ids"),
        )


@dataclass(frozen=True, slots=True)
class A2AArtifact:
    artifact_id: str
    parts: tuple[A2APart, ...]
    name: str | None = None
    description: str | None = None
    metadata: FrozenJsonObject | Mapping[str, object] | None = None
    extensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_identifier(self.artifact_id, "artifact_id")
        object.__setattr__(
            self, "parts", _typed_tuple(self.parts, A2APart, "parts", required=True)
        )
        for value, name in (
            (self.name, "artifact name"),
            (self.description, "artifact description"),
        ):
            if value is not None:
                require_text(value, name, allow_empty=False)
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))
        object.__setattr__(
            self, "extensions", freeze_string_tuple(self.extensions, "extensions")
        )


@dataclass(frozen=True, slots=True)
class A2ATaskStatus:
    state: A2ATaskState | int
    message: A2AMessage | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not int and not isinstance(self.state, A2ATaskState):
            raise TypeError("task state must be A2ATaskState or integer")
        if self.message is not None and type(self.message) is not A2AMessage:
            raise TypeError("task status message must be A2AMessage or None")
        if self.timestamp is not None and (
            type(self.timestamp) is not datetime or self.timestamp.tzinfo is None
        ):
            raise ValueError("task status timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class A2ATask:
    id: str
    status: A2ATaskStatus
    context_id: str | None = None
    artifacts: tuple[A2AArtifact, ...] | None = None
    history: tuple[A2AMessage, ...] | None = None
    metadata: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        require_identifier(self.id, "task id")
        if type(self.status) is not A2ATaskStatus:
            raise TypeError("task status must be A2ATaskStatus")
        if self.context_id is not None:
            require_identifier(self.context_id, "context_id")
        if self.artifacts is not None:
            object.__setattr__(
                self,
                "artifacts",
                _typed_tuple(self.artifacts, A2AArtifact, "artifacts"),
            )
        if self.history is not None:
            object.__setattr__(
                self, "history", _typed_tuple(self.history, A2AMessage, "history")
            )
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class A2ATaskStatusUpdateEvent:
    task_id: str
    context_id: str
    status: A2ATaskStatus
    metadata: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        require_identifier(self.task_id, "task_id")
        require_identifier(self.context_id, "context_id")
        if type(self.status) is not A2ATaskStatus:
            raise TypeError("status must be A2ATaskStatus")
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class A2ATaskArtifactUpdateEvent:
    task_id: str
    context_id: str
    artifact: A2AArtifact
    append: bool = False
    last_chunk: bool = False
    metadata: FrozenJsonObject | Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        require_identifier(self.task_id, "task_id")
        require_identifier(self.context_id, "context_id")
        if type(self.artifact) is not A2AArtifact:
            raise TypeError("artifact must be A2AArtifact")
        if type(self.append) is not bool or type(self.last_chunk) is not bool:
            raise TypeError("artifact update flags must be bool")
        if self.metadata is not None:
            object.__setattr__(self, "metadata", freeze_metadata(self.metadata))


def _typed_tuple(
    values: object,
    expected: type[object],
    field_name: str,
    *,
    required: bool = False,
) -> tuple[object, ...]:
    if type(values) is not tuple:
        values = tuple(values)  # type: ignore[arg-type]
    if required and not values:
        raise ValueError(f"{field_name} must not be empty")
    if any(type(value) is not expected for value in values):
        raise TypeError(f"{field_name} contains an invalid value")
    return values


__all__ = [
    "A2AArtifact",
    "A2AMessage",
    "A2APart",
    "A2ARole",
    "A2ATask",
    "A2ATaskArtifactUpdateEvent",
    "A2ATaskState",
    "A2ATaskStatus",
    "A2ATaskStatusUpdateEvent",
]
