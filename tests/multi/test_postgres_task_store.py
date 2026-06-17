import json
from dataclasses import replace
from pathlib import Path

from agentos.multi import TaskRecord, TaskRequest, TaskResult
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.serializers import task_record_from_dict, task_record_to_dict


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.outbox: list[dict[str, object]] = []
        self.commits = 0
        self.sql: list[str] = []

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_multi_agent_tasks" in sql:
            self.records[str(params[0])] = {
                "parent_agent_id": params[1],
                "target_agent_id": params[2],
                "status": params[3],
                "worker_id": params[4],
                "lease_expires_at": params[5],
                "deadline_at": params[6],
                "version": params[7],
                "payload": params[8],
                "consumed_at": params[9],
                "result_notified_at": params[10],
                "updated_at": params[11],
            }
            return FakeCursor()
        if "SELECT payload FROM agentos_multi_agent_tasks WHERE task_id" in sql:
            row = self.records.get(str(params[0]))
            return FakeCursor([(row["payload"],)] if row else [])
        if "parent_agent_id = %s" in sql and "consumed_at IS NULL" in sql:
            rows = []
            for row in self.records.values():
                record = task_record_from_dict(json.loads(str(row["payload"])))
                if (
                    record.parent_agent_id == str(params[0])
                    and record.result is not None
                    and record.consumed_at is None
                ):
                    rows.append((row["payload"],))
            return FakeCursor(rows)
        if "WITH candidates AS" in sql and "FOR UPDATE SKIP LOCKED" in sql:
            return self._claim(params)
        if "UPDATE agentos_multi_agent_tasks" in sql and "RETURNING payload" in sql:
            return self._update(params)
        if "INSERT INTO agentos_multi_agent_task_outbox" in sql:
            self.outbox.append(
                {
                    "task_id": params[0],
                    "event_type": params[1],
                    "payload": params[2],
                    "created_at": params[3],
                },
            )
            return FakeCursor()
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1

    def _claim(self, params: tuple[object, ...]) -> FakeCursor:
        capabilities = {str(capability) for capability in params[2]}
        limit = int(params[3])
        worker_id = str(params[4])
        lease_expires_at = float(params[5])
        now = float(params[6])
        rows: list[tuple[object, ...]] = []
        for task_id, row in self.records.items():
            if len(rows) >= limit:
                break
            record = task_record_from_dict(json.loads(str(row["payload"])))
            if record.status != "queued":
                continue
            required_capabilities = set(record.request.allowed_tool_names)
            if (
                required_capabilities
                and not required_capabilities.issubset(capabilities)
            ):
                continue
            updated = task_record_to_dict(
                replace(
                    record,
                    status="running",
                    worker_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    attempt=record.attempt + 1,
                    updated_at=now,
                    version=record.version + 1,
                ),
            )
            payload = json.dumps(updated, ensure_ascii=False)
            row["status"] = "running"
            row["worker_id"] = worker_id
            row["lease_expires_at"] = lease_expires_at
            row["version"] = updated["version"]
            row["payload"] = payload
            row["updated_at"] = now
            rows.append((task_id, payload))
        return FakeCursor(rows)

    def _update(self, params: tuple[object, ...]) -> FakeCursor:
        task_id = str(params[8])
        previous_version = int(params[9])
        previous_status = str(params[10])
        row = self.records.get(task_id)
        if (
            row is None
            or row["version"] != previous_version
            or row["status"] != previous_status
        ):
            return FakeCursor()
        row["status"] = params[0]
        row["worker_id"] = params[1]
        row["lease_expires_at"] = params[2]
        row["version"] = params[3]
        row["payload"] = params[4]
        row["consumed_at"] = params[5]
        row["result_notified_at"] = params[6]
        row["updated_at"] = params[7]
        return FakeCursor([(params[4],)])


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


def record(
    task_id: str = "task_1",
    *,
    allowed_tool_names: tuple[str, ...] = (),
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="worker",
        request=TaskRequest(
            task_id=task_id,
            instruction="Do work",
            allowed_tool_names=allowed_tool_names,
        ),
        status="queued",
        created_at=1.0,
        deadline_at=30.0,
    )


def test_postgres_task_store_saves_and_loads_record() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)

    store.create(record())

    assert store.get("task_1") == record()
    assert connection.commits == 1


def test_postgres_task_store_from_pool_borrows_per_operation() -> None:
    connection = FakeConnection()
    pool = FakePool(connection)
    store = PostgresTaskStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    assert pool.gets == 0

    store.create(record())
    assert pool.gets == 1
    assert pool.puts == [connection]

    assert store.get("task_1") == record()
    assert pool.gets == 2
    assert pool.puts == [connection, connection]

    store.close()
    assert pool.puts == [connection, connection]


def test_postgres_task_store_uses_atomic_claim_sql_and_updates_payload() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record())

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "FOR UPDATE SKIP LOCKED" in joined_sql
    assert "RETURNING tasks.task_id, tasks.payload" in joined_sql
    assert "payload = patched.payload" in joined_sql
    assert claims[0].task_id == "task_1"
    stored = store.get("task_1")
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_id == "worker-instance-1"
    assert stored.attempt == 1
    assert stored.version == 1


def test_postgres_task_store_does_not_claim_when_capabilities_do_not_match() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    original = record(allowed_tool_names=("code", "web"))
    store.create(original)

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "allowed_tool_names" in joined_sql
    assert claims == []
    assert store.get("task_1") == original


def test_postgres_task_store_claims_when_capabilities_cover_required_tools() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record(allowed_tool_names=("code", "web")))

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code", "web", "search"),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    assert [claim.task_id for claim in claims] == ["task_1"]
    stored = store.get("task_1")
    assert stored is not None
    assert stored.status == "running"


def test_postgres_task_store_terminal_transition_writes_outbox() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    changed = store.mark_completed(
        "task_1",
        TaskResult(task_id="task_1", status="completed", summary="done"),
        now=3.0,
        worker_id="worker-instance-1",
        attempt=1,
    )

    assert changed is True
    assert connection.outbox[0]["task_id"] == "task_1"
    assert connection.outbox[0]["event_type"] == "result_ready"


def test_postgres_task_store_consumes_results_once() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )
    store.mark_completed(
        "task_1",
        TaskResult(task_id="task_1", status="completed", summary="done"),
        now=3.0,
        worker_id="worker-instance-1",
        attempt=1,
    )

    assert store.consume_results_for_agent("parent") == [
        TaskResult(task_id="task_1", status="completed", summary="done"),
    ]
    assert store.consume_results_for_agent("parent") == []


def test_postgres_task_store_rejects_mark_running_after_terminal_status() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record())
    store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )
    store.mark_completed(
        "task_1",
        TaskResult(task_id="task_1", status="completed", summary="done"),
        now=3.0,
        worker_id="worker-instance-1",
        attempt=1,
    )

    assert store.mark_running("task_1", now=4.0) is False
    assert store.get("task_1").status == "completed"  # type: ignore[union-attr]


def test_postgres_multi_agent_tasks_migration_has_consumed_at_column() -> None:
    migration = Path(
        "docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql",
    ).read_text()

    assert "consumed_at DOUBLE PRECISION" in migration
