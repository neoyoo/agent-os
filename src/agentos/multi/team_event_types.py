from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from agentos.distributed._model_validation import (
    normalize_utc,
    require_identifier,
    require_positive,
)
from agentos.multi.team_delivery_types import TeamDeliveryResult
from agentos.multi.team_types import _require_prefixed_digest


TeamEventKind: TypeAlias = Literal["delivery_applied", "delivery_rejected"]
TeamStreamGapReason: TypeAlias = Literal["trimmed", "unavailable"]

_GAP_REASONS = frozenset({"trimmed", "unavailable"})


@dataclass(frozen=True, slots=True)
class TeamDeliveryAppliedEvent:
    """一条 Team delivery 已成功路由到 canonical Run。"""

    delivery_id: str
    result: TeamDeliveryResult

    def __post_init__(self) -> None:
        _require_prefixed_digest(self.delivery_id, "team_delivery_", "delivery_id")
        _require_result(self.result)
        if self.result.result_kind not in {"internal_start", "wakeup"}:
            raise ValueError("applied event requires an applied result")


@dataclass(frozen=True, slots=True)
class TeamDeliveryRejectedEvent:
    """一条 Team delivery 已按稳定原因终止。"""

    delivery_id: str
    result: TeamDeliveryResult

    def __post_init__(self) -> None:
        _require_prefixed_digest(self.delivery_id, "team_delivery_", "delivery_id")
        _require_result(self.result)
        if self.result.result_kind not in {
            "rejected_binding_revoked",
            "rejected_nonterminal",
        }:
            raise ValueError("rejected event requires a rejected result")


TeamEvent: TypeAlias = TeamDeliveryAppliedEvent | TeamDeliveryRejectedEvent


def team_event_kind(event: TeamEvent) -> TeamEventKind:
    """从 allowlist typed event 派生稳定 event kind。"""

    event_type = type(event)
    if event_type is TeamDeliveryAppliedEvent:
        return "delivery_applied"
    if event_type is TeamDeliveryRejectedEvent:
        return "delivery_rejected"
    raise TypeError("event must be an allowlisted Team event")


@dataclass(frozen=True, slots=True)
class TeamEventEnvelope:
    """PostgreSQL truth 与 Redis replay 共用的 typed Team event 信封。"""

    tenant_id: str
    team_id: str
    event_sequence: int
    event: TeamEvent
    occurred_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.team_id, "team_id")
        require_positive(self.event_sequence, "event_sequence")
        team_event_kind(self.event)
        object.__setattr__(self, "occurred_at", normalize_utc(self.occurred_at, "occurred_at"))

    @property
    def event_kind(self) -> TeamEventKind:
        return team_event_kind(self.event)


@dataclass(frozen=True, slots=True)
class TeamEventTarget:
    """PostgreSQL 根据 result outbox ID 解析出的权威 Team event。"""

    tenant_id: str
    outbox_id: str
    event: TeamEventEnvelope

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        _require_prefixed_digest(self.outbox_id, "team_outbox_", "outbox_id")
        if type(self.event) is not TeamEventEnvelope:
            raise TypeError("event must be TeamEventEnvelope")
        if self.event.tenant_id != self.tenant_id:
            raise ValueError("event tenant does not match target tenant")


@dataclass(frozen=True, slots=True)
class TeamEventReplayItem:
    """带稳定 Redis cursor 的 Team event。"""

    cursor: str
    event: TeamEventEnvelope

    def __post_init__(self) -> None:
        require_identifier(self.cursor, "cursor")
        if type(self.event) is not TeamEventEnvelope:
            raise TypeError("event must be TeamEventEnvelope")


@dataclass(frozen=True, slots=True)
class TeamEventReplayBatch:
    """Team replay 查询的有界结果。"""

    items: tuple[TeamEventReplayItem, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(type(item) is not TeamEventReplayItem for item in items):
            raise TypeError("items must contain TeamEventReplayItem values")
        scopes = {(item.event.tenant_id, item.event.team_id) for item in items}
        cursors = tuple(item.cursor for item in items)
        if len(scopes) > 1 or len(set(cursors)) != len(cursors):
            raise ValueError("replay batch must contain one team scope and unique cursors")
        if self.next_cursor is not None:
            require_identifier(self.next_cursor, "next_cursor")
        if items and self.next_cursor != items[-1].cursor:
            raise ValueError("next_cursor must equal the last item cursor")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class TeamStreamGap:
    """Team cursor 已无法连续 replay 的类型化结果。"""

    tenant_id: str
    team_id: str
    requested_cursor: str
    oldest_available_cursor: str | None
    reason: TeamStreamGapReason

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.team_id, "team_id")
        require_identifier(self.requested_cursor, "requested_cursor")
        if self.oldest_available_cursor is not None:
            require_identifier(self.oldest_available_cursor, "oldest_available_cursor")
        if self.reason not in _GAP_REASONS:
            raise ValueError("reason is invalid")
        if self.reason == "trimmed" and self.oldest_available_cursor is None:
            raise ValueError("trimmed gap requires oldest_available_cursor")


def _require_result(result: object) -> None:
    if type(result) is not TeamDeliveryResult:
        raise TypeError("result must be TeamDeliveryResult")


__all__ = [
    "TeamDeliveryAppliedEvent",
    "TeamDeliveryRejectedEvent",
    "TeamEvent",
    "TeamEventEnvelope",
    "TeamEventKind",
    "TeamEventReplayBatch",
    "TeamEventReplayItem",
    "TeamEventTarget",
    "TeamStreamGap",
    "TeamStreamGapReason",
    "team_event_kind",
]
