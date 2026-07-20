from __future__ import annotations

from dataclasses import dataclass

from agentos.runtime._side_effect_validation import require_identifier
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.side_effect_types import (
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)


@dataclass(frozen=True, slots=True)
class SideEffectResume:
    """Store resolution 后恢复原 pending Tool pair 的精确 DTO。"""

    tenant_id: str | None
    session_id: str
    run_id: str
    continuation_turn_id: str
    source_cursor: RunExecutionCursor
    record: SideEffectRecord
    resolution: SideEffectResolution
    def __post_init__(self) -> None:
        if self.tenant_id is not None:
            require_identifier(self.tenant_id, "tenant_id")
        for name, value in (
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("continuation_turn_id", self.continuation_turn_id),
        ):
            require_identifier(value, name)
        if type(self.source_cursor) is not RunExecutionCursor:
            raise TypeError("source_cursor must be RunExecutionCursor")
        if type(self.record) is not SideEffectRecord:
            raise TypeError("record must be SideEffectRecord")
        if type(self.resolution) is not SideEffectResolution:
            raise TypeError("resolution must be SideEffectResolution")
        try:
            self._validate_pair()
        except ValueError:
            raise ValueError("side effect resume state is invalid") from None

    def _validate_pair(self) -> None:
        cursor = self.source_cursor
        record = self.record
        attempt_id = record.attempt_id
        if (
            cursor.stage != "pending_tools"
            or cursor.assistant_message_id is None
            or cursor.turn_id == self.continuation_turn_id
            or record.turn_id != cursor.turn_id
            or attempt_id.tenant_id != self.tenant_id
            or attempt_id.session_id != self.session_id
            or record.run_id != self.run_id
            or attempt_id.operation_id != self.resolution.operation_id
            or not _resolution_matches_record(self.resolution, record)
        ):
            raise ValueError
        matches = tuple(
            pending
            for pending in cursor.pending_tools
            if pending.invocation_id == record.invocation_id
        )
        if len(matches) != 1:
            raise ValueError
        source = matches[0]
        if (
            record.invocation_ref is None
            or source.invocation_ref != record.invocation_ref
            or source.tool_name != record.tool_name
        ):
            raise ValueError


def _resolution_matches_record(
    resolution: SideEffectResolution,
    record: SideEffectRecord,
) -> bool:
    if resolution.kind is SideEffectResolutionKind.ACCEPT_RESULT:
        return (
            record.status is SideEffectStatus.RESOLVED
            and record.resolution is SideEffectResolutionOutcome.ACCEPTED
            and record.result_ref == resolution.result_ref
            and record.result_digest == resolution.result_digest
        )
    if resolution.kind is SideEffectResolutionKind.RETRY_PROVEN_SAFE:
        return (
            record.attempt_id.attempt > 1
            and record.status in {
                SideEffectStatus.RESERVED,
                SideEffectStatus.STARTED,
                SideEffectStatus.COMPLETED,
                SideEffectStatus.AMBIGUOUS,
            }
        )
    if resolution.kind is SideEffectResolutionKind.COMPENSATE:
        return record.status in {
            SideEffectStatus.COMPENSATING,
            SideEffectStatus.COMPENSATED,
        }
    return (
        record.status is SideEffectStatus.RESOLVED
        and record.resolution is SideEffectResolutionOutcome.FAILED
    )


__all__ = ["SideEffectResume"]
