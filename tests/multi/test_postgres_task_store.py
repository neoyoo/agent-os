import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentos.multi import (
    TaskAlreadySubmittedError,
    TaskRecord,
    TaskRequest,
    TaskResult,
)
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
        self.rollbacks = 0
        self.aborted = False
        self.fail_outbox_insert = False
        self.sql: list[str] = []

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if self.aborted:
            raise RuntimeError("current transaction is aborted")
        if "INSERT INTO agentos_multi_agent_tasks" in sql:
            if str(params[0]) in self.records:
                self.aborted = True
                raise RuntimeError("duplicate key value violates unique constraint")
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
        if "SELECT COUNT(*) FROM agentos_multi_agent_tasks" in sql:
            agent_id = str(params[0])
            count = 0
            for row in self.records.values():
                if row["target_agent_id"] == agent_id and row["status"] in (
                    "queued",
                    "running",
                ):
                    count += 1
            return FakeCursor([(count,)])
        if "WITH candidates AS" in sql and "FOR UPDATE SKIP LOCKED" in sql:
            return self._claim(params)
        if "WITH candidate AS" in sql and "FOR UPDATE SKIP LOCKED" in sql:
            return self._claim(params, exact_task_id=str(params[0]))
        if "UPDATE agentos_multi_agent_tasks" in sql and "RETURNING payload" in sql:
            return self._update(params)
        if "INSERT INTO agentos_multi_agent_task_outbox" in sql:
            if self.fail_outbox_insert:
                self.aborted = True
                raise RuntimeError("outbox insert failed")
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

    def rollback(self) -> None:
        self.rollbacks += 1
        self.aborted = False

    def _claim(
        self,
        params: tuple[object, ...],
        *,
        exact_task_id: str | None = None,
    ) -> FakeCursor:
        if exact_task_id is None:
            target_agent_id = None if params[2] is None else str(params[2])
            capabilities = {str(capability) for capability in params[4]}
            limit = int(params[5])
            worker_id = str(params[6])
            lease_expires_at = float(params[7])
            now = float(params[8])
        else:
            target_agent_id = None
            capabilities = {str(capability) for capability in params[3]}
            limit = 1
            worker_id = str(params[4])
            lease_expires_at = float(params[5])
            now = float(params[6])
        rows: list[tuple[object, ...]] = []
        for task_id, row in self.records.items():
            if len(rows) >= limit:
                break
            if exact_task_id is not None and task_id != exact_task_id:
                continue
            record = task_record_from_dict(json.loads(str(row["payload"])))
            if (
                target_agent_id is not None
                and record.target_agent_id != target_agent_id
            ):
                continue
            if record.status != "queued":
                continue
            required_capabilities = set(record.request.required_capabilities)
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


class RecordingConnectionContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.exit_args: list[
            tuple[type[BaseException] | None, BaseException | None, object | None]
        ] = []

    def __enter__(self) -> FakeConnection:
        return self.connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool:
        self.exit_args.append((exc_type, exc, traceback))
        return False


class ContextPool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.contexts: list[RecordingConnectionContext] = []

    def connection(self) -> RecordingConnectionContext:
        context = RecordingConnectionContext(self._connection)
        self.contexts.append(context)
        return context


def record(
    task_id: str = "task_1",
    *,
    target_agent_id: str = "worker",
    required_capabilities: tuple[str, ...] = (),
    allowed_tool_names: tuple[str, ...] = (),
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id=target_agent_id,
        request=TaskRequest(
            task_id=task_id,
            instruction="Do work",
            required_capabilities=required_capabilities,
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


def test_postgres_task_store_reports_duplicate_task_id_as_already_submitted() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record())

    with pytest.raises(TaskAlreadySubmittedError, match="task_1"):
        store.create(record())

    assert store.get("task_1") == record()
    assert connection.commits == 1
    assert connection.rollbacks == 1


def test_postgres_task_store_rolls_back_after_duplicate_task_id() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record())

    with pytest.raises(TaskAlreadySubmittedError):
        store.create(record())

    store.create(record("task_2"))

    assert store.get("task_2") == record("task_2")
    assert connection.commits == 2
    assert connection.rollbacks == 1


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


def test_postgres_task_store_pool_context_receives_exception_context() -> None:
    connection = FakeConnection()
    pool = ContextPool(connection)
    store = PostgresTaskStore.from_pool(
        dsn="postgresql://unused",
        pool=pool,
    )

    with pytest.raises(RuntimeError, match="boom"):
        with store._connection_scope():
            raise RuntimeError("boom")

    assert len(pool.contexts) == 1
    assert len(pool.contexts[0].exit_args) == 1
    exc_type, exc, traceback = pool.contexts[0].exit_args[0]
    assert exc_type is RuntimeError
    assert isinstance(exc, RuntimeError)
    assert str(exc) == "boom"
    assert traceback is not None


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


def test_postgres_task_store_claim_queued_can_be_constrained_to_target_agent() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    other = record("task_1", target_agent_id="other-worker")
    target = record("task_2", target_agent_id="worker")
    store.create(other)
    store.create(target)

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        target_agent_id="worker",
        capabilities=("code",),
        limit=2,
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "%s::text IS NULL OR target_agent_id = %s::text" in joined_sql
    assert [claim.task_id for claim in claims] == ["task_2"]
    assert store.get("task_1") == other
    stored = store.get("task_2")
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_id == "worker-instance-1"


def test_postgres_task_store_claims_exact_task_id_atomically() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record("task_1"))
    store.create(record("task_2"))

    claim = store.claim_task(
        "task_2",
        worker_id="worker-instance-1",
        capabilities=("code",),
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "WHERE task_id = %s" in joined_sql
    assert "FOR UPDATE SKIP LOCKED" in joined_sql
    assert claim is not None
    assert claim.task_id == "task_2"
    assert store.get("task_1") == record("task_1")
    stored = store.get("task_2")
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_id == "worker-instance-1"
    assert stored.attempt == 1


def test_postgres_task_store_does_not_claim_when_capabilities_do_not_match() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    original = record(required_capabilities=("code", "web"))
    store.create(original)

    claims = store.claim_queued(
        worker_id="worker-instance-1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "required_capabilities" in joined_sql
    assert claims == []
    assert store.get("task_1") == original


def test_postgres_task_store_claims_when_capabilities_cover_required_capabilities() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(record(required_capabilities=("code", "web")))

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


def test_postgres_task_store_claims_by_required_capabilities_not_allowed_tools() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    store.create(
        record(
            required_capabilities=("architecture-review",),
            allowed_tool_names=("read_file",),
        ),
    )

    claim = store.claim_task(
        "task_1",
        worker_id="worker-instance-1",
        capabilities=("architecture-review",),
        lease_expires_at=20.0,
        now=2.0,
    )

    joined_sql = "\n".join(connection.sql)
    assert "required_capabilities" in joined_sql
    assert claim is not None
    stored = store.get("task_1")
    assert stored is not None
    assert stored.status == "running"
    assert stored.request.allowed_tool_names == ("read_file",)


def test_postgres_task_store_does_not_treat_allowed_tools_as_capabilities() -> None:
    connection = FakeConnection()
    store = PostgresTaskStore(dsn="postgresql://unused", connection=connection)
    original = record(
        required_capabilities=("architecture-review",),
        allowed_tool_names=("read_file",),
    )
    store.create(original)

    claim = store.claim_task(
        "task_1",
        worker_id="worker-instance-1",
        capabilities=("read_file",),
        lease_expires_at=20.0,
        now=2.0,
    )

    assert claim is None
    assert store.get("task_1") == original


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


def test_postgres_task_store_rolls_back_when_outbox_insert_fails() -> None:
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
    connection.fail_outbox_insert = True

    with pytest.raises(Exception, match="Postgres backend unavailable"):
        store.mark_completed(
            "task_1",
            TaskResult(task_id="task_1", status="completed", summary="done"),
            now=3.0,
            worker_id="worker-instance-1",
            attempt=1,
        )

    assert connection.rollbacks == 1
    assert connection.commits == 2


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
