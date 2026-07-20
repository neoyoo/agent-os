from __future__ import annotations

from agentos._waiting import WaitReason
from agentos.capabilities.invocation import ToolInvocation
from agentos.capabilities.tools import SideEffectPolicy
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_integrity import wait_reason_digest
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectStatus,
    SideEffectTransitionError,
    WaitingToolCompletion,
)


RunVersionKey = tuple[str | None, str, str]
RunFence = tuple[str | None, int | None]


class ObservedRunGuards:
    def __init__(self) -> None:
        self._versions: dict[RunVersionKey, int] = {}
        self._fences: dict[RunVersionKey, RunFence] = {}

    def observe_invocation(
        self,
        invocation: ToolInvocation,
        guard: RunWriteGuard,
    ) -> None:
        context = invocation.context
        self._observe(
            tenant_id=context.tenant_id,
            session_id=context.session_id,
            run_id=context.run_id,
            guard=guard,
        )

    def observe_record(
        self,
        record: SideEffectRecord,
        guard: RunWriteGuard,
    ) -> None:
        self._observe(
            tenant_id=record.attempt_id.tenant_id,
            session_id=record.attempt_id.session_id,
            run_id=record.run_id,
            guard=guard,
        )

    def _observe(
        self,
        *,
        tenant_id: str | None,
        session_id: str,
        run_id: str,
        guard: RunWriteGuard,
    ) -> None:
        observe_run_guard(
            self._versions,
            self._fences,
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            guard=guard,
        )


def attempt_id(invocation: ToolInvocation) -> SideEffectAttemptId:
    context = invocation.context
    return SideEffectAttemptId(
        context.tenant_id,
        context.session_id,
        context.operation_id,
        context.attempt,
    )


def matches_reservation(
    record: SideEffectRecord,
    invocation: ToolInvocation,
    policy: SideEffectPolicy,
    invocation_digest: str,
    invocation_ref: ProtectedPayloadRef | None,
) -> bool:
    return record.attempt_id == attempt_id(invocation) and matches_operation(
        record,
        invocation,
        policy,
        invocation_digest,
        invocation_ref,
    )


def matches_operation(
    record: SideEffectRecord,
    invocation: ToolInvocation,
    policy: SideEffectPolicy,
    invocation_digest: str,
    invocation_ref: ProtectedPayloadRef | None,
) -> bool:
    context = invocation.context
    return (
        record.run_id == context.run_id
        and record.turn_id == context.turn_id
        and record.invocation_id == context.invocation_id
        and record.tool_name == invocation.tool_name
        and record.policy is policy
        and record.invocation_digest == invocation_digest
        and record.invocation_ref == invocation_ref
    )


def require_guard(guard: RunWriteGuard) -> None:
    if type(guard) is not RunWriteGuard:
        raise TypeError("guard must be RunWriteGuard")


def require_run_version(
    versions: dict[RunVersionKey, int],
    *,
    tenant_id: str | None,
    session_id: str,
    run_id: str,
    guard: RunWriteGuard,
) -> RunVersionKey:
    key = (tenant_id, session_id, run_id)
    current = versions.get(key)
    if current is not None and guard.expected_version < current:
        raise SideEffectTransitionError
    return key


def observe_run_guard(
    versions: dict[RunVersionKey, int],
    fences: dict[RunVersionKey, RunFence],
    *,
    tenant_id: str | None,
    session_id: str,
    run_id: str,
    guard: RunWriteGuard,
) -> None:
    key = require_run_version(
        versions,
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
        guard=guard,
    )
    incoming = (guard.claim_id, guard.fencing_token)
    if key in fences:
        current_claim, current_token = fences[key]
        current_is_local = current_claim is None
        incoming_is_local = guard.claim_id is None
        if current_is_local != incoming_is_local:
            raise SideEffectTransitionError
        if not current_is_local:
            assert current_token is not None
            assert guard.fencing_token is not None
            if guard.fencing_token < current_token or (
                guard.fencing_token == current_token
                and guard.claim_id != current_claim
            ):
                raise SideEffectTransitionError
    versions[key] = guard.expected_version
    fences[key] = incoming


def require_writable_current(
    current: SideEffectRecord,
    guard: RunWriteGuard,
) -> None:
    current_is_local = current.claim_id is None
    incoming_is_local = guard.claim_id is None
    if current_is_local != incoming_is_local:
        raise SideEffectTransitionError
    if current_is_local:
        return
    assert current.fencing_token is not None
    assert guard.fencing_token is not None
    if guard.fencing_token < current.fencing_token or (
        guard.fencing_token == current.fencing_token
        and guard.claim_id != current.claim_id
    ):
        raise SideEffectTransitionError


def require_waiting_completion(
    record: SideEffectRecord,
    completion: WaitingToolCompletion,
    *,
    run_id: str,
    turn_id: str,
    reason: WaitReason,
) -> None:
    if (
        record.status is not SideEffectStatus.STARTED
        or record.policy is not SideEffectPolicy.PURE
        or record.run_id != run_id
        or record.turn_id != turn_id
        or record.invocation_id != completion.invocation_id
        or record.attempt_id.operation_id != completion.operation_id
        or record.attempt_id.attempt != completion.attempt
        or completion.wait_reason_digest != wait_reason_digest(reason)
    ):
        raise SideEffectTransitionError
