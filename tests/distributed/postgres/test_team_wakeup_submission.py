from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from agentos.distributed.errors import CommandStateError
from agentos.distributed.internal_errors import StaleInternalSubmissionAuthorityError
from agentos.distributed.internal_models import InternalSubmissionAuthority
from agentos.distributed.postgres.team import PostgresTeamStore
from agentos.multi.team_identity import team_command_id
from agentos.runtime.durable_commands import DurableRunCommand
from tests.distributed.postgres.test_team_fakes import (
    FakeDatabase,
    FakeWorkspaceAuthority,
    NOW,
    SCOPE,
    delivery_row,
    message_and_delivery,
)
from tests.planning._async import async_test


def _trusted_command(delivery_id: str) -> DurableRunCommand:
    message, delivery = message_and_delivery()
    assert delivery.delivery_id == delivery_id
    return DurableRunCommand(
        "run_1",
        team_command_id(
            scope=SCOPE,
            delivery_id=delivery_id,
            target_session_id="session_2",
            run_id="run_1",
        ),
        "wakeup",
        {
            "team_id": delivery.team_id,
            "message_id": message.message_id,
            "recipient_agent_id": delivery.recipient_agent_id,
            "action": "team_read_messages",
        },
    )


def _handler(
    *,
    correlation_id: str | None = "correlation_1",
    wait_kind: str = "remote_result",
    wait_handle: str = "correlation_1",
    expired: bool = False,
    released: bool = False,
):
    _, pending = message_and_delivery()
    claimed = (
        replace(pending, fencing_token=3)
        if released
        else replace(
            pending,
            state="claimed",
            claim_id="claim_1",
            fencing_token=3,
            claim_expires_at=NOW + timedelta(seconds=60),
        )
    )

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_team_deliveries" in query:
            return [
                {
                    **delivery_row(claimed),
                    "database_now": NOW + timedelta(seconds=61 if expired else 1),
                }
            ]
        if "FROM agentos_team_messages" in query:
            return [{"correlation_id": correlation_id}]
        if "FROM agentos_distributed_commands" in query:
            return []
        if "FROM agentos_distributed_sessions" in query:
            return [
                {
                    "fencing_token": 2,
                    "session_fencing_token": 2,
                    "active_claim_id": None,
                    "active_claim_run_id": None,
                    "active_claim_expires_at": None,
                }
            ]
        if "FROM agentos_distributed_runs" in query:
            return [
                {
                    "tenant_id": SCOPE.tenant_id,
                    "session_id": "session_2",
                    "run_id": "run_1",
                    "status": "waiting",
                    "wait_kind": wait_kind,
                    "wait_handle": wait_handle,
                    "wait_detail": None,
                    "wait_not_before": (
                        NOW + timedelta(seconds=30)
                        if wait_kind == "timer"
                        else None
                    ),
                    "aggregate_version": 3,
                    "database_now": NOW + timedelta(seconds=1),
                }
            ]
        if "UPDATE agentos_distributed_sessions" in query:
            return [{"turn_number": 4}]
        return []

    return claimed, handler


@async_test
async def test_trusted_wakeup_persists_team_delivery_provenance() -> None:
    claimed, handler = _handler()
    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]
    authority = InternalSubmissionAuthority(claimed.delivery_id, "claim_1", 3)

    receipt = await store.submit_wakeup(
        scope=SCOPE,
        session_id="session_2",
        command=_trusted_command(claimed.delivery_id),
        authority=authority,
    )

    command_insert = next(
        params
        for query, params in database.connection_value.trace
        if query.startswith("INSERT INTO agentos_distributed_commands")
    )
    accepted_insert = next(
        params
        for query, params in database.connection_value.trace
        if query.startswith("INSERT INTO agentos_distributed_accepted_inputs")
    )
    assert command_insert[-1] == claimed.delivery_id
    assert accepted_insert[-1] == claimed.delivery_id
    assert receipt.aggregate_version == 4
    assert database.commits == 1


@pytest.mark.parametrize(
    ("expired", "released", "fence"),
    [(True, False, 3), (False, True, 3), (False, False, 2)],
)
@async_test
async def test_stale_team_wakeup_authority_creates_no_command(
    expired: bool,
    released: bool,
    fence: int,
) -> None:
    claimed, handler = _handler(expired=expired, released=released)
    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(StaleInternalSubmissionAuthorityError):
        await store.submit_wakeup(
            scope=SCOPE,
            session_id="session_2",
            command=_trusted_command(claimed.delivery_id),
            authority=InternalSubmissionAuthority(
                claimed.delivery_id,
                "claim_1",
                fence,
            ),
        )

    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )
    assert database.rollbacks == 1


@pytest.mark.parametrize(
    ("correlation_id", "wait_kind", "wait_handle"),
    [
        ("correlation_1", "remote_result", "different_handle"),
        ("correlation_1", "timer", "correlation_1"),
        (None, "remote_result", "correlation_1"),
    ],
)
@async_test
async def test_trusted_wakeup_revalidates_message_against_locked_wait(
    correlation_id: str | None,
    wait_kind: str,
    wait_handle: str,
) -> None:
    claimed, handler = _handler(
        correlation_id=correlation_id,
        wait_kind=wait_kind,
        wait_handle=wait_handle,
    )
    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(CommandStateError):
        await store.submit_wakeup(
            scope=SCOPE,
            session_id="session_2",
            command=_trusted_command(claimed.delivery_id),
            authority=InternalSubmissionAuthority(claimed.delivery_id, "claim_1", 3),
        )

    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )
    assert database.rollbacks == 1
