from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from agentos.distributed._live_events import LiveRunEvent, live_event_kind
from agentos.distributed._model_validation import (
    normalize_optional_utc,
    normalize_utc,
    require_identifier,
    require_non_negative,
    require_positive,
)


StreamGapReason: TypeAlias = Literal["trimmed", "unavailable"]
WorkerStatus: TypeAlias = Literal["created", "running", "draining", "closed"]

_STREAM_GAP_REASONS = frozenset({"trimmed", "unavailable"})
_WORKER_STATUSES = frozenset({"created", "running", "draining", "closed"})
TERMINAL_EVENT_SEQUENCE = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class RunEventEnvelope:
    """Replay 与 Transport 共用的 typed live event 信封。"""

    tenant_id: str
    session_id: str
    run_id: str
    turn_id: str
    execution_attempt: int
    event_sequence: int
    event: LiveRunEvent
    occurred_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.turn_id, "turn_id")
        require_positive(self.execution_attempt, "execution_attempt")
        require_non_negative(self.event_sequence, "event_sequence")
        live_event_kind(self.event)
        object.__setattr__(
            self,
            "occurred_at",
            normalize_utc(self.occurred_at, "occurred_at"),
        )

    @property
    def event_kind(self) -> str:
        return live_event_kind(self.event)


@dataclass(frozen=True, slots=True)
class ReplayItem:
    """一个带稳定 Redis cursor 的 live event。"""

    cursor: str
    event: RunEventEnvelope

    def __post_init__(self) -> None:
        require_identifier(self.cursor, "cursor")
        if type(self.event) is not RunEventEnvelope:
            raise TypeError("event must be RunEventEnvelope")


@dataclass(frozen=True, slots=True)
class ReplayBatch:
    """Replay 查询返回的有界事件批次。"""

    items: tuple[ReplayItem, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if any(type(item) is not ReplayItem for item in items):
            raise TypeError("replay items must contain ReplayItem values")
        cursors = tuple(item.cursor for item in items)
        if len(set(cursors)) != len(cursors):
            raise ValueError("replay item cursor must be unique")
        run_scopes = {
            (
                item.event.tenant_id,
                item.event.session_id,
                item.event.run_id,
            )
            for item in items
        }
        if len(run_scopes) > 1:
            raise ValueError("replay batch cannot mix run scopes")
        if self.next_cursor is not None:
            require_identifier(self.next_cursor, "next_cursor")
        if items and self.next_cursor != items[-1].cursor:
            raise ValueError("next_cursor must equal the last replay item cursor")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class StreamGap:
    """Cursor 已不可连续 replay 的类型化结果。"""

    tenant_id: str
    session_id: str
    run_id: str
    requested_cursor: str
    oldest_available_cursor: str | None
    reason: StreamGapReason

    def __post_init__(self) -> None:
        require_identifier(self.tenant_id, "tenant_id")
        require_identifier(self.session_id, "session_id")
        require_identifier(self.run_id, "run_id")
        require_identifier(self.requested_cursor, "requested_cursor")
        if self.oldest_available_cursor is not None:
            require_identifier(
                self.oldest_available_cursor,
                "oldest_available_cursor",
            )
        if self.reason not in _STREAM_GAP_REASONS:
            raise ValueError("stream gap reason is invalid")
        if self.reason == "trimmed" and self.oldest_available_cursor is None:
            raise ValueError("trimmed gap requires oldest_available_cursor")


@dataclass(frozen=True, slots=True)
class WorkerState:
    """Distributed Worker 生命周期的不可变 Readiness 快照。"""

    worker_id: str
    status: WorkerStatus
    accepting_claims: bool
    active_claim_count: int
    last_heartbeat_at: datetime | None = None
    drain_started_at: datetime | None = None

    def __post_init__(self) -> None:
        require_identifier(self.worker_id, "worker_id")
        if self.status not in _WORKER_STATUSES:
            raise ValueError("worker status is invalid")
        if type(self.accepting_claims) is not bool:
            raise TypeError("accepting_claims must be bool")
        require_non_negative(self.active_claim_count, "active_claim_count")
        heartbeat = normalize_optional_utc(
            self.last_heartbeat_at,
            "last_heartbeat_at",
        )
        drain_started = normalize_optional_utc(
            self.drain_started_at,
            "drain_started_at",
        )
        if self.accepting_claims and self.status != "running":
            raise ValueError("accepting_claims requires running worker")
        if self.status == "draining" and drain_started is None:
            raise ValueError("draining worker requires drain_started_at")
        if self.status == "closed" and self.active_claim_count:
            raise ValueError("closed worker cannot have active claims")
        object.__setattr__(self, "last_heartbeat_at", heartbeat)
        object.__setattr__(self, "drain_started_at", drain_started)
