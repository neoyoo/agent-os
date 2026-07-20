from __future__ import annotations

from asyncio import Lock
from collections.abc import Awaitable, Callable
from dataclasses import replace

from agentos._waiting import WaitReason
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.result_refs import ToolResultRef
from agentos.capabilities.tools import SideEffectPolicy
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectAttemptId,
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectRecordConflictError,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectResultIntegrityError,
    SideEffectStatus,
    SideEffectTransitionError,
    WaitingToolCompletion,
    result_ref_digest,
)
from agentos.runtime._side_effect_memory_validation import (
    ObservedRunGuards,
    attempt_id as _attempt_id,
    matches_operation as _matches_operation,
    matches_reservation as _matches_reservation,
    require_guard as _require_guard,
    require_waiting_completion as _require_waiting_completion,
    require_writable_current as _require_writable_current,
)
from agentos.runtime.tool_identity import compensation_operation_id


class InMemorySideEffectStore:
    """用于 Local 与 deterministic contract tests 的进程内 Ledger。"""

    def __init__(self) -> None:
        self._records: dict[SideEffectAttemptId, SideEffectRecord] = {}
        self._run_guards = ObservedRunGuards()
        self._lock = Lock()

    async def reserve(
        self,
        *,
        invocation: ToolInvocation,
        policy: SideEffectPolicy,
        invocation_digest: str,
        invocation_ref: ProtectedPayloadRef | None,
        guard: RunWriteGuard,
        supersedes: SideEffectAttemptId | None = None,
    ) -> SideEffectRecord:
        _require_guard(guard)
        attempt_id = _attempt_id(invocation)
        async with self._lock:
            current = self._current(attempt_id)
            self._run_guards.observe_invocation(invocation, guard)
            existing = self._records.get(attempt_id)
            if existing is not None:
                if existing is not current:
                    raise SideEffectTransitionError
                _require_writable_current(existing, guard)
                if _matches_reservation(
                    existing,
                    invocation,
                    policy,
                    invocation_digest,
                    invocation_ref,
                ):
                    return existing
                raise SideEffectRecordConflictError
            if supersedes is None:
                if current is not None or attempt_id.attempt != 1:
                    raise SideEffectTransitionError
            else:
                if current is not None:
                    _require_writable_current(current, guard)
                self._validate_supersede(
                    current,
                    supersedes,
                    invocation,
                    policy,
                    invocation_digest,
                    invocation_ref,
                )
                assert current is not None
                self._store(replace(
                    current,
                    status=SideEffectStatus.RESOLVED,
                    resolution=SideEffectResolutionOutcome.SUPERSEDED,
                    claim_id=guard.claim_id,
                    fencing_token=guard.fencing_token,
                ), guard)
            record = SideEffectRecord(
                attempt_id=attempt_id,
                run_id=invocation.context.run_id,
                turn_id=invocation.context.turn_id,
                invocation_id=invocation.context.invocation_id,
                tool_name=invocation.tool_name,
                policy=policy,
                status=SideEffectStatus.RESERVED,
                invocation_digest=invocation_digest,
                invocation_ref=invocation_ref,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            )
            return self._store(record, guard)

    async def get(
        self,
        *,
        tenant_id: str | None,
        session_id: str,
        operation_id: str,
        attempt: int | None,
        guard: RunWriteGuard,
    ) -> SideEffectRecord | None:
        _require_guard(guard)
        async with self._lock:
            if attempt is not None:
                record = self._records.get(
                    SideEffectAttemptId(
                        tenant_id,
                        session_id,
                        operation_id,
                        attempt,
                    ),
                )
            else:
                record = self._current(
                    SideEffectAttemptId(tenant_id, session_id, operation_id, 1),
                )
            if record is not None:
                self._run_guards.observe_record(record, guard)
            return record

    async def mark_started(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        return await self._transition(
            attempt_id,
            guard,
            expected=SideEffectStatus.RESERVED,
            target=SideEffectStatus.STARTED,
        )

    async def complete(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        completion: SideEffectCompletion,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        _require_guard(guard)
        if type(completion) is not SideEffectCompletion:
            raise TypeError("completion must be SideEffectCompletion")
        if completion.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL:
            raise SideEffectTransitionError
        async with self._lock:
            current = self._require_current(attempt_id, guard)
            if current.status is not SideEffectStatus.STARTED:
                raise SideEffectTransitionError
            if (
                completion.outcome_kind is SideEffectOutcomeKind.HANDLER_ERROR
                and current.policy
                not in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
            ):
                raise SideEffectTransitionError
            if completion.result_ref is not None and (
                result_ref_digest(completion.result_ref) != completion.result_digest
            ):
                raise SideEffectResultIntegrityError
            updated = replace(
                current,
                status=SideEffectStatus.COMPLETED,
                result_ref=completion.result_ref,
                result_digest=completion.result_digest,
                outcome_kind=completion.outcome_kind,
                failure_code=completion.failure_code,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            )
            return self._store(updated, guard)

    async def commit_wait_control(
        self,
        *,
        completion: WaitingToolCompletion,
        session_id: str,
        run_id: str,
        turn_id: str,
        reason: WaitReason,
        guard: RunWriteGuard,
        commit_run: Callable[[], Awaitable[object]],
    ) -> object:
        """在单一事件循环临界区提交 Local Ledger 与 Run WAITING。"""

        _require_guard(guard)
        if type(completion) is not WaitingToolCompletion:
            raise TypeError("completion must be WaitingToolCompletion")
        attempt_id = SideEffectAttemptId(
            None,
            session_id,
            completion.operation_id,
            completion.attempt,
        )
        async with self._lock:
            current = self._require_current(attempt_id, guard)
            _require_waiting_completion(
                current,
                completion,
                run_id=run_id,
                turn_id=turn_id,
                reason=reason,
            )
            self._store(replace(
                current,
                status=SideEffectStatus.COMPLETED,
                outcome_kind=SideEffectOutcomeKind.WAIT_CONTROL,
                wait_reason_digest=completion.wait_reason_digest,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            ), guard)
            try:
                return await commit_run()
            except BaseException:
                self._store(current, guard)
                raise

    async def mark_ambiguous(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        _require_guard(guard)
        async with self._lock:
            current = self._require_current(attempt_id, guard)
            if current.status is SideEffectStatus.AMBIGUOUS:
                return current
            if (
                current.status is not SideEffectStatus.STARTED
                or current.policy in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
            ):
                raise SideEffectTransitionError
            updated = replace(
                current,
                status=SideEffectStatus.AMBIGUOUS,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            )
            return self._store(updated, guard)

    async def begin_compensation(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        _require_guard(guard)
        async with self._lock:
            current = self._require_current(attempt_id, guard)
            if current.status is not SideEffectStatus.COMPENSATING:
                raise SideEffectTransitionError
            updated = replace(
                current,
                compensation_attempt=(current.compensation_attempt or 0) + 1,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            )
            return self._store(updated, guard)

    async def complete_compensation(
        self,
        *,
        attempt_id: CompensationAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        _require_guard(guard)
        if type(attempt_id) is not CompensationAttemptId:
            raise TypeError("attempt_id must be CompensationAttemptId")
        async with self._lock:
            current = self._require_current(attempt_id.side_effect_attempt, guard)
            if (
                current.status is not SideEffectStatus.COMPENSATING
                or current.compensation_operation_id
                != attempt_id.compensation_operation_id
                or current.compensation_attempt != attempt_id.compensation_attempt
            ):
                raise SideEffectTransitionError
            updated = replace(
                current,
                status=SideEffectStatus.COMPENSATED,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            )
            return self._store(updated, guard)

    async def resolve(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        resolution: SideEffectResolution,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        _require_guard(guard)
        if type(resolution) is not SideEffectResolution:
            raise TypeError("resolution must be SideEffectResolution")
        async with self._lock:
            current = self._require_current(attempt_id, guard)
            if (
                current.status is not SideEffectStatus.AMBIGUOUS
                or current.attempt_id.operation_id != resolution.operation_id
            ):
                raise SideEffectTransitionError
            if resolution.kind is SideEffectResolutionKind.ACCEPT_RESULT:
                return self._resolve_record(
                    current,
                    guard,
                    outcome=SideEffectResolutionOutcome.ACCEPTED,
                    result_ref=resolution.result_ref,
                    result_digest=resolution.result_digest,
                )
            if resolution.kind is SideEffectResolutionKind.FAIL:
                return self._resolve_record(
                    current,
                    guard,
                    outcome=SideEffectResolutionOutcome.FAILED,
                )
            if resolution.kind is SideEffectResolutionKind.COMPENSATE:
                if current.policy is not SideEffectPolicy.COMPENSATABLE:
                    raise SideEffectTransitionError
                updated = replace(
                    current,
                    status=SideEffectStatus.COMPENSATING,
                    compensation_operation_id=compensation_operation_id(
                        current.attempt_id.operation_id,
                    ),
                    compensation_attempt=1,
                    claim_id=guard.claim_id,
                    fencing_token=guard.fencing_token,
                )
                return self._store(updated, guard)
            return self._resolve_retry(current, resolution, guard)

    async def _transition(
        self,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
        *,
        expected: SideEffectStatus,
        target: SideEffectStatus,
    ) -> SideEffectRecord:
        _require_guard(guard)
        async with self._lock:
            current = self._require_current(attempt_id, guard)
            if current.status is target:
                return current
            if current.status is not expected:
                raise SideEffectTransitionError
            updated = replace(
                current,
                status=target,
                claim_id=guard.claim_id,
                fencing_token=guard.fencing_token,
            )
            return self._store(updated, guard)

    def _resolve_record(
        self,
        current: SideEffectRecord,
        guard: RunWriteGuard,
        *,
        outcome: SideEffectResolutionOutcome,
        result_ref: ToolResultRef | None = None,
        result_digest: str | None = None,
    ) -> SideEffectRecord:
        if result_ref is not None and result_ref_digest(result_ref) != result_digest:
            raise SideEffectResultIntegrityError
        updated = replace(
            current,
            status=SideEffectStatus.RESOLVED,
            resolution=outcome,
            result_ref=result_ref,
            result_digest=result_digest,
            claim_id=guard.claim_id,
            fencing_token=guard.fencing_token,
        )
        return self._store(updated, guard)

    def _resolve_retry(
        self,
        current: SideEffectRecord,
        resolution: SideEffectResolution,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        if (
            resolution.attestation_ref is None
            or resolution.attestation_ref.digest != resolution.attestation_digest
        ):
            raise SideEffectTransitionError
        resolved = replace(
            current,
            status=SideEffectStatus.RESOLVED,
            resolution=SideEffectResolutionOutcome.RETRY_SAFE,
            attestation_ref=resolution.attestation_ref,
            attestation_digest=resolution.attestation_digest,
            claim_id=guard.claim_id,
            fencing_token=guard.fencing_token,
        )
        next_id = replace(current.attempt_id, attempt=current.attempt_id.attempt + 1)
        reserved = replace(
            current,
            attempt_id=next_id,
            status=SideEffectStatus.RESERVED,
            claim_id=guard.claim_id,
            fencing_token=guard.fencing_token,
        )
        self._store(resolved, guard)
        return self._store(reserved, guard)

    def _validate_supersede(
        self,
        current: SideEffectRecord | None,
        supersedes: SideEffectAttemptId,
        invocation: ToolInvocation,
        policy: SideEffectPolicy,
        invocation_digest: str,
        invocation_ref: ProtectedPayloadRef | None,
    ) -> None:
        if (
            current is None
            or current.attempt_id != supersedes
            or current.status is not SideEffectStatus.STARTED
            or current.policy not in {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}
            or invocation.context.attempt != current.attempt_id.attempt + 1
            or not _matches_operation(
                current,
                invocation,
                policy,
                invocation_digest,
                invocation_ref,
            )
        ):
            raise SideEffectTransitionError

    def _require_current(
        self,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        current = self._current(attempt_id)
        if current is None or current.attempt_id != attempt_id:
            raise SideEffectTransitionError
        _require_writable_current(current, guard)
        self._run_guards.observe_record(current, guard)
        return current

    def _store(
        self,
        record: SideEffectRecord,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        self._run_guards.observe_record(record, guard)
        self._records[record.attempt_id] = record
        return record

    def _current(self, attempt_id: SideEffectAttemptId) -> SideEffectRecord | None:
        matches = (
            record
            for candidate, record in self._records.items()
            if candidate.tenant_id == attempt_id.tenant_id
            and candidate.session_id == attempt_id.session_id
            and candidate.operation_id == attempt_id.operation_id
        )
        return max(matches, key=lambda item: item.attempt_id.attempt, default=None)

__all__ = ["InMemorySideEffectStore"]
