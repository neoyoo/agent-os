from __future__ import annotations

import json
from pathlib import Path

from agentos.multi.postgres_team import PostgresTeamUiStreamStore


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.next_ids: dict[str, int] = {}
        self.events: dict[tuple[str, int], dict[str, object]] = {}
        self.sql: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_team_ui_event_sequences" in sql:
            team_id = str(params[0])
            next_id = self.next_ids.get(team_id, 1)
            self.next_ids[team_id] = next_id + 1
            return FakeCursor([(next_id,)])
        if "INSERT INTO agentos_team_ui_events" in sql:
            payload = json.loads(str(params[4]))
            self.events[(str(params[0]), int(params[1]))] = {
                "team_id": params[0],
                "event_id": params[1],
                "kind": params[2],
                "created_at": params[3],
                "payload": payload,
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_team_ui_events" in sql:
            rows = []
            team_id = str(params[0])
            after_event_id = int(params[1])
            for (_team_id, event_id), row in sorted(self.events.items()):
                if _team_id == team_id and event_id > after_event_id:
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


def test_postgres_team_ui_stream_store_appends_and_lists_by_cursor() -> None:
    connection = FakeConnection()
    store = PostgresTeamUiStreamStore(
        dsn="postgresql://unused",
        connection=connection,
    )

    created = store.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1"},
        created_at=1.0,
    )
    message = store.append(
        team_id="team_1",
        kind="message_appended",
        payload={"message_id": "message_1"},
        created_at=2.0,
    )
    other_team = store.append(
        team_id="team_2",
        kind="team_created",
        payload={"team_id": "team_2"},
        created_at=3.0,
    )

    assert created.event_id == 1
    assert message.event_id == 2
    assert other_team.event_id == 1
    assert store.list_events("team_1", after_event_id=1) == (message,)
    assert connection.commits == 3
    joined_sql = "\n".join(connection.sql)
    assert "ON CONFLICT (team_id) DO UPDATE" in joined_sql
    assert "INSERT INTO agentos_team_ui_events" in joined_sql


def test_postgres_team_ui_stream_store_from_pool_borrows_per_operation() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresTeamUiStreamStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    event = store.append(
        team_id="team_1",
        kind="team_created",
        payload={"team_id": "team_1"},
        created_at=1.0,
    )
    assert event.event_id == 1
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.list_events("team_1") == (event,)
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


def test_postgres_team_ui_stream_migration_defines_table_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-15-postgres-team-ui-events.sql",
    ).read_text(encoding="utf-8")

    assert "-- migrate:up" in migration
    assert "-- migrate:down" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_team_ui_event_sequences" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_team_ui_events" in migration
    assert "PRIMARY KEY (team_id, event_id)" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "agentos_team_ui_events_kind_idx" in migration
