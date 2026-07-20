from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias

from agentos.distributed._model_validation import (
    normalize_optional_utc,
    require_identifier,
    require_non_negative,
)
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    PlanUpdated,
    SkillLoaded,
    StatusUpdate,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamCancelled,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamStarted,
    TurnStreamWaiting,
)


LiveContextSource: TypeAlias = Literal["runtime", "memory", "session", "attachment"]
LivePlanStatus: TypeAlias = Literal["created", "updated", "completed"]
LiveToolStatusKind: TypeAlias = Literal["started", "completed", "failed"]
LiveWaitKind: TypeAlias = Literal[
    "human_input",
    "timer",
    "remote_result",
    "resource_availability",
    "retry_backoff",
    "side_effect_reconciliation",
]

_CONTEXT_SOURCES = frozenset({"runtime", "memory", "session", "attachment"})
_PLAN_STATUSES = frozenset({"created", "updated", "completed"})
_TOOL_STATUSES = frozenset({"started", "completed", "failed"})
_WAIT_KINDS = frozenset(
    {
        "human_input",
        "timer",
        "remote_result",
        "resource_availability",
        "retry_backoff",
        "side_effect_reconciliation",
    },
)
_TIMED_WAIT_KINDS = frozenset({"timer", "retry_backoff"})


@dataclass(frozen=True, slots=True)
class LiveTurnStarted:
    """A turn started; the original user prompt is intentionally omitted."""


@dataclass(frozen=True, slots=True)
class LiveStatusUpdate:
    stage: str
    message: str

    def __post_init__(self) -> None:
        require_identifier(self.stage, "stage")
        _require_text(self.message, "message")


@dataclass(frozen=True, slots=True)
class LiveContextLoaded:
    source: LiveContextSource

    def __post_init__(self) -> None:
        if self.source not in _CONTEXT_SOURCES:
            raise ValueError("context source is invalid")


@dataclass(frozen=True, slots=True)
class LiveSkillLoaded:
    skill_name: str

    def __post_init__(self) -> None:
        require_identifier(self.skill_name, "skill_name")


@dataclass(frozen=True, slots=True)
class LivePlanUpdated:
    summary: str
    status: LivePlanStatus

    def __post_init__(self) -> None:
        _require_text(self.summary, "summary")
        if self.status not in _PLAN_STATUSES:
            raise ValueError("plan status is invalid")


@dataclass(frozen=True, slots=True)
class LiveContentDelta:
    index: int
    text: str

    def __post_init__(self) -> None:
        require_non_negative(self.index, "index")
        _require_text(self.text, "text")


@dataclass(frozen=True, slots=True)
class LiveToolStatus:
    tool_name: str
    tool_call_id: str
    status: LiveToolStatusKind

    def __post_init__(self) -> None:
        require_identifier(self.tool_name, "tool_name")
        require_identifier(self.tool_call_id, "tool_call_id")
        if self.status not in _TOOL_STATUSES:
            raise ValueError("tool status is invalid")


@dataclass(frozen=True, slots=True)
class LiveFinalResult:
    content: str

    def __post_init__(self) -> None:
        _require_text(self.content, "content")


@dataclass(frozen=True, slots=True)
class LiveTurnCompleted:
    """A completed marker; terminal content is carried by LiveFinalResult."""


@dataclass(frozen=True, slots=True)
class LiveTurnWaiting:
    kind: LiveWaitKind
    handle: str
    not_before: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in _WAIT_KINDS:
            raise ValueError("wait kind is invalid")
        require_identifier(self.handle, "handle")
        not_before = normalize_optional_utc(self.not_before, "not_before")
        if self.kind in _TIMED_WAIT_KINDS and not_before is None:
            raise ValueError("timed wait requires not_before")
        if self.kind not in _TIMED_WAIT_KINDS and not_before is not None:
            raise ValueError("not_before is only valid for a timed wait")
        object.__setattr__(self, "not_before", not_before)


@dataclass(frozen=True, slots=True)
class LiveTurnFailed:
    """A failed marker; exception objects and messages are intentionally omitted."""


@dataclass(frozen=True, slots=True)
class LiveTurnCancelled:
    """A cancelled marker; internal cancellation detail is intentionally omitted."""


LiveRunEvent: TypeAlias = (
    LiveTurnStarted
    | LiveStatusUpdate
    | LiveContextLoaded
    | LiveSkillLoaded
    | LivePlanUpdated
    | LiveContentDelta
    | LiveToolStatus
    | LiveFinalResult
    | LiveTurnCompleted
    | LiveTurnWaiting
    | LiveTurnFailed
    | LiveTurnCancelled
)


def live_event_kind(event: LiveRunEvent) -> str:
    """Return the stable kind derived from an allowlisted live event."""

    event_type = type(event)
    kinds = {
        LiveTurnStarted: "turn_started",
        LiveStatusUpdate: "status_update",
        LiveContextLoaded: "context_loaded",
        LiveSkillLoaded: "skill_loaded",
        LivePlanUpdated: "plan_updated",
        LiveContentDelta: "content_delta",
        LiveFinalResult: "final_result",
        LiveTurnCompleted: "turn_completed",
        LiveTurnWaiting: "turn_waiting",
        LiveTurnFailed: "turn_failed",
        LiveTurnCancelled: "turn_cancelled",
    }
    if event_type is LiveToolStatus:
        return f"tool_{event.status}"
    try:
        return kinds[event_type]
    except KeyError as error:
        raise TypeError("event must be an allowlisted live event") from error


def project_live_event(event: TurnStreamEvent) -> LiveRunEvent | None:
    """Project a runtime stream event to the Redis-safe live-event contract."""

    event_type = type(event)
    if event_type is TurnStreamStarted:
        return LiveTurnStarted()
    if event_type is StatusUpdate:
        return LiveStatusUpdate(event.stage, event.message)
    if event_type is ContextLoaded:
        return LiveContextLoaded(event.source)
    if event_type is SkillLoaded:
        return LiveSkillLoaded(event.skill_name)
    if event_type is PlanUpdated:
        return LivePlanUpdated(event.summary, event.status)
    if event_type is AssistantContentDelta:
        return LiveContentDelta(event.index, event.text)
    if event_type in {AssistantThinkingDelta, AssistantCompleted}:
        return None
    if event_type is FinalResult:
        return LiveFinalResult(event.content)
    if event_type is ToolStreamStarted:
        return LiveToolStatus(event.tool_name, event.tool_call_id, "started")
    if event_type is ToolStreamCompleted:
        return LiveToolStatus(event.tool_name, event.tool_call_id, "completed")
    if event_type is ToolStreamFailed:
        return LiveToolStatus(event.tool_name, event.tool_call_id, "failed")
    if event_type is TurnStreamCompleted:
        return LiveTurnCompleted()
    if event_type is TurnStreamWaiting:
        return LiveTurnWaiting(
            event.reason.kind,
            event.reason.handle,
            event.reason.not_before,
        )
    if event_type is TurnStreamFailed:
        return LiveTurnFailed()
    if event_type is TurnStreamCancelled:
        return LiveTurnCancelled()
    raise TypeError("event must be TurnStreamEvent")


def _require_text(value: object, field_name: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str")
