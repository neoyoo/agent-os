from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from agentos._waiting import WaitReason
from agentos.runtime.errors import RunProtocolError


class RunStatus(StrEnum):
    """Run 聚合根的生命周期状态。"""

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class RunStateTransitionError(RunProtocolError):
    """Run 状态转换违反状态机契约。"""


class RunNotFoundError(RunProtocolError):
    """当前 Session 中不存在指定 Run。"""


class RunAlreadyExistsError(RunProtocolError):
    """当前 Session 中已经存在同名 Run。"""


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED}),
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING,
        },
    ),
    RunStatus.WAITING: frozenset({RunStatus.QUEUED, RunStatus.CANCELLED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RunState:
    """一个 Session 内 Run 聚合根的进程内权威状态。"""

    run_id: str
    session_id: str
    status: RunStatus = RunStatus.CREATED
    wait_reason: WaitReason | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        if not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if self.status is RunStatus.WAITING and self.wait_reason is None:
            raise ValueError("waiting run requires a wait reason")
        if self.status is not RunStatus.WAITING and self.wait_reason is not None:
            raise ValueError("wait reason is only valid for a waiting run")

    def transition(
        self,
        status: RunStatus,
        *,
        wait_reason: WaitReason | None = None,
    ) -> RunState:
        """校验状态边并返回新的不可变 RunState。"""

        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise RunStateTransitionError(
                f"illegal run transition: {self.status.value} -> {status.value}",
            )
        if status is RunStatus.WAITING and wait_reason is None:
            raise RunStateTransitionError(
                "waiting transition requires a wait reason",
            )
        if status is not RunStatus.WAITING and wait_reason is not None:
            raise RunStateTransitionError(
                "wait reason is only valid for a waiting transition",
            )
        return replace(
            self,
            status=status,
            wait_reason=wait_reason if status is RunStatus.WAITING else None,
        )
