from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from importlib import import_module
import os
from uuid import uuid4

import pytest

from agentos.distributed.migrations.service import (
    DistributedMigrationService,
    canonical_migration_plan,
)
from agentos.distributed.models import OutboxRecord, RequestScope, RunSubmission
from agentos.distributed.postgres._database import (
    PostgresPool,
    Row,
    fetchall,
    fetchone,
)
from agentos.distributed.postgres._outbox_records import (
    EXECUTION_TOPIC,
    STATUS_TOPIC,
)
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.worker.relay import OutboxRelay


pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _Settings:
    postgres_dsn: str
    redis_url: str


class _PublishBeforeCrash:
    async def publish(self, *, record: OutboxRecord) -> str:
        del record
        raise RuntimeError("injected crash before XADD")


class _PublishAfterCrash:
    def __init__(self, queue: RedisQueueAdapter) -> None:
        self._queue = queue

    async def publish(self, *, record: OutboxRecord) -> str:
        await self._queue.publish(record=record)
        raise RuntimeError("injected crash after XADD")


@dataclass(slots=True)
class _PublishBarrier:
    arrivals: int = 0
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> None:
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await self.ready.wait()


@dataclass(frozen=True, slots=True)
class _BarrierPublishQueue:
    delegate: RedisQueueAdapter
    barrier: _PublishBarrier

    async def publish(self, *, record: OutboxRecord) -> str:
        await self.barrier.wait()
        return await self.delegate.publish(record=record)


def _settings() -> _Settings:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    postgres_dsn = os.environ.get("AGENTOS_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("set PostgreSQL and Redis live integration settings")
    return _Settings(postgres_dsn, redis_url)


@pytest.mark.parametrize("window", ("before_xadd", "after_xadd"))
def test_live_outbox_recovery_deduplicates_publish_and_acks_after_commit(
    window: str,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_outbox_window(window))


async def _verify_outbox_window(window: str) -> None:
    settings = _settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_outbox_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-outbox-live-{suffix}"
    pool = await PostgresPool.open(settings.postgres_dsn, min_size=0, max_size=3)
    queue = RedisQueueAdapter(
        settings.redis_url,
        key_prefix=key_prefix,
        group_name="workers",
    )
    try:
        await DistributedMigrationService(
            port=PostgresMigrationPort(pool),
            plan=canonical_migration_plan(),
        ).apply()
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "recover relay crash window",
            ),
        )
        outbox_id = await _keep_execution_outbox(
            pool,
            scope,
            session_id,
            receipt.run_id,
        )
        outbox = PostgresOutboxStore(pool)
        crashing_queue = (
            _PublishBeforeCrash()
            if window == "before_xadd"
            else _PublishAfterCrash(queue)
        )
        crashing_relay = OutboxRelay(
            outbox=outbox,
            queue=crashing_queue,  # type: ignore[arg-type]
            owner_id=f"crashed_relay_{suffix}",
            batch_size=1,
            claim_ttl=timedelta(seconds=30),
        )

        with pytest.raises(RuntimeError, match=f"crash {window.split('_')[0]}"):
            await crashing_relay.relay_once()
        crashed = await _outbox_row(pool, outbox_id)
        assert crashed["published_at"] is None
        assert crashed["claim_id"] is None
        assert crashed["publish_attempts"] == 1

        recovered_relay = OutboxRelay(
            outbox=outbox,
            queue=queue,
            owner_id=f"recovered_relay_{suffix}",
            batch_size=1,
            claim_ttl=timedelta(seconds=30),
        )
        assert await recovered_relay.relay_once() == 1
        recovered = await _outbox_row(pool, outbox_id)
        assert recovered["published_at"] is not None
        assert recovered["claim_id"] is None
        assert recovered["publish_attempts"] == 2

        deliveries = await queue.receive(
            topic=EXECUTION_TOPIC,
            consumer_id="worker_recovered",
            limit=2,
        )
        assert len(deliveries) == 1
        assert deliveries[0].outbox_id == outbox_id
        await queue.ack(topic=EXECUTION_TOPIC, delivery=deliveries[0])
        assert await queue.reclaim(
            topic=EXECUTION_TOPIC,
            consumer_id="worker_recovered",
            min_idle=timedelta(milliseconds=1),
            limit=2,
        ) == ()
    finally:
        await queue.close()
        await _cleanup_redis(settings.redis_url, key_prefix)
        await _cleanup_postgres(pool, scope.tenant_id)
        await pool.close()


def test_live_two_relays_claim_distinct_outbox_records() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_two_relays())


async def _verify_two_relays() -> None:
    settings = _settings()
    suffix = uuid4().hex
    scopes = tuple(
        RequestScope(f"tenant_relay_{index}_{suffix}", "principal_1")
        for index in (1, 2)
    )
    session_ids = tuple(f"session_{index}_{suffix}" for index in (1, 2))
    key_prefix = f"agentos-relay-race-{suffix}"
    pool = await PostgresPool.open(settings.postgres_dsn, min_size=0, max_size=4)
    queues = tuple(
        RedisQueueAdapter(
            settings.redis_url,
            key_prefix=key_prefix,
            group_name="workers",
        )
        for _ in (1, 2)
    )
    try:
        await DistributedMigrationService(
            port=PostgresMigrationPort(pool),
            plan=canonical_migration_plan(),
        ).apply()
        outbox_ids: list[str] = []
        state = PostgresStateStore(pool)
        for index, (scope, session_id) in enumerate(
            zip(scopes, session_ids, strict=True),
            start=1,
        ):
            receipt = await state.submit(
                scope=scope,
                submission=RunSubmission(
                    session_id,
                    f"submission_{index}_{suffix}",
                    f"relay request {index}",
                ),
            )
            outbox_ids.append(
                await _keep_execution_outbox(
                    pool,
                    scope,
                    session_id,
                    receipt.run_id,
                ),
            )

        barrier = _PublishBarrier()
        outbox = PostgresOutboxStore(pool)
        relays = tuple(
            OutboxRelay(
                outbox=outbox,
                queue=_BarrierPublishQueue(queue, barrier),
                owner_id=f"relay_{index}_{suffix}",
                batch_size=1,
                claim_ttl=timedelta(seconds=30),
            )
            for index, queue in enumerate(queues, start=1)
        )
        assert await asyncio.gather(*(relay.relay_once() for relay in relays)) == [
            1,
            1,
        ]
        assert barrier.arrivals == 2
        assert await _published_rows(pool, tuple(outbox_ids)) == {
            outbox_id: 1 for outbox_id in outbox_ids
        }

        deliveries = await queues[0].receive(
            topic=EXECUTION_TOPIC,
            consumer_id=f"worker_{suffix}",
            limit=4,
        )
        assert {item.outbox_id for item in deliveries} == set(outbox_ids)
        for delivery in deliveries:
            await queues[0].ack(topic=EXECUTION_TOPIC, delivery=delivery)
        for relay in relays:
            await relay.close()
    finally:
        for queue in queues:
            await queue.close()
        await _cleanup_redis(settings.redis_url, key_prefix)
        for scope in scopes:
            await _cleanup_postgres(pool, scope.tenant_id)
        await pool.close()


async def _keep_execution_outbox(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> str:
    async with pool.transaction() as connection:
        await connection.execute(
            """
            DELETE FROM agentos_distributed_outbox
            WHERE tenant_id = %s AND run_id = %s AND topic = %s
            """,
            (scope.tenant_id, run_id, STATUS_TOPIC),
        )
        row = await fetchone(
            connection,
            """
            UPDATE agentos_distributed_outbox AS target
            SET created_at = COALESCE(
                (
                    SELECT MIN(candidate.created_at) - interval '1 second'
                    FROM agentos_distributed_outbox AS candidate
                    WHERE candidate.published_at IS NULL
                      AND candidate.outbox_id <> target.outbox_id
                ),
                target.created_at
            )
            WHERE target.tenant_id = %s AND target.session_id = %s
              AND target.run_id = %s AND target.topic = %s
            RETURNING target.outbox_id
            """,
            (scope.tenant_id, session_id, run_id, EXECUTION_TOPIC),
        )
    assert row is not None
    return str(row["outbox_id"])


async def _outbox_row(pool: PostgresPool, outbox_id: str) -> Row:
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT published_at, claim_id, publish_attempts
            FROM agentos_distributed_outbox WHERE outbox_id = %s
            """,
            (outbox_id,),
        )
    assert row is not None
    return row


async def _published_rows(
    pool: PostgresPool,
    outbox_ids: tuple[str, ...],
) -> dict[str, int]:
    async with pool.connection() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT outbox_id, published_at, claim_id, queue_entry_id,
                   publish_attempts
            FROM agentos_distributed_outbox
            WHERE outbox_id = ANY(%s)
            """,
            (list(outbox_ids),),
        )
    assert len(rows) == len(outbox_ids)
    assert all(row["published_at"] is not None for row in rows)
    assert all(row["claim_id"] is None for row in rows)
    assert len({row["queue_entry_id"] for row in rows}) == len(outbox_ids)
    return {
        str(row["outbox_id"]): int(row["publish_attempts"])
        for row in rows
    }


async def _cleanup_postgres(pool: PostgresPool, tenant_id: str) -> None:
    async with pool.transaction() as connection:
        for table in (
            "agentos_distributed_side_effects",
            "agentos_distributed_execution_cursors",
            "agentos_distributed_reconciliation_sources",
            "agentos_distributed_checkpoints",
            "agentos_distributed_outbox",
            "agentos_distributed_commands",
            "agentos_distributed_accepted_inputs",
            "agentos_distributed_submissions",
            "agentos_distributed_runs",
            "agentos_distributed_sessions",
        ):
            await connection.execute(
                f"DELETE FROM {table} WHERE tenant_id = %s",
                (tenant_id,),
            )


async def _cleanup_redis(redis_url: str, key_prefix: str) -> None:
    redis_asyncio = import_module("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url)
    try:
        keys = [key async for key in client.scan_iter(match=f"{key_prefix}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()
