from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from agentos.distributed._model_validation import (
    normalize_optional_utc,
    normalize_utc,
    require_identifier,
)
from agentos.workspace import WorkspaceHandle


TeamStatus: TypeAlias = Literal["active", "deleted"]
TeamMemberStatus: TypeAlias = Literal["active", "deleted"]
TeamMemberRole: TypeAlias = Literal["leader", "worker"]
TeamMessageKind: TypeAlias = Literal[
    "instruction",
    "observation",
    "result",
    "notice",
]
TeamAddressingKind: TypeAlias = Literal["direct", "broadcast"]
MAX_TEAM_MESSAGE_CONTENT_BYTES = 4096
MAX_TEAM_MEMBER_CAPABILITIES = 32
TEAM_MESSAGE_PAGE_LIMIT = 10
_TEAM_STATUSES = frozenset({"active", "deleted"})
_MEMBER_ROLES = frozenset({"leader", "worker"})
_MESSAGE_KINDS = frozenset({"instruction", "observation", "result", "notice"})
_ADDRESSING_KINDS = frozenset({"direct", "broadcast"})


@dataclass(frozen=True, slots=True)
class TeamRecord:
    """Tenant-scoped Team 的持久领域状态。"""

    team_id: str
    leader_agent_id: str
    workspace: WorkspaceHandle | None
    created_at: datetime
    status: TeamStatus = "active"
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        require_identifier(self.team_id, "team_id")
        require_identifier(self.leader_agent_id, "leader_agent_id")
        if self.workspace is not None and type(self.workspace) is not WorkspaceHandle:
            raise TypeError("workspace must be WorkspaceHandle or None")
        _normalize_lifecycle(self, "status")


@dataclass(frozen=True, slots=True)
class TeamMemberRecord:
    """Team 中绑定到目标 Session 的一个成员。"""

    team_id: str
    recipient_agent_id: str
    role: TeamMemberRole
    target_session_id: str
    created_at: datetime
    capabilities: tuple[str, ...] = ()
    status: TeamMemberStatus = "active"
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        require_identifier(self.team_id, "team_id")
        require_identifier(self.recipient_agent_id, "recipient_agent_id")
        require_identifier(self.target_session_id, "target_session_id")
        if self.role not in _MEMBER_ROLES:
            raise ValueError("role is invalid")
        capabilities = _normalized_capabilities(self.capabilities)
        object.__setattr__(self, "capabilities", capabilities)
        _normalize_lifecycle(self, "status")


@dataclass(frozen=True, slots=True)
class TeamRecipient:
    """首次寻址时冻结的 recipient 与 Session 绑定。"""

    recipient_agent_id: str
    target_session_id: str

    def __post_init__(self) -> None:
        require_identifier(self.recipient_agent_id, "recipient_agent_id")
        require_identifier(self.target_session_id, "target_session_id")


@dataclass(frozen=True, slots=True)
class TeamAccessContext:
    """Worker claim 时从 PostgreSQL active binding 重建的 Team 权限。"""

    tenant_id: str
    team_id: str
    recipient_agent_id: str
    target_session_id: str

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.team_id, "team_id")
        require_identifier(self.recipient_agent_id, "recipient_agent_id")
        require_identifier(self.target_session_id, "target_session_id")


@dataclass(frozen=True, slots=True)
class TeamMessageRequest:
    """Application Service 提交 Team message 的不可变事务输入。"""

    team_id: str
    sender_agent_id: str
    operation_id: str
    message_kind: TeamMessageKind
    content: str
    correlation_id: str | None
    addressing_kind: TeamAddressingKind
    addressed_agent_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        created_at = _validate_message_fields(
            team_id=self.team_id,
            sender_agent_id=self.sender_agent_id,
            operation_id=self.operation_id,
            message_kind=self.message_kind,
            content=self.content,
            correlation_id=self.correlation_id,
            addressing_kind=self.addressing_kind,
            addressed_agent_id=self.addressed_agent_id,
            created_at=self.created_at,
        )
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class TeamMessage:
    """Team conversation 中的一条 canonical durable message。"""

    message_id: str
    team_id: str
    sender_agent_id: str
    operation_id: str
    message_kind: TeamMessageKind
    content: str
    correlation_id: str | None
    addressing_kind: TeamAddressingKind
    addressed_agent_id: str | None
    recipient_snapshot: tuple[TeamRecipient, ...]
    request_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_prefixed_digest(self.message_id, "team_msg_", "message_id")
        created_at = _validate_message_fields(
            team_id=self.team_id,
            sender_agent_id=self.sender_agent_id,
            operation_id=self.operation_id,
            message_kind=self.message_kind,
            content=self.content,
            correlation_id=self.correlation_id,
            addressing_kind=self.addressing_kind,
            addressed_agent_id=self.addressed_agent_id,
            created_at=self.created_at,
        )
        recipients = tuple(self.recipient_snapshot)
        if not recipients or any(type(item) is not TeamRecipient for item in recipients):
            raise ValueError("recipient_snapshot must contain recipients")
        recipients = tuple(sorted(recipients, key=lambda item: item.recipient_agent_id))
        agent_ids = tuple(item.recipient_agent_id for item in recipients)
        session_ids = tuple(item.target_session_id for item in recipients)
        if len(set(agent_ids)) != len(agent_ids) or len(set(session_ids)) != len(session_ids):
            raise ValueError("recipient_snapshot must contain unique bindings")
        if self.addressing_kind == "direct" and agent_ids != (self.addressed_agent_id,):
            raise ValueError("recipient_snapshot must match direct addressing")
        if self.addressing_kind == "broadcast" and self.sender_agent_id in agent_ids:
            raise ValueError("recipient_snapshot must exclude broadcast sender")
        _require_digest(self.request_sha256, "request_sha256")
        object.__setattr__(self, "recipient_snapshot", recipients)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class TeamMessagePage:
    """有界 Team message keyset page。"""

    messages: tuple[TeamMessage, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if any(type(message) is not TeamMessage for message in messages):
            raise TypeError("messages must contain TeamMessage values")
        if len({message.team_id for message in messages}) > 1:
            raise ValueError("message page cannot mix team scopes")
        message_ids = tuple(message.message_id for message in messages)
        if len(set(message_ids)) != len(message_ids):
            raise ValueError("message page cannot contain duplicate messages")
        if self.next_cursor is not None:
            require_identifier(self.next_cursor, "next_cursor")
            if not messages or self.next_cursor != messages[-1].message_id:
                raise ValueError("next_cursor must equal the last message ID")
        object.__setattr__(self, "messages", messages)


def _normalize_lifecycle(value: object, status_field: str) -> None:
    status = getattr(value, status_field)
    if status not in _TEAM_STATUSES:
        raise ValueError(f"{status_field} is invalid")
    created_at = normalize_utc(getattr(value, "created_at"), "created_at")
    deleted_at = normalize_optional_utc(getattr(value, "deleted_at"), "deleted_at")
    if (status == "active") != (deleted_at is None):
        raise ValueError("deleted status requires deleted_at")
    if deleted_at is not None and deleted_at < created_at:
        raise ValueError("deleted_at cannot be before created_at")
    object.__setattr__(value, "created_at", created_at)
    object.__setattr__(value, "deleted_at", deleted_at)


def _normalized_capabilities(value: object) -> tuple[str, ...]:
    if type(value) not in {tuple, list}:
        raise TypeError("capabilities must contain identifier values")
    if len(value) > MAX_TEAM_MEMBER_CAPABILITIES:
        raise ValueError("capabilities cannot contain more than 32 values")
    capabilities = tuple(value)
    for capability in capabilities:
        require_identifier(capability, "capability")
    if len(set(capabilities)) != len(capabilities):
        raise ValueError("capabilities cannot contain duplicate values")
    return tuple(sorted(capabilities))


def _validate_message_fields(
    *,
    team_id: str,
    sender_agent_id: str,
    operation_id: str,
    message_kind: str,
    content: object,
    correlation_id: str | None,
    addressing_kind: str,
    addressed_agent_id: str | None,
    created_at: datetime,
) -> datetime:
    require_identifier(team_id, "team_id")
    require_identifier(sender_agent_id, "sender_agent_id")
    require_identifier(operation_id, "operation_id")
    if message_kind not in _MESSAGE_KINDS:
        raise ValueError("message_kind is invalid")
    if type(content) is not str or not content.strip():
        raise ValueError("content must be a non-empty string")
    try:
        content_bytes = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("content must be valid UTF-8") from error
    if len(content_bytes) > MAX_TEAM_MESSAGE_CONTENT_BYTES:
        raise ValueError("content must not exceed 4096 UTF-8 bytes")
    if correlation_id is not None:
        require_identifier(correlation_id, "correlation_id")
    if addressing_kind not in _ADDRESSING_KINDS:
        raise ValueError("addressing_kind is invalid")
    if addressing_kind == "direct":
        if addressed_agent_id is None:
            raise ValueError("direct addressing requires addressed_agent_id")
        require_identifier(addressed_agent_id, "addressed_agent_id")
    elif addressed_agent_id is not None:
        raise ValueError("broadcast addressing cannot contain addressed_agent_id")
    return normalize_utc(created_at, "created_at")


def _require_prefixed_digest(value: object, prefix: str, field_name: str) -> None:
    require_identifier(value, field_name)
    assert isinstance(value, str)
    if not value.startswith(prefix):
        raise ValueError(f"{field_name} is invalid")
    _require_digest(value.removeprefix(prefix), field_name)


def _require_digest(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


__all__ = [
    "MAX_TEAM_MESSAGE_CONTENT_BYTES",
    "MAX_TEAM_MEMBER_CAPABILITIES",
    "TEAM_MESSAGE_PAGE_LIMIT",
    "TeamAccessContext",
    "TeamAddressingKind",
    "TeamMemberRecord",
    "TeamMemberRole",
    "TeamMemberStatus",
    "TeamMessage",
    "TeamMessageKind",
    "TeamMessagePage",
    "TeamMessageRequest",
    "TeamRecipient",
    "TeamRecord",
    "TeamStatus",
]
