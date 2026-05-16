from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from uuid import uuid4

import pytest

from agentos.multi import AgentEnvelope, TaskRecord, TaskRequest, TaskResult
from agentos.multi.postgres_tasks import PostgresTaskStore
from agentos.multi.reconciler import OutboxReconciler
from agentos.multi.redis_queue import RedisAgentMessageQueue


pytestmark = pytest.mark.integration


def _require_live_backends() -> tuple[str, str]:
    if not os.environ.get("AGENTOS_RUN_INTEGRATION"):
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 with docker-compose.test.yml services")
    postgres_dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("set AGENTOS_TEST_POSTGRES_DSN and AGENTOS_TEST_REDIS_URL")
    return postgres_dsn, redis_url


def _connect_postgres(dsn: str):
    try:
        import psycopg
    except ImportError as error:
        pytest.skip(f"install postgres extra to run live integration tests: {error}")
    return psycopg.connect(dsn)


def _connect_redis(url: str):
    try:
        import redis
    except ImportError as error:
        pytest.skip(f"install redis extra to run live integration tests: {error}")
    return redis.Redis.from_url(url)


def _migration_parts() -> tuple[str, str]:
    migration = Path(
        "docs/migrations/2026-05-16-postgres-multi-agent-tasks.sql",
    ).read_text()
    up_part = migration.split("-- migrate:up", maxsplit=1)[1].split(
        "-- migrate:down",
        maxsplit=1,
    )[0]
    down_part = migration.split("-- migrate:down", maxsplit=1)[1]
    return up_part, down_part


def _reset_postgres_schema(dsn: str) -> None:
    up_part, down_part = _migration_parts()
    with _connect_postgres(dsn) as connection:
        connection.execute(down_part)
        connection.execute(up_part)
        connection.commit()


def _store(dsn: str) -> PostgresTaskStore:
    return PostgresTaskStore(dsn=dsn, connection=_connect_postgres(dsn))


def _record(task_id: str = "task_1") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        mode="dispatch",
        parent_agent_id="parent",
        target_agent_id="worker",
        request=TaskRequest(task_id=task_id, instruction="Do work"),
        status="queued",
        created_at=1.0,
        deadline_at=300.0,
    )


def test_live_postgres_concurrent_claim_assigns_one_worker() -> None:
    postgres_dsn, _redis_url = _require_live_backends()
    _reset_postgres_schema(postgres_dsn)
    setup_store = _store(postgres_dsn)
    setup_store.create(_record())
    setup_store.close()

    def claim(worker_id: str) -> int:
        store = _store(postgres_dsn)
        try:
            return len(
                store.claim_queued(
                    worker_id=worker_id,
                    capabilities=("code",),
                    limit=1,
                    lease_expires_at=60.0,
                    now=2.0,
                ),
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker_1", "worker_2"]))

    assert sum(results) == 1
    verify_store = _store(postgres_dsn)
    try:
        stored = verify_store.get("task_1")
        assert stored is not None
        assert stored.status == "running"
        assert stored.worker_id in {"worker_1", "worker_2"}
        assert stored.attempt == 1
    finally:
        verify_store.close()


def test_live_postgres_cancel_ack_and_completion_race_converges_once() -> None:
    postgres_dsn, _redis_url = _require_live_backends()
    _reset_postgres_schema(postgres_dsn)
    setup_store = _store(postgres_dsn)
    setup_store.create(_record())
    setup_store.claim_queued(
        worker_id="worker_1",
        capabilities=("code",),
        limit=1,
        lease_expires_at=60.0,
        now=2.0,
    )
    assert setup_store.request_cancel("task_1", now=2.5)
    setup_store.close()

    def ack_cancelled() -> bool:
        store = _store(postgres_dsn)
        try:
            return store.ack_cancelled(
                "task_1",
                TaskResult(task_id="task_1", status="cancelled", summary="cancelled"),
                now=3.0,
                worker_id="worker_1",
                attempt=1,
            )
        finally:
            store.close()

    def complete() -> bool:
        store = _store(postgres_dsn)
        try:
            return store.mark_completed(
                "task_1",
                TaskResult(task_id="task_1", status="completed", summary="done"),
                now=3.0,
                worker_id="worker_1",
                attempt=1,
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result() for future in (executor.submit(ack_cancelled), executor.submit(complete))]

    assert results.count(True) == 1
    verify_store = _store(postgres_dsn)
    try:
        stored = verify_store.get("task_1")
        assert stored is not None
        assert stored.status in {"cancelled", "completed"}
        assert stored.result is not None
    finally:
        verify_store.close()


def test_live_postgres_outbox_delivers_result_to_redis_stream() -> None:
    postgres_dsn, redis_url = _require_live_backends()
    _reset_postgres_schema(postgres_dsn)
    key_prefix = f"agentos-test-{uuid4().hex}"
    redis_client = _connect_redis(redis_url)
    store = _store(postgres_dsn)
    queue = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="parent-consumer",
    )
    try:
        queue.create_inbox("parent")
        store.create(_record())
        store.claim_queued(
            worker_id="worker_1",
            capabilities=("code",),
            limit=1,
            lease_expires_at=60.0,
            now=2.0,
        )
        assert store.mark_completed(
            "task_1",
            TaskResult(task_id="task_1", status="completed", summary="done"),
            now=3.0,
            worker_id="worker_1",
            attempt=1,
        )

        delivered = OutboxReconciler(
            task_store=store,
            message_queue=queue,
            batch_size=10,
        ).run_once(now=4.0)

        deliveries = queue.collect("parent")
        assert delivered == 1
        assert len(deliveries) == 1
        assert deliveries[0].envelope.type == "task_result"
        assert deliveries[0].envelope.payload == TaskResult(
            task_id="task_1",
            status="completed",
            summary="done",
        )
    finally:
        redis_client.delete(f"{key_prefix}:multi:inbox:parent")
        store.close()


def test_live_redis_stream_pending_message_can_be_reclaimed() -> None:
    _postgres_dsn, redis_url = _require_live_backends()
    key_prefix = f"agentos-test-{uuid4().hex}"
    redis_client = _connect_redis(redis_url)
    stream_key = f"{key_prefix}:multi:inbox:worker"
    producer = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="consumer_1",
    )
    reclaimer = RedisAgentMessageQueue(
        redis_url,
        client=redis_client,
        key_prefix=key_prefix,
        consumer_name="consumer_2",
    )
    try:
        producer.create_inbox("worker")
        producer.send(
            AgentEnvelope(
                envelope_id="env_1",
                from_agent_id="parent",
                to_agent_id="worker",
                type="task_request",
                payload=TaskRequest(task_id="task_1", instruction="work"),
                created_at=1.0,
                correlation_id="task_1",
            ),
        )
        assert producer.collect("worker")

        deliveries = reclaimer.reclaim_pending(
            "worker",
            idle_threshold_ms=0,
            max_retries=3,
        )

        assert [delivery.delivery_id for delivery in deliveries]
        assert deliveries[0].envelope.correlation_id == "task_1"
        assert reclaimer.ack("worker", deliveries[0].delivery_id)
    finally:
        redis_client.delete(stream_key, f"{stream_key}:dead")
