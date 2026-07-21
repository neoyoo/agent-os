from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json

import pytest

from agentos.distributed.a2a_models import (
    A2APushAttemptResolution,
    A2APushFailureCategory,
    A2ATaskState,
)
from agentos.distributed.errors import (
    A2APushAttemptFencedError,
    A2APushDeliveryDeferredError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._outbox_records import STATUS_TOPIC, insert_outbox
from agentos.distributed.postgres.a2a_delivery import PostgresA2APushDeliveryStore
from agentos.runtime.payloads import ProtectedPayloadRef
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
DELIVERED_AT = datetime(2026, 7, 21, 12, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = [] if rows is None else rows

    async def fetchone(self) -> dict[str, object] | None:
        return None if not self._rows else self._rows[0]

    async def fetchall(self) -> list[dict[str, object]]:
        return list(self._rows)


class DeliveryConnection:
    def __init__(self) -> None:
        self.now = DELIVERED_AT
        self.configs = [
            {
                "tenant_id": "tenant_1",
                "task_id": "run_1",
                "session_id": "session_1",
                "run_id": "run_1",
                "config_id": "push_1",
                "url": "https://push.example.test/a2a",
                "authentication_scheme": "Bearer",
                "secret_token": "sealed-token",
                "secret_digest": "secret-digest",
            },
        ]
        self.outboxes: dict[str, dict[str, object]] = {}
        self.deliveries: dict[str, dict[str, object]] = {}

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> Cursor:
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into agentos_distributed_outbox"):
            outbox_id, tenant_id, principal_id, session_id, run_id, topic, payload = (
                params
            )
            identifier = str(outbox_id)
            if identifier in self.outboxes:
                return Cursor()
            self.outboxes[identifier] = {
                "outbox_id": identifier,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "session_id": session_id,
                "run_id": run_id,
                "topic": topic,
                "payload": json.loads(str(payload)),
            }
            return Cursor([{"outbox_id": identifier}])
        if (
            "from agentos_distributed_a2a_push_configs" in normalized
            and "select 1" in normalized
        ):
            tenant_id, task_id, config_id = params
            exists = any(
                row["tenant_id"] == tenant_id
                and row["task_id"] == task_id
                and row["config_id"] == config_id
                for row in self.configs
            )
            return Cursor([{"exists": 1}] if exists else [])
        if "from agentos_distributed_a2a_tasks as task" in normalized:
            tenant_id, session_id, run_id = params
            return Cursor(
                [
                    row
                    for row in self.configs
                    if row["tenant_id"] == tenant_id
                    and row["session_id"] == session_id
                    and row["run_id"] == run_id
                ],
            )
        if normalized.startswith(
            "insert into agentos_distributed_a2a_push_deliveries",
        ):
            (
                tenant_id,
                principal_id,
                delivery_id,
                outbox_id,
                task_id,
                context_id,
                config_id,
                url,
                authentication_scheme,
                secret_token,
                secret_digest,
                event_id,
                protocol_version,
                status_sequence,
                task_state,
            ) = params
            self.deliveries.setdefault(str(outbox_id), {
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "delivery_id": delivery_id,
                "outbox_id": outbox_id,
                "task_id": task_id,
                "context_id": context_id,
                "config_id": config_id,
                "url": url,
                "authentication_scheme": authentication_scheme,
                "secret_token": secret_token,
                "secret_digest": secret_digest,
                "event_id": event_id,
                "protocol_version": protocol_version,
                "status_sequence": status_sequence,
                "task_state": task_state,
                "failure_count": 0,
                "last_failure_category": None,
                "next_attempt_at": None,
                "attempt_id": None,
                "attempt_owner_id": None,
                "attempt_expires_at": None,
                "created_at": self.now,
                "delivered_at": None,
                "suppressed_at": None,
                "abandoned_at": None,
            })
            return Cursor()
        if "and (status_sequence, created_at, delivery_id) <" in normalized:
            tenant_id, task_id, config_id, sequence, created_at, delivery_id = params
            current_key = (sequence, created_at, delivery_id)
            rows = [
                row
                for row in self.deliveries.values()
                if row["tenant_id"] == tenant_id
                and row["task_id"] == task_id
                and row["config_id"] == config_id
                and row["delivered_at"] is None
                and row["suppressed_at"] is None
                and row["abandoned_at"] is None
                and (
                    row["status_sequence"],
                    row["created_at"],
                    row["delivery_id"],
                )
                < current_key
            ]
            return Cursor(rows[:1])
        if "from agentos_distributed_a2a_push_deliveries" in normalized:
            row = self.deliveries.get(str(params[0]))
            if row is None:
                return Cursor()
            return Cursor([{**row, "database_now": self.now}])
        if normalized.startswith(
            "update agentos_distributed_a2a_push_deliveries",
        ):
            if "set attempt_id =" in normalized:
                attempt_id, worker_id, seconds, outbox_id = params
                row = self.deliveries.get(str(outbox_id))
                if row is None:
                    return Cursor()
                row["attempt_id"] = attempt_id
                row["attempt_owner_id"] = worker_id
                row["attempt_expires_at"] = self.now + timedelta(
                    seconds=float(seconds),
                )
                return Cursor([{**row, "database_now": self.now}])
            outbox_id = str(params[-1])
            row = self.deliveries.get(outbox_id)
            if row is None:
                return Cursor()
            if "set delivered_at =" in normalized:
                row["delivered_at"] = self.now
            elif "set suppressed_at =" in normalized:
                row["suppressed_at"] = self.now
            elif "set failure_count =" in normalized:
                failure_count, category, *middle, _ = params
                row["failure_count"] = failure_count
                row["last_failure_category"] = category
                if "abandoned_at" in normalized:
                    row["abandoned_at"] = self.now
                else:
                    delay = float(middle[0])
                    row["next_attempt_at"] = self.now + timedelta(seconds=delay)
            if any(
                marker in normalized
                for marker in (
                    "set delivered_at =",
                    "set suppressed_at =",
                    "set failure_count =",
                )
            ):
                row["attempt_id"] = None
                row["attempt_owner_id"] = None
                row["attempt_expires_at"] = None
                if "next_attempt_at = null" in normalized:
                    row["next_attempt_at"] = None
            return Cursor()
        raise AssertionError(f"unexpected query: {normalized}")


class Database:
    def __init__(self) -> None:
        self.connection_value = DeliveryConnection()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DeliveryConnection]:
        yield self.connection_value

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[DeliveryConnection]:
        yield self.connection_value


@async_test
async def test_status_outbox_fanout_freezes_delivery_and_is_source_idempotent() -> None:
    connection = DeliveryConnection()

    source_outbox_id = await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id="session_1",
        run_id="run_1",
        source_kind="terminal",
        source_id="run_1:4:completed",
        topic=STATUS_TOPIC,
        payload={"status": "completed", "status_sequence": 4},
    )
    assert len(connection.deliveries) == 1
    delivery = next(iter(connection.deliveries.values()))
    assert delivery["event_id"] == source_outbox_id
    assert delivery["status_sequence"] == 4
    assert delivery["task_state"] == A2ATaskState.COMPLETED.value
    push_outbox = connection.outboxes[str(delivery["outbox_id"])]
    assert push_outbox["topic"] == "agentos.a2a.push"
    assert push_outbox["payload"] == {"delivery_id": delivery["delivery_id"]}
    assert "sealed-token" not in json.dumps(push_outbox)

    connection.configs.append(
        {**connection.configs[0], "config_id": "push_added_later"},
    )
    await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id="session_1",
        run_id="run_1",
        source_kind="terminal",
        source_id="run_1:4:completed",
        topic=STATUS_TOPIC,
        payload={"status": "completed", "status_sequence": 4},
    )
    assert len(connection.deliveries) == 1


@async_test
async def test_delivery_open_attempt_is_fenced_and_ack_is_idempotent() -> None:
    database = Database()
    connection = database.connection_value
    await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id="session_1",
        run_id="run_1",
        source_kind="waiting",
        source_id="run_1:3",
        topic=STATUS_TOPIC,
        payload={
            "status": "waiting",
            "status_sequence": 3,
            "wait_kind": "human_input",
        },
    )
    outbox_id = next(iter(connection.deliveries))
    store = PostgresA2APushDeliveryStore(database)  # type: ignore[arg-type]
    attempt = await store.open_attempt(
        outbox_id=outbox_id,
        worker_id="worker_1",
        ttl=timedelta(seconds=30),
    )

    assert attempt is not None
    target = attempt.target
    assert target.scope == SCOPE
    assert target.url == "https://push.example.test/a2a"
    assert target.secret_ref == ProtectedPayloadRef("sealed-token", "secret-digest")
    assert target.task_state is A2ATaskState.INPUT_REQUIRED
    assert target.delivered_at is None

    assert attempt.attempt_id
    assert attempt.expires_at > DELIVERED_AT

    await attempt.mark_delivered()
    await attempt.mark_delivered()
    assert connection.deliveries[outbox_id]["delivered_at"] == DELIVERED_AT


@pytest.mark.parametrize(
    ("payload", "task_state"),
    [
        ({"status": "queued"}, A2ATaskState.SUBMITTED),
        ({"status": "running"}, A2ATaskState.WORKING),
        (
            {"status": "waiting", "wait_kind": "human_input"},
            A2ATaskState.INPUT_REQUIRED,
        ),
        (
            {"status": "waiting", "wait_kind": "timer"},
            A2ATaskState.WORKING,
        ),
        ({"status": "completed"}, A2ATaskState.COMPLETED),
        ({"status": "failed"}, A2ATaskState.FAILED),
        ({"status": "cancelled"}, A2ATaskState.CANCELED),
    ],
)
@async_test
async def test_status_fanout_maps_all_durable_run_states(
    payload: dict[str, object],
    task_state: A2ATaskState,
) -> None:
    connection = DeliveryConnection()
    await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id="session_1",
        run_id="run_1",
        source_kind=str(payload["status"]),
        source_id=f"run_1:7:{payload['status']}",
        topic=STATUS_TOPIC,
        payload={**payload, "status_sequence": 7},
    )

    delivery = next(iter(connection.deliveries.values()))
    assert delivery["task_state"] == task_state.value
    assert delivery["status_sequence"] == 7


@async_test
async def test_attempt_order_blocks_successor_until_predecessor_is_terminal() -> None:
    database = Database()
    connection = database.connection_value
    for sequence in (1, 2):
        await insert_outbox(
            connection,  # type: ignore[arg-type]
            scope=SCOPE,
            session_id="session_1",
            run_id="run_1",
            source_kind="running",
            source_id=f"run_1:{sequence}",
            topic=STATUS_TOPIC,
            payload={"status": "running", "status_sequence": sequence},
        )
    outbox_ids = list(connection.deliveries)
    store = PostgresA2APushDeliveryStore(database)  # type: ignore[arg-type]

    with pytest.raises(A2APushDeliveryDeferredError):
        await store.open_attempt(
            outbox_id=outbox_ids[1],
            worker_id="worker_2",
            ttl=timedelta(seconds=30),
        )
    first = await store.open_attempt(
        outbox_id=outbox_ids[0],
        worker_id="worker_1",
        ttl=timedelta(seconds=30),
    )
    assert first is not None
    await first.mark_delivered()
    assert await store.open_attempt(
        outbox_id=outbox_ids[1],
        worker_id="worker_2",
        ttl=timedelta(seconds=30),
    )


@async_test
async def test_expired_attempt_takeover_fences_stale_worker() -> None:
    database = Database()
    connection = database.connection_value
    await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id="session_1",
        run_id="run_1",
        source_kind="running",
        source_id="run_1:2",
        topic=STATUS_TOPIC,
        payload={"status": "running", "status_sequence": 2},
    )
    outbox_id = next(iter(connection.deliveries))
    store = PostgresA2APushDeliveryStore(database)  # type: ignore[arg-type]
    stale = await store.open_attempt(
        outbox_id=outbox_id,
        worker_id="worker_1",
        ttl=timedelta(seconds=30),
    )
    assert stale is not None
    with pytest.raises(A2APushDeliveryDeferredError):
        await store.open_attempt(
            outbox_id=outbox_id,
            worker_id="worker_2",
            ttl=timedelta(seconds=30),
        )

    connection.now += timedelta(seconds=31)
    current = await store.open_attempt(
        outbox_id=outbox_id,
        worker_id="worker_2",
        ttl=timedelta(seconds=30),
    )
    assert current is not None
    with pytest.raises(A2APushAttemptFencedError):
        await stale.mark_failed(category=A2APushFailureCategory.NETWORK)


@async_test
async def test_eighth_failure_abandons_delivery_with_exponential_backoff() -> None:
    database = Database()
    connection = database.connection_value
    await insert_outbox(
        connection,  # type: ignore[arg-type]
        scope=SCOPE,
        session_id="session_1",
        run_id="run_1",
        source_kind="running",
        source_id="run_1:2",
        topic=STATUS_TOPIC,
        payload={"status": "running", "status_sequence": 2},
    )
    outbox_id = next(iter(connection.deliveries))
    store = PostgresA2APushDeliveryStore(database)  # type: ignore[arg-type]

    for failure_count in range(1, 9):
        attempt = await store.open_attempt(
            outbox_id=outbox_id,
            worker_id=f"worker_{failure_count}",
            ttl=timedelta(seconds=30),
        )
        assert attempt is not None
        resolution = await attempt.mark_failed(
            category=A2APushFailureCategory.NETWORK,
        )
        row = connection.deliveries[outbox_id]
        assert row["failure_count"] == failure_count
        if failure_count < 8:
            assert resolution is A2APushAttemptResolution.RETRY_PENDING
            assert row["next_attempt_at"] == connection.now + timedelta(
                seconds=min(2**failure_count, 300),
            )
            with pytest.raises(A2APushDeliveryDeferredError):
                await store.open_attempt(
                    outbox_id=outbox_id,
                    worker_id="early_worker",
                    ttl=timedelta(seconds=30),
                )
            connection.now = row["next_attempt_at"]  # type: ignore[assignment]
        else:
            assert resolution is A2APushAttemptResolution.ACK_SAFE
            assert row["abandoned_at"] == connection.now
            assert row["next_attempt_at"] is None
            assert await store.open_attempt(
                outbox_id=outbox_id,
                worker_id="late_worker",
                ttl=timedelta(seconds=30),
            ) is None


def test_runtime_schema_contains_immutable_push_delivery_truth() -> None:
    from agentos.distributed.postgres.schema import SCHEMA_STATEMENTS

    schema = " ".join("\n".join(SCHEMA_STATEMENTS).lower().split())
    assert "agentos_distributed_a2a_push_deliveries" in schema
    assert "unique (outbox_id)" in schema
    assert "task_state" in schema
    assert "delivered_at" in schema
