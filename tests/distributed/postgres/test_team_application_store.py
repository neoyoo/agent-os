from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres.team import PostgresTeamStore
from agentos.multi.team_errors import (
    TeamActiveRunConflictError,
    TeamBoundaryError,
    TeamConflictError,
    TeamMembershipError,
)
from agentos.multi.team_types import TeamAccessContext
from agentos.workspace import WorkspaceHandle
from tests.distributed.postgres.test_team_fakes import (
    FakeDatabase,
    FakeWorkspaceAuthority,
    NOW,
    SCOPE,
    access,
    delivery_row,
    leader,
    member_row,
    message_and_delivery,
    message_row,
    request,
    team,
    team_row,
    worker,
)
from tests.planning._async import async_test


class UnrelatedParentWorkspaceAuthority(FakeWorkspaceAuthority):
    async def resolve_target_workspace(
        self,
        *,
        scope: RequestScope,
        target_session_id: str,
    ) -> WorkspaceHandle:
        return WorkspaceHandle(
            f"workspace_{target_session_id}",
            "session",
            root=f"C:/work/team_1/sessions/{target_session_id}",
            parent_workspace_id="workspace_other_team",
        )


class EscapingRootWorkspaceAuthority(FakeWorkspaceAuthority):
    async def resolve_target_workspace(
        self,
        *,
        scope: RequestScope,
        target_session_id: str,
    ) -> WorkspaceHandle:
        return WorkspaceHandle(
            f"workspace_{target_session_id}",
            "session",
            root=f"C:/outside/{target_session_id}",
            parent_workspace_id="workspace_team_1",
        )


class FailingTeamWorkspaceAuthority(FakeWorkspaceAuthority):
    async def resolve_team_workspace(
        self,
        *,
        scope: RequestScope,
        workspace_id: str,
    ) -> WorkspaceHandle | None:
        raise RuntimeError("workspace authority unavailable")


@async_test
async def test_create_team_persists_only_workspace_id() -> None:
    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_distributed_sessions" in query:
            return [{"session_id": "session_1"}]
        if "FROM agentos_teams" in query or "active_session" in query:
            return []
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    assert await store.create_team(scope=SCOPE, team=team(), leader=leader()) == team()

    insert = next(
        item
        for item in database.connection_value.trace
        if "INSERT INTO agentos_teams" in item[0]
    )
    assert "workspace_team_1" in insert[1]
    assert all("C:/work" not in str(value) for value in insert[1])
    assert all("local-only" not in str(value) for value in insert[1])
    trace = [query for query, _ in database.connection_value.trace]
    binding_lock = next(
        index
        for index, query in enumerate(trace)
        if "FROM agentos_team_members" in query and "FOR UPDATE" in query
    )
    session_lock = next(
        index
        for index, query in enumerate(trace)
        if "FROM agentos_distributed_sessions" in query and "FOR UPDATE" in query
    )
    assert binding_lock < session_lock
    assert database.commits == 1


@pytest.mark.parametrize(
    "authority",
    [UnrelatedParentWorkspaceAuthority(), EscapingRootWorkspaceAuthority()],
)
@async_test
async def test_create_team_rejects_workspace_that_does_not_narrow_canonical_parent(
    authority: FakeWorkspaceAuthority,
) -> None:
    database = FakeDatabase(lambda query, params: [])
    store = PostgresTeamStore(database, authority)  # type: ignore[arg-type]

    with pytest.raises(TeamBoundaryError):
        await store.create_team(scope=SCOPE, team=team(), leader=leader())

    assert database.connection_value.trace == []


@async_test
async def test_send_message_rolls_back_message_delivery_and_outbox_together() -> None:
    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            if "recipient_agent_id = %s" in query:
                return [member_row(leader())]
            return [member_row(worker())]
        if "FROM agentos_team_messages" in query:
            return []
        if "INSERT INTO agentos_distributed_outbox" in query:
            raise RuntimeError("outbox insert failed")
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await store.send_message(scope=SCOPE, access=access(), request=request())

    sql = [query for query, _ in database.connection_value.trace]
    assert any("INSERT INTO agentos_team_messages" in query for query in sql)
    assert any("INSERT INTO agentos_team_deliveries" in query for query in sql)
    assert any("INSERT INTO agentos_distributed_outbox" in query for query in sql)
    assert database.commits == 0
    assert database.rollbacks == 1


@async_test
async def test_duplicate_message_reuses_frozen_snapshot_without_membership_rescan() -> None:
    existing_message, existing_delivery = message_and_delivery()

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            if "recipient_agent_id = %s" not in query:
                raise AssertionError("duplicate must not rescan recipient membership")
            return [member_row(leader())]
        if "FROM agentos_team_messages" in query:
            return [message_row(existing_message)]
        if "FROM agentos_team_deliveries" in query:
            return [delivery_row(existing_delivery)]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    receipt = await store.send_message(scope=SCOPE, access=access(), request=request())

    assert receipt.duplicate is True
    assert receipt.message == existing_message
    assert receipt.deliveries == (existing_delivery,)


@async_test
async def test_duplicate_message_conflicts_when_request_semantics_change() -> None:
    existing_message, _ = message_and_delivery()

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            return [member_row(leader())]
        if "FROM agentos_team_messages" in query:
            return [message_row(existing_message)]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(TeamConflictError):
        await store.send_message(
            scope=SCOPE,
            access=access(),
            request=request(content="changed content"),
        )


@async_test
async def test_message_page_uses_sequence_not_request_time_as_cursor_truth() -> None:
    first, _ = message_and_delivery()
    later_commit = replace(
        first,
        message_id="team_msg_" + "2" * 64,
        operation_id="operation_2",
        request_sha256="2" * 64,
        created_at=NOW - timedelta(minutes=5),
    )

    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            return [member_row(leader())]
        if "SELECT message.message_sequence" in query:
            return [{"message_sequence": 10, "message_id": first.message_id}]
        if "SELECT message.*" in query:
            assert "message.message_sequence > %s" in query
            assert "ORDER BY message.message_sequence" in query
            assert "message.created_at" not in query
            assert params[-2] == 10
            return [{**message_row(later_commit), "message_sequence": 11}]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    page = await store.list_messages(
        scope=SCOPE,
        access=access(),
        after_message_id=first.message_id,
        limit=10,
    )

    assert page.messages == (later_commit,)
    assert page.next_cursor is None


@async_test
async def test_delete_team_locks_bindings_before_sessions_and_checks_active_runs() -> None:
    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query and "FOR UPDATE" in query:
            return [member_row(leader()), member_row(worker())]
        if "FROM agentos_distributed_sessions" in query:
            return [
                {"session_id": "session_1"},
                {"session_id": "session_2"},
            ]
        if "FROM agentos_distributed_runs" in query:
            return []
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    deleted = await store.delete_team(
        scope=SCOPE,
        access=access(),
        deleted_at=team().created_at + timedelta(seconds=1),
    )

    trace = [query for query, _ in database.connection_value.trace]
    binding_lock = next(
        index
        for index, query in enumerate(trace)
        if "FROM agentos_team_members" in query and "FOR UPDATE" in query
    )
    session_lock = next(
        index
        for index, query in enumerate(trace)
        if "FROM agentos_distributed_sessions" in query and "FOR UPDATE" in query
    )
    run_check = next(
        index
        for index, query in enumerate(trace)
        if "FROM agentos_distributed_runs" in query
    )
    assert binding_lock < session_lock < run_check
    assert deleted.status == "deleted"
    assert database.commits == 1


@pytest.mark.parametrize(
    ("authority", "deleted_at", "error"),
    [
        (
            FailingTeamWorkspaceAuthority(),
            team().created_at + timedelta(seconds=1),
            RuntimeError,
        ),
        (
            FakeWorkspaceAuthority(),
            team().created_at - timedelta(seconds=1),
            ValueError,
        ),
    ],
)
@async_test
async def test_delete_team_validates_result_before_soft_delete(
    authority: FakeWorkspaceAuthority,
    deleted_at: object,
    error: type[Exception],
) -> None:
    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            return [member_row(leader()), member_row(worker())]
        if "FROM agentos_distributed_sessions" in query:
            return [
                {"session_id": "session_1"},
                {"session_id": "session_2"},
            ]
        if "FROM agentos_distributed_runs" in query:
            return []
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, authority)  # type: ignore[arg-type]

    with pytest.raises(error):
        await store.delete_team(
            scope=SCOPE,
            access=access(),
            deleted_at=deleted_at,  # type: ignore[arg-type]
        )

    assert not any(
        query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )
    assert database.commits == 0
    assert database.rollbacks == 1


@async_test
async def test_message_write_revalidates_full_member_session_binding() -> None:
    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            return [member_row(leader())]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]
    wrong_binding = TeamAccessContext(
        "tenant_1",
        "team_1",
        "agent_1",
        "session_other",
    )

    with pytest.raises(TeamMembershipError):
        await store.send_message(
            scope=SCOPE,
            access=wrong_binding,
            request=request(),
        )

    assert not any(
        query.startswith("INSERT") or query.startswith("UPDATE")
        for query, _ in database.connection_value.trace
    )


@async_test
async def test_active_run_prevents_team_deletion_before_soft_delete() -> None:
    def handler(query: str, params: tuple[object, ...]):
        if "FROM agentos_teams" in query:
            return [team_row()]
        if "FROM agentos_team_members" in query:
            return [member_row(leader()), member_row(worker())]
        if "FROM agentos_distributed_sessions" in query:
            return [
                {"session_id": "session_1"},
                {"session_id": "session_2"},
            ]
        if "FROM agentos_distributed_runs" in query:
            return [{"run_id": "run_active"}]
        return []

    database = FakeDatabase(handler)
    store = PostgresTeamStore(database, FakeWorkspaceAuthority())  # type: ignore[arg-type]

    with pytest.raises(TeamActiveRunConflictError):
        await store.delete_team(
            scope=SCOPE,
            access=access(),
            deleted_at=team().created_at + timedelta(seconds=1),
        )

    assert not any(
        query.startswith("UPDATE") for query, _ in database.connection_value.trace
    )
