from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from agentos.distributed._model_validation import (
    normalize_optional_utc,
    normalize_utc,
    require_identifier,
    require_non_negative,
    require_positive,
)
from agentos.multi.team_types import (
    TeamMessage,
    TeamRecipient,
    _require_digest,
    _require_prefixed_digest,
)
from agentos.runtime.run_state import RunStatus


TeamDeliveryState: TypeAlias = Literal["pending", "claimed", "applied", "rejected"]
TeamDeliveryResultKind: TypeAlias = Literal[
    "internal_start",
    "wakeup",
    "rejected_binding_revoked",
    "rejected_nonterminal",
]

_DELIVERY_STATES = frozenset({"pending", "claimed", "applied", "rejected"})
_RESULT_KINDS = frozenset(
    {"internal_start", "wakeup", "rejected_binding_revoked", "rejected_nonterminal"},
)


@dataclass(frozen=True, slots=True)
class TeamDelivery:
    """一条 per-recipient Team message delivery 的持久状态。"""

    delivery_id: str
    team_id: str
    message_id: str
    recipient_agent_id: str
    target_session_id: str
    state: TeamDeliveryState
    fencing_token: int
    source_sha256: str
    created_at: datetime
    updated_at: datetime
    claim_id: str | None = None
    claim_expires_at: datetime | None = None
    result_kind: TeamDeliveryResultKind | None = None
    observed_run_id: str | None = None
    observed_aggregate_version: int | None = None
    observed_run_status: RunStatus | None = None

    def __post_init__(self) -> None:
        _require_prefixed_digest(self.delivery_id, "team_delivery_", "delivery_id")
        require_identifier(self.team_id, "team_id")
        _require_prefixed_digest(self.message_id, "team_msg_", "message_id")
        require_identifier(self.recipient_agent_id, "recipient_agent_id")
        require_identifier(self.target_session_id, "target_session_id")
        if self.state not in _DELIVERY_STATES:
            raise ValueError("state is invalid")
        require_non_negative(self.fencing_token, "fencing_token")
        _require_digest(self.source_sha256, "source_sha256")
        created_at = normalize_utc(self.created_at, "created_at")
        updated_at = normalize_utc(self.updated_at, "updated_at")
        expires_at = normalize_optional_utc(self.claim_expires_at, "claim_expires_at")
        if updated_at < created_at:
            raise ValueError("updated_at cannot be before created_at")
        _validate_delivery_shape(self, expires_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "claim_expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class TeamMessageReceipt:
    """Team message 与首次 fanout 的原子写入回执。"""

    message: TeamMessage
    deliveries: tuple[TeamDelivery, ...]
    duplicate: bool

    def __post_init__(self) -> None:
        if type(self.message) is not TeamMessage:
            raise TypeError("message must be TeamMessage")
        deliveries = tuple(self.deliveries)
        if any(type(item) is not TeamDelivery for item in deliveries):
            raise TypeError("deliveries must contain TeamDelivery values")
        deliveries = tuple(sorted(deliveries, key=lambda item: item.recipient_agent_id))
        expected = tuple(item.recipient_agent_id for item in self.message.recipient_snapshot)
        actual = tuple(item.recipient_agent_id for item in deliveries)
        expected_bindings = tuple(
            (item.recipient_agent_id, item.target_session_id)
            for item in self.message.recipient_snapshot
        )
        actual_bindings = tuple(
            (item.recipient_agent_id, item.target_session_id)
            for item in deliveries
        )
        if (
            actual != expected
            or actual_bindings != expected_bindings
            or any(
                item.team_id != self.message.team_id
                or item.message_id != self.message.message_id
                for item in deliveries
            )
        ):
            raise ValueError("deliveries must match message recipient_snapshot")
        if type(self.duplicate) is not bool:
            raise TypeError("duplicate must be bool")
        object.__setattr__(self, "deliveries", deliveries)


@dataclass(frozen=True, slots=True)
class TeamDeliveryClaim:
    """数据库时间约束的 Team delivery claim 与 fencing token。"""

    tenant_id: str
    team_id: str
    delivery_id: str
    claim_id: str
    fencing_token: int
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.team_id, "team_id")
        _require_prefixed_digest(self.delivery_id, "team_delivery_", "delivery_id")
        require_identifier(self.claim_id, "claim_id")
        require_positive(self.fencing_token, "fencing_token")
        object.__setattr__(self, "expires_at", normalize_utc(self.expires_at, "expires_at"))


@dataclass(frozen=True, slots=True)
class TeamDeliveryTarget:
    """PostgreSQL 根据 opaque outbox ID 解析出的权威 Team delivery。"""

    tenant_id: str
    outbox_id: str
    delivery: TeamDelivery
    message: TeamMessage

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        _require_prefixed_digest(self.outbox_id, "team_outbox_", "outbox_id")
        if type(self.delivery) is not TeamDelivery:
            raise TypeError("delivery must be TeamDelivery")
        if type(self.message) is not TeamMessage:
            raise TypeError("message must be TeamMessage")
        recipient = TeamRecipient(
            self.delivery.recipient_agent_id,
            self.delivery.target_session_id,
        )
        if (
            self.message.team_id != self.delivery.team_id
            or self.message.message_id != self.delivery.message_id
            or recipient not in self.message.recipient_snapshot
        ):
            raise ValueError("message does not match delivery target")


@dataclass(frozen=True, slots=True)
class ClaimedTeamDelivery:
    """权威 Team delivery target 与当前 claim 的绑定。"""

    target: TeamDeliveryTarget
    claim: TeamDeliveryClaim

    def __post_init__(self) -> None:
        if type(self.target) is not TeamDeliveryTarget:
            raise TypeError("target must be TeamDeliveryTarget")
        if type(self.claim) is not TeamDeliveryClaim:
            raise TypeError("claim must be TeamDeliveryClaim")
        delivery = self.target.delivery
        if (
            self.claim.tenant_id != self.target.tenant_id
            or self.claim.team_id != delivery.team_id
            or self.claim.delivery_id != delivery.delivery_id
            or delivery.state != "claimed"
            or self.claim.claim_id != delivery.claim_id
            or self.claim.fencing_token != delivery.fencing_token
            or self.claim.expires_at != delivery.claim_expires_at
        ):
            raise ValueError("claim does not match delivery target")


@dataclass(frozen=True, slots=True)
class TeamDeliveryResult:
    """Team Worker 提交 fenced delivery result 的领域输入。"""

    result_kind: TeamDeliveryResultKind
    observed_run_id: str | None
    observed_aggregate_version: int | None
    observed_run_status: RunStatus | None

    def __post_init__(self) -> None:
        if self.result_kind not in _RESULT_KINDS:
            raise ValueError("result_kind is invalid")
        observed = _validate_observed_run(
            self.observed_run_id,
            self.observed_aggregate_version,
            self.observed_run_status,
        )
        if self.result_kind in {"internal_start", "wakeup", "rejected_nonterminal"}:
            if not observed:
                raise ValueError("result_kind requires observed run state")
        elif observed:
            raise ValueError("binding-revoked result cannot contain observed run state")
        if self.result_kind == "rejected_nonterminal" and self.observed_run_status not in {
            RunStatus.CREATED,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
            RunStatus.WAITING,
        }:
            raise ValueError("rejected_nonterminal requires a nonterminal run status")


def _validate_delivery_shape(value: TeamDelivery, expires_at: datetime | None) -> None:
    if value.claim_id is not None:
        require_identifier(value.claim_id, "claim_id")
    observed = _validate_observed_run(
        value.observed_run_id,
        value.observed_aggregate_version,
        value.observed_run_status,
    )
    if value.state == "claimed":
        if value.claim_id is None or expires_at is None or value.fencing_token < 1:
            raise ValueError("claimed delivery requires claim identity, expiry, and fence")
        if expires_at <= value.updated_at:
            raise ValueError("claimed delivery expiry must be after updated_at")
        if value.result_kind is not None or observed:
            raise ValueError("claimed delivery cannot contain a result")
        return
    if value.claim_id is not None or expires_at is not None:
        raise ValueError("non-claimed delivery cannot contain claim state")
    if value.state == "pending":
        if value.result_kind is not None or observed:
            raise ValueError("pending delivery cannot contain a result")
        return
    require_positive(value.fencing_token, "fencing_token")
    if value.result_kind not in _RESULT_KINDS:
        raise ValueError("terminal delivery requires result_kind")
    TeamDeliveryResult(
        result_kind=value.result_kind,
        observed_run_id=value.observed_run_id,
        observed_aggregate_version=value.observed_aggregate_version,
        observed_run_status=value.observed_run_status,
    )
    if value.state == "applied":
        if value.result_kind not in {"internal_start", "wakeup"}:
            raise ValueError("applied delivery result_kind is invalid")
        if not observed:
            raise ValueError("applied delivery requires observed run state")
    elif value.result_kind not in {
        "rejected_binding_revoked",
        "rejected_nonterminal",
    }:
        raise ValueError("rejected delivery result_kind is invalid")


def _validate_observed_run(
    run_id: str | None,
    version: int | None,
    status: RunStatus | None,
) -> bool:
    values = (run_id, version, status)
    if all(value is None for value in values):
        return False
    if any(value is None for value in values):
        raise ValueError("observed run fields must be provided together")
    require_identifier(run_id, "observed_run_id")
    require_non_negative(version, "observed_aggregate_version")
    if type(status) is not RunStatus:
        raise TypeError("observed_run_status must be RunStatus")
    return True


__all__ = [
    "ClaimedTeamDelivery",
    "TeamDelivery",
    "TeamDeliveryClaim",
    "TeamDeliveryResult",
    "TeamDeliveryResultKind",
    "TeamDeliveryState",
    "TeamDeliveryTarget",
    "TeamMessageReceipt",
]
