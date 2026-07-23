from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.distributed.errors import DistributedBackendUnavailableError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.leases import RedisLeaseAdapter
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter
from agentos.distributed.worker.relay import OutboxRelay
from agentos.distributed.worker.runner import WorkerRunner
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime.run_state import RunStatus
from agentos.security import FernetPayloadProtector
from tests.integration._backend_restart_support import (
    DockerTestService,
    cleanup_redis,
    prioritize_outbox,
    wait_for_postgres,
    wait_for_redis,
)
from tests.integration._distributed_failure_support import (
    NoopBlobStore,
    claim_scoped_agent_factory,
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
)


pytestmark = pytest.mark.integration


class _RecoveryProvider:
    def __init__(self, content: str) -> None:
        self._content = content
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(self._content)


def test_live_redis_restart_redrives_published_postgres_outbox() -> None:
    service = DockerTestService.from_environment("AGENTOS_TEST_REDIS_CONTAINER")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_redis_restart(service))


async def _verify_redis_restart(service: DockerTestService) -> None:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_redis_restart_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-redis-restart-{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    resources = _Resources(pool, redis_url, key_prefix, suffix)
    try:
        state = PostgresStateStore(pool)
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "recover the PostgreSQL outbox after Redis restart",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await prioritize_outbox(pool, outbox_id)
        assert await resources.relay.relay_once() == 1
        await _assert_outbox_attempts(pool, outbox_id, attempts=1)

        service.kill()
        try:
            await _assert_queued_truth(pool, scope, session_id, receipt.run_id)
        finally:
            service.start()
            await wait_for_redis(redis_url)

        await _redrive_outbox(resources.relay, pool, outbox_id)
        delivery = await _receive_one(resources.queue, f"worker_{suffix}")
        provider = _RecoveryProvider("recovered after Redis restart")
        worker = resources.worker(provider, f"worker_{suffix}")
        assert await worker.run_delivery(delivery) is True
        assert len(provider.requests) == 1
        await _assert_completed(
            state,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            content="recovered after Redis restart",
        )
    finally:
        await resources.close()
        await cleanup_redis(redis_url, key_prefix)
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def test_live_postgres_restart_keeps_redis_delivery_pending() -> None:
    service = DockerTestService.from_environment("AGENTOS_TEST_POSTGRES_CONTAINER")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_postgres_restart(service))


async def _verify_postgres_restart(service: DockerTestService) -> None:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_postgres_restart_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-postgres-restart-{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    resources = _Resources(pool, redis_url, key_prefix, suffix)
    provider = _RecoveryProvider("recovered after PostgreSQL restart")
    try:
        state = PostgresStateStore(pool)
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "keep delivery pending while PostgreSQL is unavailable",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await prioritize_outbox(pool, outbox_id)
        assert await resources.relay.relay_once() == 1
        delivery = await _receive_one(resources.queue, f"worker_failed_{suffix}")
        worker = resources.worker(provider, f"worker_failed_{suffix}")

        service.kill()
        try:
            with pytest.raises(DistributedBackendUnavailableError):
                await worker.run_delivery(delivery)
            assert provider.requests == []
        finally:
            service.start()
            await wait_for_postgres(pool)

        reclaimed = await _reclaim_one(resources.queue, f"worker_recovered_{suffix}")
        assert reclaimed.outbox_id == outbox_id
        recovered_worker = resources.worker(provider, f"worker_recovered_{suffix}")
        assert await recovered_worker.run_delivery(reclaimed) is True
        assert len(provider.requests) == 1
        await _assert_completed(
            state,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            content="recovered after PostgreSQL restart",
        )
    finally:
        await resources.close()
        await cleanup_redis(redis_url, key_prefix)
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


class _Resources:
    def __init__(
        self,
        pool: PostgresPool,
        redis_url: str,
        key_prefix: str,
        suffix: str,
    ) -> None:
        self.pool = pool
        self.artifacts = PostgresArtifactStore(pool, NoopBlobStore())
        self.queue = RedisQueueAdapter(
            redis_url,
            key_prefix=key_prefix,
            group_name="workers",
        )
        self.replay = RedisEventReplayAdapter(redis_url, key_prefix=key_prefix)
        self.leases = RedisLeaseAdapter(redis_url, key_prefix=key_prefix)
        self.relay = OutboxRelay(
            outbox=PostgresOutboxStore(pool),
            queue=self.queue,
            owner_id=f"relay_{suffix}",
            batch_size=1,
            claim_ttl=timedelta(milliseconds=200),
        )

    def worker(self, provider: _RecoveryProvider, worker_id: str) -> WorkerRunner:
        protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
        return WorkerRunner(
            claims=PostgresClaimStore(self.pool),
            queue=self.queue,
            leases=self.leases,
            agent_factory=claim_scoped_agent_factory(
                pool=self.pool,
                builder=AgentBuilder().provider(provider),
                state=PostgresStateStore(self.pool),
                artifacts=self.artifacts,
                protector=protector,
            ),
            event_sink=self.replay,
            worker_id=worker_id,
            topic=EXECUTION_TOPIC,
            claim_ttl=timedelta(seconds=5),
            lease_ttl=timedelta(seconds=2),
            heartbeat_interval=timedelta(milliseconds=500),
            heartbeat_cycle_timeout=timedelta(milliseconds=500),
        )

    async def close(self) -> None:
        await self.relay.close()
        await self.replay.close()
        await self.leases.close()
        await self.queue.close()
        await self.artifacts.close()


async def _receive_one(queue: RedisQueueAdapter, consumer_id: str):
    deliveries = await queue.receive(
        topic=EXECUTION_TOPIC,
        consumer_id=consumer_id,
        limit=1,
    )
    assert len(deliveries) == 1
    return deliveries[0]


async def _reclaim_one(queue: RedisQueueAdapter, consumer_id: str):
    async def poll():
        while True:
            deliveries = await queue.reclaim(
                topic=EXECUTION_TOPIC,
                consumer_id=consumer_id,
                min_idle=timedelta(milliseconds=1),
                limit=1,
            )
            if deliveries:
                assert len(deliveries) == 1
                return deliveries[0]
            await asyncio.sleep(0)

    return await asyncio.wait_for(poll(), timeout=2)


async def _redrive_outbox(
    relay: OutboxRelay,
    pool: PostgresPool,
    outbox_id: str,
) -> None:
    for _ in range(200):
        await relay.relay_once()
        if await _outbox_attempts(pool, outbox_id) == 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("published execution outbox was not redriven")


async def _assert_outbox_attempts(
    pool: PostgresPool,
    outbox_id: str,
    *,
    attempts: int,
) -> None:
    assert await _outbox_attempts(pool, outbox_id) == attempts


async def _outbox_attempts(pool: PostgresPool, outbox_id: str) -> int:
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
    assert row["published_at"] is not None
    assert row["claim_id"] is None
    value = row["publish_attempts"]
    assert type(value) is int
    return value


async def _assert_queued_truth(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> None:
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_runs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS run_status,
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS input_status
            """,
            (
                scope.tenant_id,
                session_id,
                run_id,
                scope.tenant_id,
                session_id,
                run_id,
            ),
        )
    assert row == {"run_status": "queued", "input_status": "accepted"}


async def _assert_completed(
    state: PostgresStateStore,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    content: str,
) -> None:
    run = await state.get_run(
        scope=scope,
        session_id=session_id,
        run_id=run_id,
    )
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.content == content


def _redis_url() -> str:
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set AGENTOS_TEST_REDIS_URL to run live Redis tests")
    return redis_url
