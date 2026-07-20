import asyncio
from dataclasses import replace

import pytest

from agentos._waiting import WaitReason
from agentos.capabilities import (
    InlineToolResultRef,
    SideEffectPolicy,
    ToolInvocation,
    ToolInvocationContext,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.side_effect_cancel import plan_side_effect_cancel
from agentos.runtime.side_effect_integrity import wait_reason_digest
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectCompletion,
    SideEffectInFlightError,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectRecordConflictError,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
    SideEffectTransitionError,
    WaitingToolCompletion,
    result_ref_digest,
)
from agentos.runtime.tool_identity import compensation_operation_id
from tests.planning._async import async_test


_INVOCATION_DIGEST = f"sha256:{'1' * 64}"
_ATTESTATION_DIGEST = f"sha256:{'2' * 64}"
_INVOCATION_ID = "invocation_ea91f27fdf596bceb7c90d2578c5e988"
_OPERATION_ID = "operation_d340f3861e0c6a7eefbaf707fdc69d3d"


def _invocation(*, attempt: int = 1) -> ToolInvocation:
    return ToolInvocation(
        tool_name="charge",
        arguments={"amount": 50, "currency": "CNY"},
        context=ToolInvocationContext(
            invocation_id=_INVOCATION_ID,
            operation_id=_OPERATION_ID,
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            tool_call_id="call_1",
            attempt=attempt,
        ),
    )


def _ref(content: str = "ok") -> InlineToolResultRef:
    return InlineToolResultRef(content)


def _provider_completion(content: str = "ok") -> SideEffectCompletion:
    result_ref = _ref(content)
    return SideEffectCompletion(
        outcome_kind=SideEffectOutcomeKind.PROVIDER_RESULT,
        result_ref=result_ref,
        result_digest=result_ref_digest(result_ref),
    )


def test_inline_result_ref_digest_v1_golden_vector() -> None:
    assert result_ref_digest(_ref()) == (
        "sha256:2e05c7a332b40b27729047b6b2bcf8a0d7765c2ee2c34a657ce4b4c2c43cd3f1"
    )


@async_test
async def test_ledger_reserve_start_complete_and_reuse_current_result() -> None:
    store = InMemorySideEffectStore()
    invocation = _invocation()
    guard = RunWriteGuard(expected_version=7)

    reserved = await store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.NON_RETRYABLE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    repeated = await store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.NON_RETRYABLE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    started = await store.mark_started(
        attempt_id=reserved.attempt_id,
        guard=guard,
    )
    completed = await store.complete(
        attempt_id=started.attempt_id,
        completion=_provider_completion(),
        guard=guard,
    )
    current = await store.get(
        tenant_id="tenant_1",
        session_id="session_1",
        operation_id=_OPERATION_ID,
        attempt=None,
        guard=guard,
    )

    assert repeated == reserved
    assert completed.status is SideEffectStatus.COMPLETED
    assert completed.outcome_kind is SideEffectOutcomeKind.PROVIDER_RESULT
    assert completed.result_ref == _ref()
    assert current == completed
    assert guard.expected_version == 7


@async_test
async def test_reserve_rejects_same_operation_with_changed_digest() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=1)
    await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.PURE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )

    with pytest.raises(SideEffectRecordConflictError):
        await store.reserve(
            invocation=_invocation(),
            policy=SideEffectPolicy.PURE,
            invocation_digest=f"sha256:{'3' * 64}",
            invocation_ref=None,
            guard=guard,
        )


@async_test
async def test_safe_retry_atomically_supersedes_started_attempt() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=2)
    first = await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.IDEMPOTENT,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    first = await store.mark_started(attempt_id=first.attempt_id, guard=guard)

    second = await store.reserve(
        invocation=_invocation(attempt=2),
        policy=SideEffectPolicy.IDEMPOTENT,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
        supersedes=first.attempt_id,
    )
    old = await store.get(
        tenant_id="tenant_1",
        session_id="session_1",
        operation_id=_OPERATION_ID,
        attempt=1,
        guard=guard,
    )

    assert second.status is SideEffectStatus.RESERVED
    assert second.attempt_id.attempt == 2
    assert old is not None
    assert old.status is SideEffectStatus.RESOLVED
    assert old.resolution is SideEffectResolutionOutcome.SUPERSEDED

    with pytest.raises(SideEffectTransitionError):
        await store.reserve(
            invocation=_invocation(),
            policy=SideEffectPolicy.IDEMPOTENT,
            invocation_digest=_INVOCATION_DIGEST,
            invocation_ref=None,
            guard=guard,
        )


@async_test
async def test_non_retryable_handler_error_becomes_ambiguous() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=3)
    record = await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.NON_RETRYABLE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    record = await store.mark_started(attempt_id=record.attempt_id, guard=guard)

    with pytest.raises(SideEffectTransitionError):
        await store.complete(
            attempt_id=record.attempt_id,
            completion=SideEffectCompletion(
                SideEffectOutcomeKind.HANDLER_ERROR,
                failure_code="tool_execution_failed",
            ),
            guard=guard,
        )

    ambiguous = await store.mark_ambiguous(
        attempt_id=record.attempt_id,
        guard=guard,
    )
    assert ambiguous.status is SideEffectStatus.AMBIGUOUS


@async_test
async def test_regular_complete_rejects_wait_control_outcome() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=3)
    record = await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.PURE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    record = await store.mark_started(attempt_id=record.attempt_id, guard=guard)

    with pytest.raises(SideEffectTransitionError):
        await store.complete(
            attempt_id=record.attempt_id,
            completion=SideEffectCompletion(SideEffectOutcomeKind.WAIT_CONTROL),
            guard=guard,
        )


@async_test
async def test_retry_resolution_closes_old_attempt_and_returns_new_reserved_attempt() -> None:
    store, ambiguous, guard = await _ambiguous_store(
        SideEffectPolicy.NON_RETRYABLE,
    )
    attestation = ProtectedPayloadRef("sealed", _ATTESTATION_DIGEST)

    retried = await store.resolve(
        attempt_id=ambiguous.attempt_id,
        resolution=SideEffectResolution(
            operation_id=_OPERATION_ID,
            kind=SideEffectResolutionKind.RETRY_PROVEN_SAFE,
            attestation_ref=attestation,
            attestation_digest=_ATTESTATION_DIGEST,
        ),
        guard=guard,
    )
    old = await store.get(
        tenant_id="tenant_1",
        session_id="session_1",
        operation_id=_OPERATION_ID,
        attempt=1,
        guard=guard,
    )

    assert retried.status is SideEffectStatus.RESERVED
    assert retried.attempt_id.attempt == 2
    assert old is not None
    assert old.resolution is SideEffectResolutionOutcome.RETRY_SAFE
    assert old.attestation_ref == attestation


@async_test
async def test_compensation_rejects_stale_attempt_completion() -> None:
    store, ambiguous, guard = await _ambiguous_store(
        SideEffectPolicy.COMPENSATABLE,
    )
    first = await store.resolve(
        attempt_id=ambiguous.attempt_id,
        resolution=SideEffectResolution(
            operation_id=_OPERATION_ID,
            kind=SideEffectResolutionKind.COMPENSATE,
        ),
        guard=guard,
    )
    stale_id = CompensationAttemptId(
        side_effect_attempt=first.attempt_id,
        compensation_operation_id=first.compensation_operation_id or "",
        compensation_attempt=1,
    )
    second = await store.begin_compensation(
        attempt_id=first.attempt_id,
        guard=guard,
    )

    with pytest.raises(SideEffectTransitionError):
        await store.complete_compensation(attempt_id=stale_id, guard=guard)

    current_id = CompensationAttemptId(
        side_effect_attempt=second.attempt_id,
        compensation_operation_id=second.compensation_operation_id or "",
        compensation_attempt=2,
    )
    completed = await store.complete_compensation(
        attempt_id=current_id,
        guard=guard,
    )
    assert completed.status is SideEffectStatus.COMPENSATED


@async_test
async def test_cancel_plan_allows_reserved_and_terminal_but_rejects_in_flight() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=4)
    reserved = await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.PURE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    plan = plan_side_effect_cancel((reserved,))
    assert plan.cancel_before_start == (reserved.attempt_id,)

    started = await store.mark_started(attempt_id=reserved.attempt_id, guard=guard)
    with pytest.raises(SideEffectInFlightError):
        plan_side_effect_cancel((started,))

    completed = replace(
        started,
        status=SideEffectStatus.COMPLETED,
        outcome_kind=SideEffectOutcomeKind.PROVIDER_RESULT,
        result_ref=_ref(),
        result_digest=result_ref_digest(_ref()),
    )
    assert plan_side_effect_cancel((completed,)).cancel_before_start == ()


def test_cancel_plan_covers_all_safe_stop_states() -> None:
    reserved = _record(SideEffectPolicy.PURE)
    provider = _ref()
    completed = replace(
        reserved,
        status=SideEffectStatus.COMPLETED,
        outcome_kind=SideEffectOutcomeKind.PROVIDER_RESULT,
        result_ref=provider,
        result_digest=result_ref_digest(provider),
    )
    resolved = replace(
        reserved,
        status=SideEffectStatus.RESOLVED,
        resolution=SideEffectResolutionOutcome.FAILED,
    )
    compensating = replace(
        _record(SideEffectPolicy.COMPENSATABLE),
        status=SideEffectStatus.COMPENSATING,
        compensation_operation_id=compensation_operation_id(_OPERATION_ID),
        compensation_attempt=1,
    )
    compensated = replace(compensating, status=SideEffectStatus.COMPENSATED)

    assert plan_side_effect_cancel(()).cancel_before_start == ()
    assert plan_side_effect_cancel((reserved,)).cancel_before_start == (
        reserved.attempt_id,
    )
    for terminal in (completed, resolved, compensated):
        assert plan_side_effect_cancel((terminal,)).cancel_before_start == ()
    for in_flight in (
        replace(reserved, status=SideEffectStatus.STARTED),
        replace(reserved, status=SideEffectStatus.AMBIGUOUS),
        compensating,
    ):
        with pytest.raises(SideEffectInFlightError):
            plan_side_effect_cancel((in_flight,))


def test_side_effect_record_rejects_superseded_unsafe_policy() -> None:
    with pytest.raises(ValueError, match="superseded"):
        replace(
            _record(SideEffectPolicy.NON_RETRYABLE),
            status=SideEffectStatus.RESOLVED,
            resolution=SideEffectResolutionOutcome.SUPERSEDED,
        )


def test_side_effect_record_rejects_result_digest_mismatch() -> None:
    with pytest.raises(ValueError, match="result_digest"):
        replace(
            _record(SideEffectPolicy.PURE),
            status=SideEffectStatus.COMPLETED,
            outcome_kind=SideEffectOutcomeKind.PROVIDER_RESULT,
            result_ref=_ref(),
            result_digest=f"sha256:{'0' * 64}",
        )


def test_side_effect_record_rejects_unsafe_wait_control() -> None:
    with pytest.raises(ValueError, match="wait control"):
        replace(
            _record(SideEffectPolicy.NON_RETRYABLE),
            status=SideEffectStatus.COMPLETED,
            outcome_kind=SideEffectOutcomeKind.WAIT_CONTROL,
            wait_reason_digest=f"sha256:{'0' * 64}",
        )


def test_side_effect_record_rejects_forged_compensation_identity() -> None:
    with pytest.raises(ValueError, match="compensation_operation_id"):
        replace(
            _record(SideEffectPolicy.COMPENSATABLE),
            status=SideEffectStatus.COMPENSATING,
            compensation_operation_id="operation_00000000000000000000000000000000",
            compensation_attempt=1,
        )


def test_retry_resolution_rejects_attestation_digest_mismatch() -> None:
    with pytest.raises(ValueError, match="attestation_digest"):
        SideEffectResolution(
            operation_id=_OPERATION_ID,
            kind=SideEffectResolutionKind.RETRY_PROVEN_SAFE,
            attestation_ref=ProtectedPayloadRef("sealed", _ATTESTATION_DIGEST),
            attestation_digest=f"sha256:{'3' * 64}",
        )


@async_test
async def test_stale_fence_cannot_mutate_current_record() -> None:
    store = InMemorySideEffectStore()
    current_guard = RunWriteGuard(1, "claim_2", 2)
    stale_guard = RunWriteGuard(1, "claim_1", 1)
    record = await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.NON_RETRYABLE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=current_guard,
    )
    record = await store.mark_started(
        attempt_id=record.attempt_id,
        guard=current_guard,
    )

    with pytest.raises(SideEffectTransitionError):
        await store.mark_ambiguous(
            attempt_id=record.attempt_id,
            guard=stale_guard,
        )

    unchanged = await store.get(
        tenant_id="tenant_1",
        session_id="session_1",
        operation_id=_OPERATION_ID,
        attempt=None,
        guard=current_guard,
    )
    assert unchanged == record


@async_test
async def test_stale_fence_cannot_replay_completed_record() -> None:
    store = InMemorySideEffectStore()
    current_guard = RunWriteGuard(1, "claim_2", 2)
    next_guard = RunWriteGuard(1, "claim_3", 3)
    stale_guard = RunWriteGuard(1, "claim_1", 1)
    invocation = _invocation()
    record = await store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.NON_RETRYABLE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=current_guard,
    )
    record = await store.mark_started(
        attempt_id=record.attempt_id,
        guard=current_guard,
    )
    await store.complete(
        attempt_id=record.attempt_id,
        completion=_provider_completion(),
        guard=current_guard,
    )

    with pytest.raises(SideEffectTransitionError):
        await store.reserve(
            invocation=invocation,
            policy=SideEffectPolicy.NON_RETRYABLE,
            invocation_digest=_INVOCATION_DIGEST,
            invocation_ref=None,
            guard=stale_guard,
        )

    replayed = await store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.NON_RETRYABLE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=next_guard,
    )
    assert replayed.status is SideEffectStatus.COMPLETED

    with pytest.raises(SideEffectTransitionError):
        await store.reserve(
            invocation=invocation,
            policy=SideEffectPolicy.NON_RETRYABLE,
            invocation_digest=_INVOCATION_DIGEST,
            invocation_ref=None,
            guard=current_guard,
        )


@async_test
async def test_stale_run_version_cannot_mutate_current_record() -> None:
    store = InMemorySideEffectStore()
    current_guard = RunWriteGuard(expected_version=5)
    reserved = await store.reserve(
        invocation=_invocation(),
        policy=SideEffectPolicy.PURE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=current_guard,
    )

    with pytest.raises(SideEffectTransitionError):
        await store.mark_started(
            attempt_id=reserved.attempt_id,
            guard=RunWriteGuard(expected_version=4),
        )


@async_test
async def test_wait_control_is_not_visible_before_run_commit_finishes() -> None:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=5)
    original = _invocation()
    invocation = ToolInvocation(
        original.tool_name,
        original.arguments,
        replace(original.context, tenant_id=None),
    )
    record = await store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.PURE,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    record = await store.mark_started(attempt_id=record.attempt_id, guard=guard)
    reason = WaitReason("human_input", "approval_1")
    commit_entered = asyncio.Event()
    release_commit = asyncio.Event()

    async def commit_run() -> str:
        commit_entered.set()
        await release_commit.wait()
        return "committed"

    commit = asyncio.create_task(store.commit_wait_control(
        completion=WaitingToolCompletion(
            invocation.context.invocation_id,
            invocation.context.operation_id,
            1,
            wait_reason_digest(reason),
        ),
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        reason=reason,
        guard=guard,
        commit_run=commit_run,
    ))
    await commit_entered.wait()
    read = asyncio.create_task(store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=_OPERATION_ID,
        attempt=None,
        guard=guard,
    ))
    await asyncio.sleep(0)

    visible_before_commit = read.done()
    release_commit.set()
    assert await commit == "committed"
    visible = await read
    assert not visible_before_commit
    assert visible is not None
    assert visible.status is SideEffectStatus.COMPLETED


@pytest.mark.parametrize(
    "digest",
    ["sha256:short", f"sha256:{'A' * 64}", f"sha1:{'1' * 64}"],
)
def test_side_effect_record_rejects_noncanonical_digest(digest: str) -> None:
    with pytest.raises(ValueError, match="invocation_digest"):
        replace(_record(SideEffectPolicy.PURE), invocation_digest=digest)


async def _ambiguous_store(
    policy: SideEffectPolicy,
) -> tuple[InMemorySideEffectStore, object, RunWriteGuard]:
    store = InMemorySideEffectStore()
    guard = RunWriteGuard(expected_version=5)
    record = await store.reserve(
        invocation=_invocation(),
        policy=policy,
        invocation_digest=_INVOCATION_DIGEST,
        invocation_ref=None,
        guard=guard,
    )
    record = await store.mark_started(attempt_id=record.attempt_id, guard=guard)
    record = await store.mark_ambiguous(attempt_id=record.attempt_id, guard=guard)
    return store, record, guard


def _record(policy: SideEffectPolicy) -> SideEffectRecord:
    from agentos.runtime.side_effect_types import SideEffectAttemptId

    return SideEffectRecord(
        attempt_id=SideEffectAttemptId("tenant_1", "session_1", _OPERATION_ID, 1),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id=_INVOCATION_ID,
        tool_name="charge",
        policy=policy,
        status=SideEffectStatus.RESERVED,
        invocation_digest=_INVOCATION_DIGEST,
    )
