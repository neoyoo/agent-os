from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.channels.a2a_operations import (
    A2APushNotificationAuthentication,
    A2APushNotificationConfig,
    A2APushNotificationConfigError,
    A2APushNotificationDelivery,
    A2APushNotificationDeliveryRecord,
    A2APushNotificationRetryPolicy,
    A2ATask,
    A2ATaskSubscriptionEvent,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self) -> None:
        self.configs: dict[tuple[str, str], dict[str, object]] = {}
        self.deliveries: dict[str, dict[str, object]] = {}
        self.sql: list[str] = []
        self.commits = 0

    def execute(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> FakeCursor:
        self.sql.append(sql)
        if "INSERT INTO agentos_a2a_push_notification_configs" in sql:
            payload = json.loads(str(params[4]))
            self.configs[(str(params[0]), str(params[1]))] = {
                "task_id": params[0],
                "config_id": params[1],
                "url": params[2],
                "payload": payload,
            }
            return FakeCursor()
        if (
            "SELECT payload FROM agentos_a2a_push_notification_configs" in sql
            and "config_id = %s" in sql
        ):
            row = self.configs.get((str(params[0]), str(params[1])))
            return FakeCursor([(row["payload"],)] if row else [])
        if "SELECT payload FROM agentos_a2a_push_notification_configs" in sql:
            rows = [
                (row["payload"],)
                for (task_id, _config_id), row in sorted(self.configs.items())
                if task_id == str(params[0])
            ]
            return FakeCursor(rows)
        if "DELETE FROM agentos_a2a_push_notification_configs" in sql:
            removed = self.configs.pop((str(params[0]), str(params[1])), None)
            return FakeCursor([(params[1],)] if removed else [])
        if "INSERT INTO agentos_a2a_push_notification_deliveries" in sql:
            payload = json.loads(str(params[11]))
            self.deliveries[str(params[0])] = {
                "delivery_id": params[0],
                "task_id": params[1],
                "config_id": params[2],
                "status": params[3],
                "next_run_at": params[4],
                "created_at": params[5],
                "attempts": params[6],
                "worker_id": params[7],
                "lease_expires_at": params[8],
                "delivered_at": params[9],
                "dead_lettered_at": params[10],
                "payload": payload,
            }
            return FakeCursor()
        if (
            "SELECT payload FROM agentos_a2a_push_notification_deliveries" in sql
            and "delivery_id = %s" in sql
        ):
            row = self.deliveries.get(str(params[0]))
            return FakeCursor([(row["payload"],)] if row else [])
        if "FOR UPDATE SKIP LOCKED" in sql:
            now = float(params[0])
            limit = int(params[2])
            rows = []
            for row in sorted(
                self.deliveries.values(),
                key=lambda item: (
                    float(item["next_run_at"]),
                    float(item["created_at"]),
                    str(item["delivery_id"]),
                ),
            ):
                due_waiting = (
                    row["status"] in {"queued", "retry_scheduled"}
                    and float(row["next_run_at"]) <= now
                )
                expired_running = (
                    row["status"] == "running"
                    and row["lease_expires_at"] is not None
                    and float(row["lease_expires_at"]) <= now
                )
                if due_waiting or expired_running:
                    rows.append((row["payload"],))
                if len(rows) >= limit:
                    break
            return FakeCursor(rows)
        if "UPDATE agentos_a2a_push_notification_deliveries" in sql:
            payload = json.loads(str(params[7]))
            row = self.deliveries.get(str(params[8]))
            if row is None:
                return FakeCursor()
            row.update(
                {
                    "status": params[0],
                    "next_run_at": params[1],
                    "attempts": params[2],
                    "worker_id": params[3],
                    "lease_expires_at": params[4],
                    "delivered_at": params[5],
                    "dead_lettered_at": params[6],
                    "payload": payload,
                },
            )
            return FakeCursor([(payload,)])
        if "SELECT payload FROM agentos_a2a_push_notification_deliveries" in sql:
            rows = []
            for row in sorted(
                self.deliveries.values(),
                key=lambda item: (
                    float(item["next_run_at"]),
                    float(item["created_at"]),
                    str(item["delivery_id"]),
                ),
            ):
                if params and row["status"] != params[0]:
                    continue
                rows.append((row["payload"],))
            return FakeCursor(rows)
        return FakeCursor()

    def commit(self) -> None:
        self.commits += 1


def push_config(config_id: str = "cfg_1") -> A2APushNotificationConfig:
    return A2APushNotificationConfig(
        config_id=config_id,
        url=f"https://client.example/{config_id}",
        token="opaque-token",
        authentication=A2APushNotificationAuthentication(
            schemes=("Bearer",),
            credentials="secret-token",
        ),
    )


def push_event(event_id: str = "7") -> A2ATaskSubscriptionEvent:
    return A2ATaskSubscriptionEvent(
        event_id=event_id,
        task=A2ATask(task_id="task_1", context_id="ctx_1", state="working"),
    )


def delivery_record(
    delivery_id: str = "delivery_1",
    *,
    next_run_at: float = 5.0,
) -> A2APushNotificationDeliveryRecord:
    return A2APushNotificationDeliveryRecord(
        delivery_id=delivery_id,
        task_id="task_1",
        config=push_config(),
        event=push_event(),
        created_at=1.0,
        next_run_at=next_run_at,
    )


def test_postgres_a2a_push_notification_config_store_round_trips_configs() -> None:
    from agentos.channels.a2a_operations import (
        PostgresA2APushNotificationConfigStore,
    )

    connection = FakeConnection()
    store = PostgresA2APushNotificationConfigStore(
        dsn="postgresql://unused",
        connection=connection,
    )

    created = store.create("task_1", push_config())
    fetched = store.get("task_1", "cfg_1")
    listed = store.list("task_1")
    deleted = store.delete("task_1", "cfg_1")

    assert created == push_config()
    assert fetched == push_config()
    assert listed == (push_config(),)
    assert deleted is True
    assert store.delete("task_1", "cfg_1") is False
    assert connection.commits == 3
    joined_sql = "\n".join(connection.sql)
    assert "ON CONFLICT (task_id, config_id) DO UPDATE" in joined_sql
    assert "payload = EXCLUDED.payload" in joined_sql


def test_postgres_a2a_push_notification_config_store_enforces_url_policy_before_insert() -> None:
    from agentos.channels.a2a_operations import (
        HostAllowListA2APushNotificationUrlPolicy,
        PostgresA2APushNotificationConfigStore,
    )

    connection = FakeConnection()
    store = PostgresA2APushNotificationConfigStore(
        dsn="postgresql://unused",
        connection=connection,
        url_policy=HostAllowListA2APushNotificationUrlPolicy(
            allowed_hosts=("client.example",),
        ),
    )

    with pytest.raises(A2APushNotificationConfigError):
        store.create(
            "task_1",
            A2APushNotificationConfig(
                config_id="cfg_blocked",
                url="https://other.example/webhook",
            ),
        )

    assert connection.configs == {}
    assert connection.commits == 0


def test_postgres_a2a_push_notification_delivery_store_claims_due_with_locking() -> None:
    from agentos.channels.a2a_operations import (
        PostgresA2APushNotificationDeliveryStore,
    )

    connection = FakeConnection()
    store = PostgresA2APushNotificationDeliveryStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.enqueue(delivery_record("delivery_2", next_run_at=10.0))
    store.enqueue(delivery_record("delivery_1", next_run_at=5.0))

    early = store.claim_due(
        now=4.9,
        worker_id="worker-a",
        lease_seconds=30,
        limit=10,
    )
    claimed = store.claim_due(
        now=5.0,
        worker_id="worker-a",
        lease_seconds=30,
        limit=10,
    )

    assert early == ()
    assert len(claimed) == 1
    assert claimed[0].delivery_id == "delivery_1"
    assert claimed[0].status == "running"
    assert claimed[0].worker_id == "worker-a"
    assert claimed[0].lease_expires_at == 35.0
    assert store.get("delivery_1") == claimed[0]
    assert store.list_records(status="running") == claimed
    assert "FOR UPDATE SKIP LOCKED" in "\n".join(connection.sql)


def test_postgres_a2a_push_notification_delivery_store_records_outcomes() -> None:
    from agentos.channels.a2a_operations import (
        PostgresA2APushNotificationDeliveryStore,
    )

    connection = FakeConnection()
    store = PostgresA2APushNotificationDeliveryStore(
        dsn="postgresql://unused",
        connection=connection,
    )
    store.enqueue(delivery_record())
    store.claim_due(now=5.0, worker_id="worker-a", lease_seconds=30, limit=1)

    retry = store.record_failure(
        "delivery_1",
        delivery=A2APushNotificationDelivery(
            config_id="cfg_1",
            url="https://client.example/cfg_1",
            status="failed",
            error="temporary outage",
        ),
        now=5.0,
        retry_policy=A2APushNotificationRetryPolicy(
            max_attempts=2,
            backoff_seconds=10,
        ),
    )
    store.claim_due(now=15.0, worker_id="worker-b", lease_seconds=30, limit=1)
    dead_letter = store.record_failure(
        "delivery_1",
        delivery=A2APushNotificationDelivery(
            config_id="cfg_1",
            url="https://client.example/cfg_1",
            status="failed",
            error="still failing",
        ),
        now=15.0,
        retry_policy=A2APushNotificationRetryPolicy(
            max_attempts=2,
            backoff_seconds=10,
        ),
    )

    assert retry is not None
    assert retry.status == "retry_scheduled"
    assert retry.attempts == 1
    assert retry.next_run_at == 15.0
    assert dead_letter is not None
    assert dead_letter.status == "dead_letter"
    assert dead_letter.attempts == 2
    assert dead_letter.dead_lettered_at == 15.0


def test_postgres_a2a_push_notification_delivery_store_marks_delivered() -> None:
    from agentos.channels.a2a_operations import (
        PostgresA2APushNotificationDeliveryStore,
    )

    store = PostgresA2APushNotificationDeliveryStore(
        dsn="postgresql://unused",
        connection=FakeConnection(),
    )
    store.enqueue(delivery_record())
    store.claim_due(now=5.0, worker_id="worker-a", lease_seconds=30, limit=1)

    delivered = store.mark_delivered(
        "delivery_1",
        delivery=A2APushNotificationDelivery(
            config_id="cfg_1",
            url="https://client.example/cfg_1",
            status="delivered",
            response={"status": "accepted"},
        ),
        now=5.0,
    )

    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.attempts == 1
    assert delivered.response == {"status": "accepted"}
    assert delivered.delivered_at == 5.0


def test_postgres_a2a_push_notification_migration_defines_tables_and_indexes() -> None:
    migration = Path(
        "docs/migrations/2026-06-15-postgres-a2a-push-notifications.sql",
    ).read_text(encoding="utf-8")

    assert "-- migrate:up" in migration
    assert "-- migrate:down" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_a2a_push_notification_configs" in migration
    assert "CREATE TABLE IF NOT EXISTS agentos_a2a_push_notification_deliveries" in migration
    assert "PRIMARY KEY (task_id, config_id)" in migration
    assert "PRIMARY KEY (delivery_id)" in migration
    assert "payload JSONB NOT NULL" in migration
    assert "agentos_a2a_push_notification_deliveries_due_idx" in migration
