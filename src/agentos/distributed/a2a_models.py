from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import unicodedata

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.models import RequestScope, RunReadModel
from agentos.runtime.payloads import ProtectedPayloadRef


_MAX_PUSH_URL_BYTES = 2048
_MAX_PUSH_SECRET_BYTES = 8192


@dataclass(frozen=True, slots=True, repr=False)
class A2APushAuthenticationInput:
    """A2A push wire authentication before secret protection."""

    scheme: str
    credentials: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.scheme, "a2a push authentication scheme")
        _require_optional_secret(self.credentials, "credentials")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(scheme={self.scheme!r}, "
            "credentials=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class A2APushConfigInput:
    """Untrusted push config whose token and credentials are never repr-visible."""

    config_id: str | None
    url: str
    token: str | None = None
    authentication: A2APushAuthenticationInput | None = None

    def __post_init__(self) -> None:
        if self.config_id is not None:
            require_identifier(self.config_id, "a2a push config_id")
        _require_url(self.url)
        _require_optional_secret(self.token, "token")
        if self.authentication is not None and (
            type(self.authentication) is not A2APushAuthenticationInput
        ):
            raise TypeError(
                "authentication must be A2APushAuthenticationInput or None",
            )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(config_id={self.config_id!r}, "
            f"url={self.url!r}, token=<redacted>, authentication=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class A2APushConfigRecord:
    """Port-facing protected push config truth."""

    tenant_id: str
    task_id: str
    config_id: str
    url: str
    authentication_scheme: str | None
    secret_ref: ProtectedPayloadRef | None

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.task_id, "task_id")
        require_identifier(self.config_id, "config_id")
        _require_url(self.url)
        if self.authentication_scheme is not None:
            require_identifier(
                self.authentication_scheme,
                "a2a push authentication scheme",
            )
        if self.secret_ref is not None and type(self.secret_ref) is not ProtectedPayloadRef:
            raise TypeError("secret_ref must be ProtectedPayloadRef or None")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(tenant_id={self.tenant_id!r}, "
            f"task_id={self.task_id!r}, config_id={self.config_id!r}, "
            f"url={self.url!r}, "
            f"authentication_scheme={self.authentication_scheme!r}, "
            "secret_ref=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class A2APushConfigView:
    """Secret-free push config returned to Channel and wire mapping."""

    task_id: str
    config_id: str
    url: str
    authentication_scheme: str | None

    def __post_init__(self) -> None:
        require_identifier(self.task_id, "task_id")
        require_identifier(self.config_id, "config_id")
        _require_url(self.url)
        if self.authentication_scheme is not None:
            require_identifier(
                self.authentication_scheme,
                "a2a push authentication scheme",
            )


@dataclass(frozen=True, slots=True, repr=False)
class A2APushConfigRecordPage:
    """Port-facing keyset page whose records may contain protected secrets."""

    records: tuple[A2APushConfigRecord, ...]
    next_page_token: str

    def __post_init__(self) -> None:
        if type(self.records) is not tuple or any(
            type(record) is not A2APushConfigRecord for record in self.records
        ):
            raise TypeError("records must be a tuple of A2APushConfigRecord")
        _require_page_token(self.next_page_token)


@dataclass(frozen=True, slots=True)
class A2APushConfigPage:
    """Secret-free push config page returned to Channel."""

    configs: tuple[A2APushConfigView, ...]
    next_page_token: str

    def __post_init__(self) -> None:
        if type(self.configs) is not tuple or any(
            type(config) is not A2APushConfigView for config in self.configs
        ):
            raise TypeError("configs must be a tuple of A2APushConfigView")
        _require_page_token(self.next_page_token)


@dataclass(frozen=True, slots=True, repr=False)
class A2APushDeliveryTarget:
    """PostgreSQL 解析出的 immutable outbound Push delivery 真值。"""

    scope: RequestScope
    outbox_id: str
    delivery_id: str
    task_id: str
    context_id: str
    config_id: str
    url: str
    authentication_scheme: str | None
    secret_ref: ProtectedPayloadRef | None
    event_id: str
    protocol_version: str
    status_sequence: int
    task_state: A2ATaskState
    delivered_at: datetime | None
    suppressed_at: datetime | None
    abandoned_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        for value, name in (
            (self.outbox_id, "outbox_id"),
            (self.delivery_id, "delivery_id"),
            (self.task_id, "task_id"),
            (self.context_id, "context_id"),
            (self.config_id, "config_id"),
            (self.event_id, "event_id"),
            (self.protocol_version, "protocol_version"),
        ):
            require_identifier(value, name)
        _require_url(self.url)
        if self.authentication_scheme is not None:
            require_identifier(self.authentication_scheme, "authentication_scheme")
        if self.secret_ref is not None and type(self.secret_ref) is not ProtectedPayloadRef:
            raise TypeError("secret_ref must be ProtectedPayloadRef or None")
        if type(self.task_state) is not A2ATaskState:
            raise TypeError("task_state must be A2ATaskState")
        if self.task_state is A2ATaskState.UNSPECIFIED:
            raise ValueError("task_state must be specified")
        if type(self.status_sequence) is not int or self.status_sequence <= 0:
            raise ValueError("status_sequence must be a positive integer")
        terminal_values = (
            self.delivered_at,
            self.suppressed_at,
            self.abandoned_at,
        )
        if sum(value is not None for value in terminal_values) > 1:
            raise ValueError("push delivery terminal timestamps are mutually exclusive")
        for field_name, value in zip(
            ("delivered_at", "suppressed_at", "abandoned_at"),
            terminal_values,
            strict=True,
        ):
            if value is None:
                continue
            if (
                type(value) is not datetime
                or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must be timezone-aware")
            object.__setattr__(self, field_name, value.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class A2ATaskBinding:
    """A2A task 到 AgentOS Session/Run 的 tenant-scoped 持久绑定。"""

    tenant_id: str
    task_id: str
    session_id: str
    run_id: str

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.task_id, "task_id")
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        if self.task_id != self.run_id:
            raise ValueError("task_id must equal run_id in a2a binding v1")


class A2ATaskState(str, Enum):
    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    REJECTED = "TASK_STATE_REJECTED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"


class A2APushFailureCategory(str, Enum):
    HTTP_REJECTED = "http_rejected"
    NETWORK = "network"
    SECURITY = "security"
    RESPONSE_TOO_LARGE = "response_too_large"
    SECRET_UNAVAILABLE = "secret_unavailable"
    PROTOCOL_ENCODE = "protocol_encode"


class A2APushAttemptResolution(str, Enum):
    RETRY_PENDING = "retry_pending"
    ACK_SAFE = "ack_safe"


@dataclass(frozen=True, slots=True)
class A2ATaskListQuery:
    context_id: str | None = None
    status: A2ATaskState | None = None
    page_size: int = 50
    page_token: str | None = None
    history_length: int | None = None
    status_timestamp_after: datetime | None = None
    include_artifacts: bool = False

    def __post_init__(self) -> None:
        if self.context_id is not None:
            require_identifier(self.context_id, "a2a context_id")
        if self.status is not None and (
            type(self.status) is not A2ATaskState
            or self.status is A2ATaskState.UNSPECIFIED
        ):
            raise ValueError("a2a task status filter is invalid")
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if self.page_token is not None:
            _require_input_page_token(self.page_token)
        if self.history_length is not None and (
            type(self.history_length) is not int or self.history_length < 0
        ):
            raise ValueError("history_length must be non-negative")
        if self.status_timestamp_after is not None:
            if (
                type(self.status_timestamp_after) is not datetime
                or self.status_timestamp_after.utcoffset() is None
            ):
                raise ValueError("status_timestamp_after must be timezone-aware")
            object.__setattr__(
                self,
                "status_timestamp_after",
                self.status_timestamp_after.astimezone(UTC),
            )
        if type(self.include_artifacts) is not bool:
            raise TypeError("include_artifacts must be bool")


@dataclass(frozen=True, slots=True)
class A2ATaskListItem:
    binding: A2ATaskBinding
    run: RunReadModel
    status_updated_at: datetime

    def __post_init__(self) -> None:
        if type(self.binding) is not A2ATaskBinding:
            raise TypeError("binding must be A2ATaskBinding")
        if type(self.run) is not RunReadModel:
            raise TypeError("run must be RunReadModel")
        if (
            self.binding.tenant_id != self.run.tenant_id
            or self.binding.session_id != self.run.session_id
            or self.binding.run_id != self.run.run_id
        ):
            raise ValueError("a2a task list item binding does not match run")
        if (
            type(self.status_updated_at) is not datetime
            or self.status_updated_at.utcoffset() is None
        ):
            raise ValueError("status_updated_at must be timezone-aware")
        object.__setattr__(
            self,
            "status_updated_at",
            self.status_updated_at.astimezone(UTC),
        )


@dataclass(frozen=True, slots=True)
class A2ATaskListPage:
    items: tuple[A2ATaskListItem, ...]
    next_page_token: str
    page_size: int
    total_size: int

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or any(
            type(item) is not A2ATaskListItem for item in self.items
        ):
            raise TypeError("items must be a tuple of A2ATaskListItem")
        _require_page_token(self.next_page_token)
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if (
            type(self.total_size) is not int
            or self.total_size < 0
            or self.total_size < len(self.items)
        ):
            raise ValueError("total_size is invalid")


def _require_url(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > _MAX_PUSH_URL_BYTES
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError("a2a push url is invalid")


def _require_optional_secret(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > _MAX_PUSH_SECRET_BYTES
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError(f"a2a push {field_name} is invalid")


def _require_page_token(value: object) -> None:
    if (
        type(value) is not str
        or len(value) > 1024
        or any(ord(character) > 127 for character in value)
    ):
        raise ValueError("a2a push next_page_token is invalid")


def _require_input_page_token(value: object) -> None:
    _require_page_token(value)
    if not value:
        raise ValueError("page_token is invalid")


__all__ = [
    "A2APushAttemptResolution",
    "A2APushAuthenticationInput",
    "A2APushConfigInput",
    "A2APushConfigPage",
    "A2APushConfigRecord",
    "A2APushConfigRecordPage",
    "A2APushConfigView",
    "A2APushDeliveryTarget",
    "A2APushFailureCategory",
    "A2ATaskBinding",
    "A2ATaskListItem",
    "A2ATaskListPage",
    "A2ATaskListQuery",
    "A2ATaskState",
]
