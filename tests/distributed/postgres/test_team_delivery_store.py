from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from agentos.distributed.postgres.team import PostgresTeamStore
from agentos.multi.team_delivery_types import TeamDeliveryClaim, TeamDeliveryResult
from agentos.multi.team_errors import (
    StaleTeamDeliveryClaimError,
    TeamConflictError,
)
from agentos.multi.team_identity import team_outbox_id
from agentos.runtime.run_state import RunStatus
from tests.distributed.postgres.test_team_fakes import (
    FakeDatabase,
    FakeWorkspaceAuthority,
    NOW,
    SCOPE,
    delivery_row,
    delivery_target_row,
    message_and_delivery,
)
from tests.planning._async import async_test


def _ready_outbox_id(delivery_id: str) -> str:
    return team_outbox_id(
        scope=SCOPE,
        delivery_id=delivery_id,
        outbox_kind="delivery_ready",
    )


@async_test
async def test_resolve_delivery_rebuilds_authoritative_target_from_outbox() -> None:
    message, delivery = message_and_delivery()
    outbox_id = _ready_outbox_id(delivery.delivery_id)

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_distributed_outbox" in query:
            return [delivery_target_row(message, delivery, outbox_id=outbox_id)]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    target = await store.resolve_delivery(outbox_id=outbox_id)

    assert target is not None
    assert target.tenant_id == SCOPE.tenant_id
    assert target.message == message
    assert target.delivery == delivery


@async_test
async def test_resolve_event_uses_unique_structured_delivery_identity() -> None:
    _, delivery = message_and_delivery()
    outbox_id = team_outbox_id(
        scope=SCOPE,
        delivery_id=delivery.delivery_id,
        outbox_kind="delivery_result",
    )

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_distributed_outbox" not in query:
            return []
        assert "event.delivery_id = delivery.delivery_id" in query
        assert "payload_json ->>" not in query
        return [
            {
                "outbox_id": outbox_id,
                "tenant_id": SCOPE.tenant_id,
                "team_id": delivery.team_id,
                "event_sequence": 7,
                "event_kind": "delivery_applied",
                "payload_json": {
                    "delivery_id": delivery.delivery_id,
                    "result": {
                        "result_kind": "internal_start",
                        "observed_run_id": "run_1",
                        "observed_aggregate_version": 2,
                        "observed_run_status": "queued",
                    },
                },
                "created_at": NOW,
            }
        ]

    store = PostgresTeamStore(
        FakeDatabase(handler),  # type: ignore[arg-type]
        FakeWorkspaceAuthority(),
    )

    target = await store.resolve_event(outbox_id=outbox_id)

    assert target is not None
    assert target.event.event_sequence == 7
    assert target.event.event.delivery_id == delivery.delivery_id


@async_test
async def test_expired_claim_takeover_increments_monotonic_fence_with_database_time() -> None:
    message, pending = message_and_delivery()
    expired = replace(
        pending,
        state="claimed",
        claim_id="old_claim",
        fencing_token=3,
        claim_expires_at=NOW + timedelta(seconds=10),
    )
    database_now = NOW + timedelta(seconds=20)
    claimed = replace(
        expired,
        claim_id="new_claim",
        fencing_token=4,
        claim_expires_at=database_now + timedelta(seconds=30),
        updated_at=database_now,
    )
    outbox_id = _ready_outbox_id(pending.delivery_id)

    def handler(query: str, params: tuple[object, ...]):
        if "FOR UPDATE OF delivery" in query:
            return [
                {
                    **delivery_target_row(message, expired, outbox_id=outbox_id),
                    "database_now": database_now,
                }
            ]
        if "UPDATE agentos_team_deliveries" in query:
            return [delivery_row(claimed)]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    result = await store.claim_pending(
        scope=SCOPE,
        outbox_id=outbox_id,
        claim_id="new_claim",
        ttl=timedelta(seconds=30),
    )

    assert result is not None
    assert result.claim.fencing_token == 4
    assert result.claim.expires_at == database_now + timedelta(seconds=30)
    update = next(
        query
        for query, _ in database.connection_value.trace
        if "UPDATE agentos_team_deliveries" in query
    )
    assert "clock_timestamp()" in update


@async_test
async def test_expired_claim_result_is_stale_and_writes_nothing() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_1",
        fencing_token=1,
        claim_expires_at=NOW + timedelta(seconds=10),
    )
    claim = TeamDeliveryClaim(
        "tenant_1",
        "team_1",
        claimed.delivery_id,
        "claim_1",
        1,
        NOW + timedelta(seconds=10),
    )

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

    with pytest.raises(StaleTeamDeliveryClaimError):
        await store.commit_result(
            scope=SCOPE,
            claim=claim,
            result=TeamDeliveryResult(
                "internal_start",
                "run_1",
                1,
                RunStatus.QUEUED,
            ),
        )

    writes = [
        query
        for query, _ in database.connection_value.trace
        if query.startswith("UPDATE") or query.startswith("INSERT")
    ]
    assert writes == []
    assert database.rollbacks == 1


@async_test
async def test_commit_result_persists_terminal_event_and_result_outbox_atomically() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_1",
        fencing_token=1,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    result = TeamDeliveryResult(
        "internal_start",
        "run_1",
        2,
        RunStatus.QUEUED,
    )
    terminal = replace(
        claimed,
        state="applied",
        claim_id=None,
        claim_expires_at=None,
        result_kind=result.result_kind,
        observed_run_id=result.observed_run_id,
        observed_aggregate_version=result.observed_aggregate_version,
        observed_run_status=result.observed_run_status,
        updated_at=NOW + timedelta(seconds=1),
    )
    claim = TeamDeliveryClaim(
        "tenant_1",
        "team_1",
        claimed.delivery_id,
        "claim_1",
        1,
        claimed.claim_expires_at,  # type: ignore[arg-type]
    )

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "UPDATE agentos_team_deliveries" in query:
            return [delivery_row(terminal)]
        if "INSERT INTO agentos_team_events" in query:
            return [{"event_sequence": 7, "created_at": NOW + timedelta(seconds=1)}]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    committed = await store.commit_result(scope=SCOPE, claim=claim, result=result)

    assert committed == terminal
    trace = [query for query, _ in database.connection_value.trace]
    assert any("INSERT INTO agentos_team_events" in query for query in trace)
    assert any("INSERT INTO agentos_distributed_outbox" in query for query in trace)
    event_query, event_params = next(
        (query, params)
        for query, params in database.connection_value.trace
        if "INSERT INTO agentos_team_events" in query
    )
    assert (
        "tenant_id, team_id, delivery_id, event_kind, payload_json"
        in event_query
    )
    assert event_params[2] == claimed.delivery_id
    assert database.commits == 1


@async_test
async def test_nonterminal_rejection_revalidates_observed_run_before_writing() -> None:
    _, pending = message_and_delivery()
    claimed = replace(
        pending,
        state="claimed",
        claim_id="claim_1",
        fencing_token=1,
        claim_expires_at=NOW + timedelta(seconds=60),
    )
    claim = TeamDeliveryClaim(
        "tenant_1",
        "team_1",
        claimed.delivery_id,
        "claim_1",
        1,
        claimed.claim_expires_at,  # type: ignore[arg-type]
    )

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "FROM agentos_distributed_runs" in query:
            return [
                {
                    "run_id": "run_2",
                    "aggregate_version": 4,
                    "status": "running",
                }
            ]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(TeamConflictError):
        await store.commit_result(
            scope=SCOPE,
            claim=claim,
            result=TeamDeliveryResult(
                "rejected_nonterminal",
                "run_1",
                3,
                RunStatus.RUNNING,
            ),
        )

    assert not any(
        query.startswith("UPDATE") or query.startswith("INSERT")
        for query, _ in database.connection_value.trace
    )


@async_test
async def test_heartbeat_and_release_require_current_unexpired_fence() -> None:
    _, pending = message_and_delivery()
    claim = TeamDeliveryClaim(
        "tenant_1",
        "team_1",
        pending.delivery_id,
        "claim_1",
        2,
        NOW + timedelta(seconds=30),
    )
    responses = 0

    def handler(query: str, params: tuple[object, ...]):
        nonlocal responses
        if "UPDATE agentos_team_deliveries" not in query:
            return []
        responses += 1
        if responses == 1:
            return [{"claim_expires_at": NOW + timedelta(seconds=60)}]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    refreshed = await store.heartbeat(
        scope=SCOPE,
        claim=claim,
        ttl=timedelta(seconds=30),
    )
    assert refreshed.expires_at == NOW + timedelta(seconds=60)

    with pytest.raises(StaleTeamDeliveryClaimError):
        await store.release(scope=SCOPE, claim=refreshed)

    updates = [
        query
        for query, _ in database.connection_value.trace
        if "UPDATE agentos_team_deliveries" in query
    ]
    assert all("clock_timestamp()" in query for query in updates)
