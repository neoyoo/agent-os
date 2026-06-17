from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentos.multi.postgres_team import PostgresTeamStore
from agentos.multi.serializers import (
    team_member_record_from_dict,
    team_member_record_to_dict,
    team_record_from_dict,
    team_record_to_dict,
)
from agentos.multi.team import (
    TeamMessage,
    TeamNotFoundError,
    TeamMemberRecord,
    TeamRecord,
)
from agentos.workspace import WorkspaceHandle


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.teams: dict[str, dict[str, object]] = {}
        self.members: dict[tuple[str, str], dict[str, object]] = {}
        self.messages: dict[str, dict[str, object]] = {}
        self.sql: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_team_records" in sql:
            payload = json.loads(str(params[4]))
            self.teams[str(params[0])] = {
                "status": params[1],
                "leader_agent_id": params[2],
                "created_at": params[3],
                "payload": payload,
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_team_records" in sql:
            row = self.teams.get(str(params[0]))
            return FakeCursor([(row["payload"],)] if row else [])
        if "UPDATE agentos_team_records" in sql:
            row = self.teams.get(str(params[1]))
            if row is None:
                return FakeCursor()
            payload = json.loads(str(params[0]))
            row["status"] = "deleted"
            row["payload"] = payload
            return FakeCursor([(payload,)])
        if "INSERT INTO agentos_team_members" in sql:
            payload = json.loads(str(params[6]))
            self.members[(str(params[0]), str(params[1]))] = {
                "role": params[2],
                "status": params[3],
                "session_id": params[4],
                "created_at": params[5],
                "payload": payload,
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_team_members" in sql and "agent_id = %s" in sql:
            row = self.members.get((str(params[0]), str(params[1])))
            return FakeCursor([(row["payload"],)] if row else [])
        if "SELECT payload FROM agentos_team_members" in sql:
            rows = []
            for (team_id, _agent_id), row in sorted(
                self.members.items(),
                key=lambda item: (float(item[1]["created_at"]), item[0][1]),
            ):
                if team_id == str(params[0]):
                    rows.append((row["payload"],))
            return FakeCursor(rows)
        if "INSERT INTO agentos_team_messages" in sql:
            payload = json.loads(str(params[7]))
            self.messages[str(params[0])] = {
                "team_id": params[1],
                "from_agent_id": params[2],
                "to_agent_id": params[3],
                "kind": params[4],
                "created_at": params[5],
                "correlation_id": params[6],
                "payload": payload,
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_team_messages" in sql:
            rows = []
            for message_id, row in sorted(
                self.messages.items(),
                key=lambda item: (float(item[1]["created_at"]), item[0]),
            ):
                if row["team_id"] == str(params[0]):
                    rows.append((row["payload"],))
            return FakeCursor(rows)
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.gets = 0
        self.puts: list[FakeConnection] = []

    def getconn(self) -> FakeConnection:
        self.gets += 1
        return self.connection

    def putconn(self, connection: FakeConnection) -> None:
        self.puts.append(connection)


def workspace() -> WorkspaceHandle:
    return WorkspaceHandle(
        workspace_id="team:team_1",
        scope="team",
        root="/work/team_1",
        parent_workspace_id="session:session_1",
        metadata={"tenant": "acme"},
    )


def team() -> TeamRecord:
    return TeamRecord(
        team_id="team_1",
        name="Research Team",
        description="Finds evidence.",
        leader_agent_id="leader",
        created_at=1.0,
        workspace=workspace(),
    )


def leader() -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        agent_id="leader",
        role="leader",
        session_id="session_leader",
        capabilities=("coordinate",),
        workspace=workspace(),
        created_at=1.0,
    )


def worker() -> TeamMemberRecord:
    return TeamMemberRecord(
        team_id="team_1",
        agent_id="worker",
        role="worker",
        session_id="session_worker",
        capabilities=("research",),
        created_at=2.0,
    )


def message(
    message_id: str,
    *,
    from_agent_id: str = "leader",
    to_agent_id: str | None = "worker",
    created_at: float = 3.0,
) -> TeamMessage:
    return TeamMessage(
        message_id=message_id,
        team_id="team_1",
        from_agent_id=from_agent_id,
        to_agent_id=to_agent_id,
        content="Find source A.",
        kind="instruction",
        created_at=created_at,
        correlation_id="task_1",
        artifact_handles=("artifact://one",),
        metadata={"priority": "high"},
    )


def test_team_record_and_member_serializers_round_trip_workspace() -> None:
    assert team_record_from_dict(team_record_to_dict(team())) == team()
    assert team_member_record_from_dict(team_member_record_to_dict(leader())) == leader()


def test_postgres_team_store_round_trips_teams_members_and_messages() -> None:
    connection = FakeConnection()
    store = PostgresTeamStore(dsn="postgresql://unused", connection=connection)

    store.create_team(team())
    store.add_member(leader())
    store.add_member(worker())
    store.append_message(message("message_1"))

    assert store.get_team("team_1") == team()
    assert store.get_member("team_1", "leader") == leader()
    assert store.list_members("team_1") == [leader(), worker()]
    assert store.list_messages("team_1") == [message("message_1")]
    assert connection.commits == 4


def test_postgres_team_store_from_pool_borrows_per_operation() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresTeamStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    store.create_team(team())
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.get_team("team_1") == team()
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.add_member(leader())
    assert pool.gets == 3
    assert pool.puts == [connection, connection, connection]

    store.close()
    assert pool.puts == [connection, connection, connection]


def test_postgres_team_store_message_visibility_matches_in_memory_store() -> None:
    store = PostgresTeamStore(
        dsn="postgresql://unused",
        connection=FakeConnection(),
    )
    store.create_team(team())
    store.add_member(leader())
    store.add_member(worker())
    store.append_message(
        message(
            "message_1",
            from_agent_id="leader",
            to_agent_id="worker",
            created_at=3.0,
        ),
    )
    store.append_message(
        message(
            "message_2",
            from_agent_id="worker",
            to_agent_id=None,
            created_at=4.0,
        ),
    )

    assert store.list_messages("team_1", agent_id="leader") == [
        message(
            "message_2",
            from_agent_id="worker",
            to_agent_id=None,
            created_at=4.0,
        ),
    ]
    assert store.list_messages("team_1", agent_id="worker") == [
        message(
            "message_1",
            from_agent_id="leader",
            to_agent_id="worker",
            created_at=3.0,
        ),
    ]


def test_postgres_team_store_after_message_cursor_returns_later_messages() -> None:
    store = PostgresTeamStore(
        dsn="postgresql://unused",
        connection=FakeConnection(),
    )
    store.create_team(team())
    store.add_member(leader())
    store.add_member(worker())
    store.append_message(message("message_1", created_at=3.0))
    store.append_message(message("message_2", created_at=4.0))

    assert store.list_messages("team_1", after_message_id="message_1") == [
        message("message_2", created_at=4.0),
    ]
    assert store.list_messages("team_1", after_message_id="missing") == [
        message("message_1", created_at=3.0),
        message("message_2", created_at=4.0),
    ]


def test_postgres_team_store_mark_deleted_blocks_future_writes() -> None:
    store = PostgresTeamStore(
        dsn="postgresql://unused",
        connection=FakeConnection(),
    )
    store.create_team(team())

    assert store.mark_team_deleted("team_1", now=10.0) is True
    assert store.get_team("team_1") == replace(team(), status="deleted")
    assert store.mark_team_deleted("missing", now=11.0) is False

    with pytest.raises(TeamNotFoundError):
        store.add_member(worker())
    with pytest.raises(TeamNotFoundError):
        store.append_message(message("message_1"))


def test_postgres_team_store_migration_defines_tables_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-12-postgres-team-store.sql",
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS agentos_team_records" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_team_members" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_team_messages" in migration
    assert "agentos_team_messages_order_idx" in migration
    assert "payload JSONB NOT NULL" in migration
