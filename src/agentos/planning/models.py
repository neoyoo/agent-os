from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Mapping

from agentos.workspace import WorkspaceHandle, WorkspaceScope


PlanStatus = Literal["draft", "running", "completed", "failed", "cancelled"]
PLAN_STATUSES: tuple[PlanStatus, ...] = (
    "draft",
    "running",
    "completed",
    "failed",
    "cancelled",
)
PlanStepStatus = Literal[
    "pending",
    "assigned",
    "running",
    "completed",
    "failed",
    "blocked",
    "cancelled",
]
PlanStepRetryStatus = Literal["scheduled", "exhausted"]
PlanAssignmentDispatchStatus = Literal["pending", "submitted", "failed"]
EvidenceKind = Literal["text", "artifact", "task_result", "team_message", "external"]
EVIDENCE_KINDS: tuple[str, ...] = (
    "text",
    "artifact",
    "task_result",
    "team_message",
    "external",
)


@dataclass(frozen=True, slots=True)
class PlanRetryPolicy:
    """失败 Plan Step 的重试与退避策略。"""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    backoff_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")

    def delay_for_attempt(self, attempt: int) -> float:
        """返回一次失败尝试后的重试延迟。"""

        return self.backoff_seconds * (self.backoff_multiplier ** max(0, attempt - 1))


@dataclass(frozen=True, slots=True)
class SubAgentTemplate:
    """可复用 Subagent 执行模板。"""

    template_id: str
    name: str
    role: str
    capabilities: tuple[str, ...] = ()
    allowed_tool_names: tuple[str, ...] = ()
    context_seed: tuple[str, ...] = ()
    workspace_scope: WorkspaceScope = "task"
    target_agent_id: str | None = None
    timeout_seconds: float = 300


@dataclass(frozen=True, slots=True)
class EvidenceHandle:
    """Plan 中引用的 Evidence/Artifact 句柄。"""

    evidence_id: str
    kind: EvidenceKind
    summary: str
    uri: str | None = None
    producer_agent_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanStep:
    """Plan 中的一个可分配步骤。"""

    step_id: str
    instruction: str
    status: PlanStepStatus = "pending"
    required_capabilities: tuple[str, ...] = ()
    assigned_agent_id: str | None = None
    template_id: str | None = None
    task_id: str | None = None
    depends_on: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None
    attempts: int = 0
    last_failed_at: float | None = None
    next_retry_at: float | None = None
    retry_status: PlanStepRetryStatus | None = None
    retry_exhausted_at: float | None = None


@dataclass(frozen=True, slots=True)
class PlanAssignment:
    """Step 到 Task/Subagent 的分配记录。"""

    plan_id: str
    step_id: str
    template_id: str
    task_id: str
    target_agent_id: str
    created_at: float
    dispatch_status: PlanAssignmentDispatchStatus = "pending"
    submitted_at: float | None = None
    dispatch_error: str | None = None


@dataclass(frozen=True, slots=True)
class PlanState:
    """Planner State 的真值投影。"""

    plan_id: str
    objective: str
    owner_agent_id: str
    status: PlanStatus = "draft"
    steps: tuple[PlanStep, ...] = ()
    evidence: tuple[EvidenceHandle, ...] = ()
    assignments: tuple[PlanAssignment, ...] = ()
    created_at: float = 0
    updated_at: float = 0
    workspace: WorkspaceHandle | None = None

    def with_status(self, status: PlanStatus, *, now: float) -> "PlanState":
        """返回更新状态后的 Plan。"""

        return replace(self, status=status, updated_at=now)
