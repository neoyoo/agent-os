from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from agentos.distributed.errors import RunSubmissionConflictError
from agentos.distributed.internal_errors import (
    InternalSubmissionBindingRevokedError,
    StaleInternalSubmissionAuthorityError,
)
from agentos.distributed.internal_models import (
    InternalRunInputReceipt,
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.postgres._team_internal import _submission_digest
from agentos.distributed.postgres.team import PostgresTeamStore
from agentos.multi.team_identity import team_submission_id
from tests.distributed.postgres.test_team_fakes import (
    FakeDatabase,
    FakeWorkspaceAuthority,
    NOW,
    SCOPE,
    delivery_row,
    member_row,
    message_and_delivery,
    worker,
)
from tests.planning._async import async_test


def _submission(delivery_id: str) -> InternalRunSubmission:
    message, delivery = message_and_delivery()
    assert delivery.delivery_id == delivery_id
    return InternalRunSubmission(
        delivery.target_session_id,
        team_submission_id(
            scope=SCOPE,
            delivery_id=delivery.delivery_id,
            target_session_id=delivery.target_session_id,
        ),
        "team_message",
        {
            "team_id": delivery.team_id,
            "message_id": message.message_id,
            "recipient_agent_id": delivery.recipient_agent_id,
            "action": "team_read_messages",
        },
    )


@async_test
async def test_expired_internal_authority_creates_no_run_or_accepted_input() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_1",
        fencing_token=3,
        claim_expires_at=NOW + timedelta(seconds=10),
    )
    submission = _submission(claimed.delivery_id)
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_1", 3)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=11),
                }
            ]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(StaleInternalSubmissionAuthorityError):
        await store.submit_internal(
            scope=SCOPE,
            submission=submission,
            authority=authority,
        )

    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )
    assert database.rollbacks == 1


@async_test
async def test_revoked_binding_raises_a_distinct_internal_submission_error() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_1",
        fencing_token=3,
        claim_expires_at=NOW + timedelta(seconds=10),
    )
    submission = _submission(claimed.delivery_id)
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_1", 3)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(InternalSubmissionBindingRevokedError):
        await store.submit_internal(
            scope=SCOPE,
            submission=submission,
            authority=authority,
        )

    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )
    assert database.rollbacks == 1


@async_test
async def test_internal_submission_locks_binding_before_session_and_creates_run() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_1",
        fencing_token=3,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    submission = _submission(claimed.delivery_id)
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_1", 3)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "FROM agentos_team_members" in query:
            return [member_row(worker())]
        if "FROM agentos_distributed_sessions" in query:
            return [{"session_id": "session_2"}]
        if "FROM agentos_distributed_submissions" in query:
            return []
        if "FROM agentos_distributed_runs" in query:
            return []
        if "UPDATE agentos_distributed_sessions" in query:
            return [{"turn_number": 4}]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    receipt = await store.submit_internal(
        scope=SCOPE,
        submission=submission,
        authority=authority,
    )

    trace = [query for query, _ in database.connection_value.trace]
    binding_lock = next(
        index for index, query in enumerate(trace) if "FROM agentos_team_members" in query
    )
    session_lock = next(
        index
        for index, query in enumerate(trace)
        if "FROM agentos_distributed_sessions" in query
    )
    assert binding_lock < session_lock
    assert any("INSERT INTO agentos_distributed_runs" in query for query in trace)
    assert any("INSERT INTO agentos_distributed_accepted_inputs" in query for query in trace)
    assert receipt.session_id == "session_2"
    assert receipt.aggregate_version == 1
    assert receipt.duplicate is False
    assert database.commits == 1


@async_test
async def test_internal_submission_duplicate_reuses_original_run() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_2",
        fencing_token=4,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    submission = _submission(claimed.delivery_id)
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_2", 4)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "FROM agentos_team_members" in query:
            return [member_row(worker())]
        if "FROM agentos_distributed_sessions" in query:
            return [{"session_id": "session_2"}]
        if "FROM agentos_distributed_submissions" in query:
            return [
                {
                    "session_id": submission.session_id,
                    "run_id": "run_existing",
                    "input_digest": _submission_digest(SCOPE, submission),
                    "aggregate_version": 1,
                    "source_payload_json": (
                        '{"action":"team_read_messages","message_id":"'
                        + str(submission.source_payload["message_id"])
                        + '","recipient_agent_id":"agent_2","team_id":"team_1"}'
                    ),
                    "team_delivery_id": authority.delivery_id,
                }
            ]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    receipt = await store.submit_internal(
        scope=SCOPE,
        submission=submission,
        authority=authority,
    )

    assert receipt.run_id == "run_existing"
    assert receipt.duplicate is True
    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )


@async_test
async def test_internal_duplicate_does_not_require_active_binding() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_2",
        fencing_token=4,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    submission = _submission(claimed.delivery_id)
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_2", 4)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "FROM agentos_distributed_submissions" in query:
            return [
                {
                    "session_id": submission.session_id,
                    "run_id": "run_terminal",
                    "input_digest": _submission_digest(SCOPE, submission),
                    "aggregate_version": 1,
                    "source_payload_json": (
                        '{"action":"team_read_messages","message_id":"'
                        + str(submission.source_payload["message_id"])
                        + '","recipient_agent_id":"agent_2","team_id":"team_1"}'
                    ),
                    "team_delivery_id": authority.delivery_id,
                }
            ]
        if "FROM agentos_team_members" in query:
            raise AssertionError("exact duplicate must not revalidate deleted binding")
        return []

    store = PostgresTeamStore(
        FakeDatabase(handler),  # type: ignore[arg-type]
        FakeWorkspaceAuthority(),
    )

    receipt = await store.submit_internal(
        scope=SCOPE,
        submission=submission,
        authority=authority,
    )

    assert receipt.run_id == "run_terminal"
    assert receipt.duplicate is True


@pytest.mark.parametrize(
    ("source_kind", "continuation_kind", "version_field", "expected_kind", "version"),
    [
        (
            "team_message",
            None,
            "submission_version",
            "internal_start",
            1,
        ),
        (
            "command",
            "wakeup",
            "command_version",
            "wakeup",
            7,
        ),
    ],
)
@async_test
async def test_applied_input_is_recovered_by_current_delivery_claim(
    source_kind: str,
    continuation_kind: str | None,
    version_field: str,
    expected_kind: str,
    version: int,
) -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_3",
        fencing_token=5,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_3", 5)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "FROM agentos_distributed_accepted_inputs" in query:
            return [
                {
                    "session_id": "session_2",
                    "run_id": "run_1",
                    "source_kind": source_kind,
                    "continuation_kind": continuation_kind,
                    "submission_version": version if version_field == "submission_version" else None,
                    "command_version": version if version_field == "command_version" else None,
                }
            ]
        return []

    store = PostgresTeamStore(
        FakeDatabase(handler),  # type: ignore[arg-type]
        FakeWorkspaceAuthority(),
    )

    assert await store.get_applied_input(
        scope=SCOPE,
        authority=authority,
    ) == InternalRunInputReceipt(
        claimed.delivery_id,
        "session_2",
        expected_kind,  # type: ignore[arg-type]
        "run_1",
        version,
    )


@async_test
async def test_applied_input_returns_none_when_delivery_has_no_run_input() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_3",
        fencing_token=5,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_3", 5)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        return []

    store = PostgresTeamStore(
        FakeDatabase(handler),  # type: ignore[arg-type]
        FakeWorkspaceAuthority(),
    )

    assert await store.get_applied_input(scope=SCOPE, authority=authority) is None


@async_test
async def test_internal_duplicate_digest_conflict_does_not_modify_run() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_2",
        fencing_token=4,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    submission = _submission(claimed.delivery_id)
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_2", 4)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "FROM agentos_team_members" in query:
            return [member_row(worker())]
        if "FROM agentos_distributed_sessions" in query:
            return [{"session_id": "session_2"}]
        if "FROM agentos_distributed_submissions" in query:
            return [
                {
                    "session_id": submission.session_id,
                    "run_id": "run_existing",
                    "input_digest": "f" * 64,
                    "aggregate_version": 1,
                    "source_payload_json": "{}",
                    "team_delivery_id": authority.delivery_id,
                }
            ]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(RunSubmissionConflictError):
        await store.submit_internal(
            scope=SCOPE,
            submission=submission,
            authority=authority,
        )

    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )
