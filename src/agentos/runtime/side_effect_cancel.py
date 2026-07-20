from __future__ import annotations

from dataclasses import dataclass

from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectInFlightError,
    SideEffectRecord,
    SideEffectRecordConflictError,
    SideEffectStatus,
)


@dataclass(frozen=True, slots=True)
class SideEffectCancelPlan:
    """cancel transaction 在写入前得到的确定性 safe-stop 计划。"""

    cancel_before_start: tuple[SideEffectAttemptId, ...] = ()


def plan_side_effect_cancel(
    records: tuple[SideEffectRecord, ...],
) -> SideEffectCancelPlan:
    """只依据每个 operation 的 current attempt 生成 cancel safe-stop 计划。"""

    current: dict[tuple[str | None, str, str], SideEffectRecord] = {}
    for record in records:
        if type(record) is not SideEffectRecord:
            raise TypeError("records must contain SideEffectRecord values")
        key = (
            record.attempt_id.tenant_id,
            record.attempt_id.session_id,
            record.attempt_id.operation_id,
        )
        existing = current.get(key)
        if existing is None or existing.attempt_id.attempt < record.attempt_id.attempt:
            current[key] = record
        elif existing.attempt_id.attempt == record.attempt_id.attempt and existing != record:
            raise SideEffectRecordConflictError
    ordered = sorted(current.values(), key=_record_order)
    if any(
        record.status
        in {
            SideEffectStatus.STARTED,
            SideEffectStatus.AMBIGUOUS,
            SideEffectStatus.COMPENSATING,
        }
        for record in ordered
    ):
        raise SideEffectInFlightError
    return SideEffectCancelPlan(
        tuple(
            record.attempt_id
            for record in ordered
            if record.status is SideEffectStatus.RESERVED
        ),
    )


def _record_order(record: SideEffectRecord) -> tuple[str, str, str, int]:
    return (
        record.attempt_id.tenant_id or "",
        record.attempt_id.session_id,
        record.attempt_id.operation_id,
        record.attempt_id.attempt,
    )


__all__ = ["SideEffectCancelPlan", "plan_side_effect_cancel"]
