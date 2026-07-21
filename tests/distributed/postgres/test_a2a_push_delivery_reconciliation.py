from __future__ import annotations

import pytest

from agentos.distributed.a2a_models import A2APushConfigRecord, A2ATaskState
from agentos.distributed.errors import A2APushConflictError
from agentos.distributed.postgres._outbox_records import STATUS_TOPIC, insert_outbox
from agentos.distributed.postgres.a2a import PostgresA2APushStore
from agentos.runtime.payloads import ProtectedPayloadRef
from tests.distributed.postgres._a2a_push_fake import BINDING, Database, SCOPE
from tests.planning._async import async_test


def _record(*, url: str = "https://push.example.test/a2a") -> A2APushConfigRecord:
    return A2APushConfigRecord(
        tenant_id=SCOPE.tenant_id,
        task_id=BINDING.task_id,
        config_id="push_1",
        url=url,
        authentication_scheme="Bearer",
        secret_ref=ProtectedPayloadRef("ciphertext-1", "digest-1"),
    )


@async_test
async def test_create_locks_run_before_binding_and_reconciles_terminal_state() -> None:
    database = Database()
    connection = database.connection_value
    run = connection.runs[(SCOPE.tenant_id, BINDING.session_id, BINDING.run_id)]
    run["status"] = "completed"
    run["aggregate_version"] = 5
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]

    await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="create_terminal",
    )

    assert connection.lock_log[:4] == ["session", "run", "binding", "config"]
    assert len(connection.deliveries) == 1
    delivery = next(iter(connection.deliveries.values()))
    assert delivery["task_state"] == A2ATaskState.COMPLETED.value
    assert delivery["status_sequence"] == 5
    assert next(iter(connection.outboxes.values()))["payload"] == {
        "delivery_id": delivery["delivery_id"],
    }


@async_test
async def test_create_then_status_transition_produces_ordered_deliveries_once() -> None:
    database = Database()
    connection = database.connection_value
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]

    await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="create_queued",
    )
    await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="create_queued",
    )
    await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id=BINDING.session_id,
        run_id=BINDING.run_id,
        source_kind="running",
        source_id=f"{BINDING.run_id}:2",
        topic=STATUS_TOPIC,
        payload={"status": "running", "status_sequence": 2},
    )

    deliveries = sorted(
        connection.deliveries.values(),
        key=lambda row: int(row["status_sequence"]),
    )
    assert [row["status_sequence"] for row in deliveries] == [1, 2]
    assert [row["task_state"] for row in deliveries] == [
        A2ATaskState.SUBMITTED.value,
        A2ATaskState.WORKING.value,
    ]


@async_test
async def test_new_operation_cannot_overwrite_existing_config_identity() -> None:
    database = Database()
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]
    await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="create_1",
    )

    with pytest.raises(A2APushConflictError):
        await store.create(
            scope=SCOPE,
            binding=BINDING,
            record=_record(url="https://other.example.test/a2a"),
            operation_id="create_2",
        )


@async_test
async def test_delete_suppresses_pending_delivery_before_removing_config() -> None:
    database = Database()
    connection = database.connection_value
    store = PostgresA2APushStore(database)  # type: ignore[arg-type]
    await store.create(
        scope=SCOPE,
        binding=BINDING,
        record=_record(),
        operation_id="create_1",
    )
    delivery = next(iter(connection.deliveries.values()))
    delivery["attempt_id"] = "attempt_1"
    delivery["attempt_owner_id"] = "worker_1"
    delivery["attempt_expires_at"] = connection.runs[
        (SCOPE.tenant_id, BINDING.session_id, BINDING.run_id)
    ]["database_now"]
    delivery["next_attempt_at"] = delivery["attempt_expires_at"]
    connection.lock_log.clear()

    await store.delete(
        scope=SCOPE,
        task_id=BINDING.task_id,
        config_id="push_1",
        operation_id="delete_1",
    )

    assert connection.lock_log == [
        "session",
        "run",
        "binding",
        "config",
        "delivery",
    ]
    assert connection.configs == {}
    assert delivery["suppressed_at"] is not None
    assert delivery["attempt_id"] is None
    assert delivery["attempt_owner_id"] is None
    assert delivery["attempt_expires_at"] is None
    assert delivery["next_attempt_at"] is None
