from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from agentos.distributed.errors import DistributedError
from agentos.distributed.models import (
    OutboxClaim,
    OutboxRecord,
    RequestScope,
    RunSubmission,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.worker.relay import OutboxRelay
from tests.integration._backend_restart_support import (
    DockerTestService,
    cleanup_redis,
    prioritize_outbox,
)
from tests.integration._distributed_failure_support import (
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
)
from tests.integration._network_timeout_support import (
    Backend,
    IO_TIMEOUT,
    NetworkTimeoutResources,
    receive_one,
    redis_url,
    wait_for_backend,
)


pytestmark = pytest.mark.integration


class _GatedPublishQueue:
    def __init__(self, delegate: RedisQueueAdapter) -> None:
        self._delegate = delegate
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def publish(self, *, record: OutboxRecord) -> str:
        self.started.set()
        await self.release.wait()
        return await self._delegate.publish(record=record)


class _GatedMarkOutbox:
    def __init__(self, delegate: PostgresOutboxStore) -> None:
        self._delegate = delegate
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def claim_batch(
        self,
        *,
        owner_id: str,
        limit: int,
        ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]:
        return await self._delegate.claim_batch(
            owner_id=owner_id,
            limit=limit,
            ttl=ttl,
        )

    async def mark_published(
        self,
        *,
        claim: OutboxClaim,
        queue_entry_id: str,
    ) -> None:
        self.started.set()
        await self.release.wait()
        await self._delegate.mark_published(
            claim=claim,
            queue_entry_id=queue_entry_id,
        )

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        await self._delegate.release_claim(claim=claim)


@pytest.mark.parametrize("backend", ["redis", "postgres"])
def test_live_relay_backend_timeout_releases_or_redrives_authoritative_outbox(
    backend: Backend,
) -> None:
    environment = (
        "AGENTOS_TEST_REDIS_CONTAINER"
        if backend == "redis"
        else "AGENTOS_TEST_POSTGRES_CONTAINER"
    )
    service = DockerTestService.from_environment(environment)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_relay_backend_timeout(backend, service))


async def _verify_relay_backend_timeout(
    backend: Backend,
    service: DockerTestService,
) -> None:
    settings = live_postgres_settings()
    selected_redis_url = redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_relay_{backend}_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-relay-{backend}-{suffix}"
    pool = await open_migrated_pool(
        settings.dsn,
        operation_timeout=IO_TIMEOUT,
    )
    resources = NetworkTimeoutResources(
        pool,
        selected_redis_url,
        key_prefix,
        suffix,
    )
    publish_gate = _GatedPublishQueue(resources.queue)
    mark_gate = _GatedMarkOutbox(PostgresOutboxStore(pool))
    relay = OutboxRelay(
        outbox=(
            PostgresOutboxStore(pool) if backend == "redis" else mark_gate
        ),  # type: ignore[arg-type]
        queue=(
            publish_gate if backend == "redis" else resources.queue
        ),  # type: ignore[arg-type]
        owner_id=f"fault_relay_{suffix}",
        batch_size=1,
        claim_ttl=timedelta(milliseconds=300),
        batch_timeout=timedelta(seconds=1),
    )
    active: asyncio.Task[int] | None = None
    closing: asyncio.Task[None] | None = None
    paused = False
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                f"recover relay after {backend} timeout",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await prioritize_outbox(pool, outbox_id)
        active = asyncio.create_task(relay.relay_once())
        gate = publish_gate if backend == "redis" else mark_gate
        await asyncio.wait_for(gate.started.wait(), timeout=2)

        service.pause()
        paused = True
        gate.release.set()
        closing = asyncio.create_task(relay.close())

        with pytest.raises(DistributedError):
            await asyncio.wait_for(active, timeout=2)
        await asyncio.wait_for(closing, timeout=2)

        service.unpause()
        paused = False
        await wait_for_backend(backend, selected_redis_url, pool)
        assert await _outbox_published_at(pool, outbox_id) is None
        if backend == "postgres":
            await asyncio.sleep(0.35)
        await prioritize_outbox(pool, outbox_id)
        assert await resources.relay.relay_once() == 1
        assert await _outbox_published_at(pool, outbox_id) is not None
        delivery = await receive_one(
            resources.queue,
            f"relay_recovery_{suffix}",
        )
        assert delivery.outbox_id == outbox_id
        await resources.queue.ack(topic=EXECUTION_TOPIC, delivery=delivery)
    finally:
        publish_gate.release.set()
        mark_gate.release.set()
        if active is not None and not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        if closing is not None and not closing.done():
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
        if paused:
            service.unpause()
            await wait_for_backend(backend, selected_redis_url, pool)
        await resources.close()
        await cleanup_redis(selected_redis_url, key_prefix)
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


async def _outbox_published_at(
    pool: PostgresPool,
    outbox_id: str,
) -> object:
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT published_at FROM agentos_distributed_outbox
            WHERE outbox_id = %s
            """,
            (outbox_id,),
        )
    assert row is not None
    return row["published_at"]
