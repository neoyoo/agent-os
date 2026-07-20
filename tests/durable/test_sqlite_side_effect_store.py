from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from agentos.capabilities import (
    InlineToolResultRef,
    SideEffectPolicy,
    ToolInvocation,
)
from agentos.durable import SQLiteDurableStore
from agentos.providers import ProviderToolCall
from agentos.runtime.errors import CheckpointCorruptedError
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.run_runtime import RunRuntime, RunWriteGuard
from agentos.runtime.side_effect_integrity import result_ref_digest
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectCompletion,
    SideEffectOutcomeKind,
    SideEffectRecord,
    SideEffectRecordConflictError,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
    SideEffectTransitionError,
)
from agentos.runtime.tool_identity import (
    compensation_operation_id,
    invocation_digest,
)
from agentos.runtime.tool_invocations import build_tool_invocation_plan
from tests.durable._async_support import create_running, run
from tests.durable._fixtures import NOW, checkpoint_source, database_path


_INVOCATION_REF = ProtectedPayloadRef(
    "sealed-invocation",
    f"sha256:{'1' * 64}",
)
_ATTESTATION_DIGEST = f"sha256:{'2' * 64}"
_ATTESTATION_REF = ProtectedPayloadRef(
    "sealed-attestation",
    _ATTESTATION_DIGEST,
)


@dataclass(frozen=True, slots=True)
class _LedgerCase:
    store: SQLiteDurableStore
    runs: RunRuntime
    guard: RunWriteGuard


@pytest.fixture
def ledger_case(tmp_path) -> Iterator[_LedgerCase]:
    store = run(SQLiteDurableStore.open(database_path(tmp_path), clock=lambda: NOW))
    source = checkpoint_source()
    run(store.initialize_session(source.session))
    runs = RunRuntime(session_id="session_1", store=store)
    running = run(create_running(runs, "run_1"))
    try:
        yield _LedgerCase(
            store,
            runs,
            RunWriteGuard(running.aggregate_version),
        )
    finally:
        run(store.close())


def _invocation(
    *,
    attempt: int = 1,
    tenant_id: str | None = None,
) -> ToolInvocation:
    invocation = build_tool_invocation_plan(
        tenant_id=tenant_id,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        provider_call_index=0,
        assistant_message_id="message_1",
        calls=(ProviderToolCall("call_1", "charge", {"amount": 50}),),
    ).entries[0].invocation
    return invocation if attempt == 1 else invocation.with_attempt(attempt)


def _reserve(
    case: _LedgerCase,
    policy: SideEffectPolicy,
    *,
    invocation: ToolInvocation | None = None,
) -> tuple[ToolInvocation, SideEffectRecord]:
    invocation = invocation or _invocation()
    record = run(case.store.side_effect_store.reserve(
        invocation=invocation,
        policy=policy,
        invocation_digest=invocation_digest(invocation),
        invocation_ref=_INVOCATION_REF,
        guard=case.guard,
    ))
    return invocation, record


def _started(
    case: _LedgerCase,
    policy: SideEffectPolicy,
) -> tuple[ToolInvocation, SideEffectRecord]:
    invocation, reserved = _reserve(case, policy)
    return invocation, run(case.store.side_effect_store.mark_started(
        attempt_id=reserved.attempt_id,
        guard=case.guard,
    ))


def _ambiguous(
    case: _LedgerCase,
    policy: SideEffectPolicy,
) -> tuple[ToolInvocation, SideEffectRecord]:
    invocation, started = _started(case, policy)
    return invocation, run(case.store.side_effect_store.mark_ambiguous(
        attempt_id=started.attempt_id,
        guard=case.guard,
    ))


def _get(
    case: _LedgerCase,
    operation_id: str,
    *,
    attempt: int | None = None,
) -> SideEffectRecord | None:
    return run(case.store.side_effect_store.get(
        tenant_id=None,
        session_id="session_1",
        operation_id=operation_id,
        attempt=attempt,
        guard=case.guard,
    ))


async def _row_count(store: SQLiteDurableStore) -> int:
    connection = store._connection
    assert connection is not None
    async with connection.execute(
        "SELECT COUNT(*) AS count FROM durable_side_effects",
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    return row["count"]


async def _payload_json(
    store: SQLiteDurableStore,
    operation_id: str,
) -> str:
    connection = store._connection
    assert connection is not None
    async with connection.execute(
        "SELECT payload_json FROM durable_side_effects "
        "WHERE session_id = ? AND operation_id = ? AND attempt = 1",
        ("session_1", operation_id),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    return row["payload_json"]


async def _replace_payload_json(
    store: SQLiteDurableStore,
    operation_id: str,
    payload_json: str,
) -> None:
    connection = store._connection
    assert connection is not None
    await connection.execute(
        "UPDATE durable_side_effects SET payload_json = ? "
        "WHERE session_id = ? AND operation_id = ? AND attempt = 1",
        (payload_json, "session_1", operation_id),
    )


def test_reserve_is_idempotent_and_rejects_identity_conflict(
    ledger_case: _LedgerCase,
) -> None:
    invocation, first = _reserve(ledger_case, SideEffectPolicy.PURE)
    repeated = run(ledger_case.store.side_effect_store.reserve(
        invocation=invocation,
        policy=SideEffectPolicy.PURE,
        invocation_digest=invocation_digest(invocation),
        invocation_ref=_INVOCATION_REF,
        guard=ledger_case.guard,
    ))

    with pytest.raises(SideEffectRecordConflictError):
        run(ledger_case.store.side_effect_store.reserve(
            invocation=invocation,
            policy=SideEffectPolicy.PURE,
            invocation_digest=f"sha256:{'3' * 64}",
            invocation_ref=_INVOCATION_REF,
            guard=ledger_case.guard,
        ))

    assert repeated == first
    assert _get(ledger_case, invocation.context.operation_id) == first
    assert run(_row_count(ledger_case.store)) == 1


def test_started_attempt_completes_with_replayable_provider_result(
    ledger_case: _LedgerCase,
) -> None:
    invocation, started = _started(ledger_case, SideEffectPolicy.PURE)
    reference = InlineToolResultRef("charged")

    completed = run(ledger_case.store.side_effect_store.complete(
        attempt_id=started.attempt_id,
        completion=SideEffectCompletion(
            SideEffectOutcomeKind.PROVIDER_RESULT,
            result_ref=reference,
            result_digest=result_ref_digest(reference),
        ),
        guard=ledger_case.guard,
    ))

    assert completed.status is SideEffectStatus.COMPLETED
    assert completed.outcome_kind is SideEffectOutcomeKind.PROVIDER_RESULT
    assert completed.result_ref == reference
    assert _get(ledger_case, invocation.context.operation_id) == completed
    assert run(ledger_case.runs.get_run("run_1")).aggregate_version == (
        ledger_case.guard.expected_version
    )


@pytest.mark.parametrize(
    "policy",
    [SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT],
)
def test_safe_started_attempt_is_atomically_superseded(
    ledger_case: _LedgerCase,
    policy: SideEffectPolicy,
) -> None:
    invocation, started = _started(ledger_case, policy)
    retry = invocation.with_attempt(2)

    current = run(ledger_case.store.side_effect_store.reserve(
        invocation=retry,
        policy=policy,
        invocation_digest=invocation_digest(retry),
        invocation_ref=_INVOCATION_REF,
        guard=ledger_case.guard,
        supersedes=started.attempt_id,
    ))
    old = _get(
        ledger_case,
        invocation.context.operation_id,
        attempt=1,
    )

    assert old is not None
    assert old.status is SideEffectStatus.RESOLVED
    assert old.resolution is SideEffectResolutionOutcome.SUPERSEDED
    assert current.status is SideEffectStatus.RESERVED
    assert current.attempt_id.attempt == 2
    assert _get(ledger_case, invocation.context.operation_id) == current


def test_supersede_rolls_back_old_attempt_when_new_insert_fails(
    ledger_case: _LedgerCase,
) -> None:
    invocation, started = _started(ledger_case, SideEffectPolicy.PURE)
    connection = ledger_case.store._connection
    assert connection is not None
    run(connection.execute(
        "CREATE TRIGGER fail_second_attempt BEFORE INSERT ON durable_side_effects "
        "WHEN NEW.attempt = 2 "
        "BEGIN SELECT RAISE(ABORT, 'injected attempt failure'); END",
    ))

    with pytest.raises(sqlite3.IntegrityError, match="injected attempt failure"):
        run(ledger_case.store.side_effect_store.reserve(
            invocation=invocation.with_attempt(2),
            policy=SideEffectPolicy.PURE,
            invocation_digest=invocation_digest(invocation.with_attempt(2)),
            invocation_ref=_INVOCATION_REF,
            guard=ledger_case.guard,
            supersedes=started.attempt_id,
        ))

    assert _get(ledger_case, invocation.context.operation_id) == started
    assert run(_row_count(ledger_case.store)) == 1


@pytest.mark.parametrize(
    "policy",
    [
        SideEffectPolicy.DEDUPLICATED,
        SideEffectPolicy.COMPENSATABLE,
        SideEffectPolicy.NON_RETRYABLE,
    ],
)
def test_unsafe_started_attempt_becomes_ambiguous(
    ledger_case: _LedgerCase,
    policy: SideEffectPolicy,
) -> None:
    invocation, started = _started(ledger_case, policy)

    ambiguous = run(ledger_case.store.side_effect_store.mark_ambiguous(
        attempt_id=started.attempt_id,
        guard=ledger_case.guard,
    ))

    assert ambiguous.status is SideEffectStatus.AMBIGUOUS
    assert _get(ledger_case, invocation.context.operation_id) == ambiguous


def test_accept_result_resolution_persists_result_evidence(
    ledger_case: _LedgerCase,
) -> None:
    invocation, ambiguous = _ambiguous(
        ledger_case,
        SideEffectPolicy.DEDUPLICATED,
    )
    reference = InlineToolResultRef("accepted")

    resolved = run(ledger_case.store.side_effect_store.resolve(
        attempt_id=ambiguous.attempt_id,
        resolution=SideEffectResolution(
            invocation.context.operation_id,
            SideEffectResolutionKind.ACCEPT_RESULT,
            result_ref=reference,
            result_digest=result_ref_digest(reference),
        ),
        guard=ledger_case.guard,
    ))

    assert resolved.status is SideEffectStatus.RESOLVED
    assert resolved.resolution is SideEffectResolutionOutcome.ACCEPTED
    assert resolved.result_ref == reference
    assert _get(ledger_case, invocation.context.operation_id) == resolved


def test_fail_resolution_persists_terminal_decision(
    ledger_case: _LedgerCase,
) -> None:
    invocation, ambiguous = _ambiguous(
        ledger_case,
        SideEffectPolicy.NON_RETRYABLE,
    )

    resolved = run(ledger_case.store.side_effect_store.resolve(
        attempt_id=ambiguous.attempt_id,
        resolution=SideEffectResolution(
            invocation.context.operation_id,
            SideEffectResolutionKind.FAIL,
        ),
        guard=ledger_case.guard,
    ))

    assert resolved.status is SideEffectStatus.RESOLVED
    assert resolved.resolution is SideEffectResolutionOutcome.FAILED
    assert _get(ledger_case, invocation.context.operation_id) == resolved


def test_retry_safe_resolution_closes_old_and_creates_next_attempt(
    ledger_case: _LedgerCase,
) -> None:
    invocation, ambiguous = _ambiguous(
        ledger_case,
        SideEffectPolicy.NON_RETRYABLE,
    )

    current = run(ledger_case.store.side_effect_store.resolve(
        attempt_id=ambiguous.attempt_id,
        resolution=SideEffectResolution(
            invocation.context.operation_id,
            SideEffectResolutionKind.RETRY_PROVEN_SAFE,
            attestation_ref=_ATTESTATION_REF,
            attestation_digest=_ATTESTATION_DIGEST,
        ),
        guard=ledger_case.guard,
    ))
    old = _get(
        ledger_case,
        invocation.context.operation_id,
        attempt=1,
    )

    assert old is not None
    assert old.status is SideEffectStatus.RESOLVED
    assert old.resolution is SideEffectResolutionOutcome.RETRY_SAFE
    assert old.attestation_ref == _ATTESTATION_REF
    assert current.status is SideEffectStatus.RESERVED
    assert current.attempt_id.attempt == 2


def test_compensation_rejects_stale_completion_attempt(
    ledger_case: _LedgerCase,
) -> None:
    invocation, ambiguous = _ambiguous(
        ledger_case,
        SideEffectPolicy.COMPENSATABLE,
    )
    first = run(ledger_case.store.side_effect_store.resolve(
        attempt_id=ambiguous.attempt_id,
        resolution=SideEffectResolution(
            invocation.context.operation_id,
            SideEffectResolutionKind.COMPENSATE,
        ),
        guard=ledger_case.guard,
    ))
    operation_id = compensation_operation_id(invocation.context.operation_id)
    stale = CompensationAttemptId(first.attempt_id, operation_id, 1)

    second = run(ledger_case.store.side_effect_store.begin_compensation(
        attempt_id=first.attempt_id,
        guard=ledger_case.guard,
    ))
    with pytest.raises(SideEffectTransitionError):
        run(ledger_case.store.side_effect_store.complete_compensation(
            attempt_id=stale,
            guard=ledger_case.guard,
        ))
    completed = run(ledger_case.store.side_effect_store.complete_compensation(
        attempt_id=CompensationAttemptId(second.attempt_id, operation_id, 2),
        guard=ledger_case.guard,
    ))

    assert first.status is SideEffectStatus.COMPENSATING
    assert first.compensation_attempt == 1
    assert second.compensation_attempt == 2
    assert completed.status is SideEffectStatus.COMPENSATED
    assert _get(ledger_case, invocation.context.operation_id) == completed


def test_stale_aggregate_guard_leaves_ledger_empty(
    ledger_case: _LedgerCase,
) -> None:
    invocation = _invocation()
    stale_guard = RunWriteGuard(ledger_case.guard.expected_version - 1)

    with pytest.raises(SideEffectTransitionError):
        run(ledger_case.store.side_effect_store.reserve(
            invocation=invocation,
            policy=SideEffectPolicy.PURE,
            invocation_digest=invocation_digest(invocation),
            invocation_ref=_INVOCATION_REF,
            guard=stale_guard,
        ))

    assert run(_row_count(ledger_case.store)) == 0


@pytest.mark.parametrize("invalid_scope", ["tenant", "fence"])
def test_sqlite_rejects_tenant_and_fenced_writes(
    ledger_case: _LedgerCase,
    invalid_scope: str,
) -> None:
    invocation = _invocation(
        tenant_id="tenant_1" if invalid_scope == "tenant" else None,
    )
    guard = (
        RunWriteGuard(ledger_case.guard.expected_version, "claim_1", 1)
        if invalid_scope == "fence"
        else ledger_case.guard
    )

    with pytest.raises(SideEffectTransitionError):
        run(ledger_case.store.side_effect_store.reserve(
            invocation=invocation,
            policy=SideEffectPolicy.PURE,
            invocation_digest=invocation_digest(invocation),
            invocation_ref=_INVOCATION_REF,
            guard=guard,
        ))

    assert run(_row_count(ledger_case.store)) == 0


def _noncanonical_json(payload_json: str) -> str:
    return f" {payload_json}"


def _nan_json(payload_json: str) -> str:
    payload = json.loads(payload_json)
    payload["version"] = float("nan")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _null_invocation_ref(payload_json: str) -> str:
    payload = json.loads(payload_json)
    payload["invocation_ref"] = None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize(
    "corrupt",
    [_noncanonical_json, _nan_json, _null_invocation_ref],
    ids=["noncanonical-json", "nan", "null-invocation-ref"],
)
def test_corrupted_payload_fails_with_stable_domain_error(
    ledger_case: _LedgerCase,
    corrupt,
) -> None:  # type: ignore[no-untyped-def]
    invocation, _record = _reserve(ledger_case, SideEffectPolicy.PURE)
    payload_json = run(_payload_json(
        ledger_case.store,
        invocation.context.operation_id,
    ))
    run(_replace_payload_json(
        ledger_case.store,
        invocation.context.operation_id,
        corrupt(payload_json),
    ))

    with pytest.raises(
        CheckpointCorruptedError,
        match="^durable side effect record is corrupted$",
    ):
        _get(ledger_case, invocation.context.operation_id)


def test_row_payload_identity_mismatch_fails_with_stable_domain_error(
    ledger_case: _LedgerCase,
) -> None:
    invocation, _record = _reserve(ledger_case, SideEffectPolicy.PURE)
    connection = ledger_case.store._connection
    assert connection is not None
    run(connection.execute(
        "UPDATE durable_side_effects SET status = 'started' "
        "WHERE session_id = ? AND operation_id = ? AND attempt = 1",
        ("session_1", invocation.context.operation_id),
    ))

    with pytest.raises(
        CheckpointCorruptedError,
        match="^durable side effect record is corrupted$",
    ):
        _get(ledger_case, invocation.context.operation_id)
