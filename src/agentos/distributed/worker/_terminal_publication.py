from __future__ import annotations

from agentos.distributed._execution_outcomes import CommittedExecutionOutcome
from agentos.distributed.models import (
    LiveRunEvent,
    LiveTurnCancelled,
    LiveTurnCompleted,
    LiveTurnFailed,
    LiveTurnWaiting,
    RunEventEnvelope,
)
from agentos.distributed._stream_models import TERMINAL_EVENT_SEQUENCE
from agentos.runtime.run_state import RunStatus


def terminal_event(outcome: CommittedExecutionOutcome) -> LiveRunEvent:
    """从 PostgreSQL committed outcome 构造唯一 terminal projection。"""

    if not outcome.is_current:
        raise ValueError("superseded outcome has no current terminal event")
    run = outcome.target.run
    if run.status is RunStatus.COMPLETED:
        return LiveTurnCompleted()
    if run.status is RunStatus.FAILED:
        return LiveTurnFailed()
    if run.status is RunStatus.CANCELLED:
        return LiveTurnCancelled()
    if run.status is RunStatus.WAITING:
        reason = run.wait_reason
        if reason is None:
            raise ValueError("waiting outcome requires a wait reason")
        return LiveTurnWaiting(reason.kind, reason.handle, reason.not_before)
    raise ValueError("outcome is not terminal or waiting")


def terminal_envelope(outcome: CommittedExecutionOutcome) -> RunEventEnvelope:
    """使用 checkpoint identity 构造可重试且字节稳定的 terminal envelope。"""

    target = outcome.target
    return RunEventEnvelope(
        tenant_id=target.scope.tenant_id,
        session_id=target.session_id,
        run_id=target.run.run_id,
        turn_id=outcome.turn_id,
        execution_attempt=outcome.execution_attempt,
        event_sequence=TERMINAL_EVENT_SEQUENCE,
        event=terminal_event(outcome),
        occurred_at=outcome.committed_at,
    )


__all__ = ["terminal_envelope", "terminal_event"]
