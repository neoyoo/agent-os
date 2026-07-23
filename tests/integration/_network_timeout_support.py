from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from typing import Literal

import pytest

from agentos import AgentBuilder
from agentos.distributed.models import QueueDelivery, RequestScope
from agentos.distributed.postgres._database import PostgresPool
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
from agentos.distributed.worker.supervisor import DistributedWorker
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime.run_state import RunStatus
from agentos.security import FernetPayloadProtector
from tests.integration._backend_restart_support import (
    wait_for_postgres,
    wait_for_redis,
)
from tests.integration._distributed_failure_support import (
    NoopBlobStore,
    claim_scoped_agent_factory,
)


Backend = Literal["redis", "postgres"]
IO_TIMEOUT = timedelta(milliseconds=200)


class GatedProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ProviderResponse("unexpected old worker completion")


class ImmediateProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(self.content)


class NetworkTimeoutResources:
    def __init__(
        self,
        pool: PostgresPool,
        redis_url: str,
        key_prefix: str,
        suffix: str,
    ) -> None:
        self.pool = pool
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.artifacts = PostgresArtifactStore(pool, NoopBlobStore())
        queue_options = {
            "key_prefix": key_prefix,
            "group_name": "workers",
            "operation_timeout": IO_TIMEOUT,
        }
        self.queue = RedisQueueAdapter(redis_url, **queue_options)
        self.replay = RedisEventReplayAdapter(
            redis_url,
            key_prefix=key_prefix,
            operation_timeout=IO_TIMEOUT,
        )
        self.leases = RedisLeaseAdapter(
            redis_url,
            key_prefix=key_prefix,
            operation_timeout=IO_TIMEOUT,
        )
        self.relay = OutboxRelay(
            outbox=PostgresOutboxStore(pool),
            queue=self.queue,
            owner_id=f"relay_{suffix}",
            batch_size=1,
            claim_ttl=timedelta(seconds=2),
            batch_timeout=timedelta(seconds=1),
        )
        self.worker_queues: list[RedisQueueAdapter] = []

    def worker(
        self,
        provider: GatedProvider | ImmediateProvider,
        worker_id: str,
    ) -> DistributedWorker:
        queue = RedisQueueAdapter(
            self.redis_url,
            key_prefix=self.key_prefix,
            group_name="workers",
            block_ms=50,
            operation_timeout=IO_TIMEOUT,
        )
        self.worker_queues.append(queue)
        protector = FernetPayloadProtector(FernetPayloadProtector.generate_key())
        runner = WorkerRunner(
            claims=PostgresClaimStore(self.pool),
            queue=queue,
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
            claim_ttl=timedelta(seconds=2),
            lease_ttl=timedelta(seconds=1),
            heartbeat_interval=timedelta(milliseconds=50),
            heartbeat_cycle_timeout=timedelta(milliseconds=300),
        )
        return DistributedWorker(
            runner=runner,
            queue=queue,
            worker_id=worker_id,
            topic=EXECUTION_TOPIC,
            max_concurrency=1,
            shutdown_cleanup_timeout=timedelta(seconds=1),
        )

    async def close(self) -> None:
        await asyncio.gather(
            *(queue.close() for queue in self.worker_queues),
            return_exceptions=True,
        )
        await self.relay.close()
        await self.replay.close()
        await self.leases.close()
        await self.queue.close()
        await self.artifacts.close()


async def expire_and_recover_claim(
    pool: PostgresPool,
    *,
    scope: RequestScope,
    claims: PostgresClaimStore,
) -> str:
    async with pool.transaction() as connection:
        await connection.execute(
            """
            UPDATE agentos_distributed_sessions
            SET active_claim_expires_at = clock_timestamp() - interval '1 second'
            WHERE tenant_id = %s AND active_claim_id IS NOT NULL
            """,
            (scope.tenant_id,),
        )
    recovered = await claims.recover_expired(scope=scope, limit=1)
    assert len(recovered) == 1
    return recovered[0].outbox_id


async def receive_one(
    queue: RedisQueueAdapter,
    consumer_id: str,
) -> QueueDelivery:
    deliveries = await queue.receive(
        topic=EXECUTION_TOPIC,
        consumer_id=consumer_id,
        limit=1,
    )
    assert len(deliveries) == 1
    return deliveries[0]


async def reclaim_pending_delivery(
    queue: RedisQueueAdapter,
    *,
    consumer_id: str,
    outbox_id: str,
) -> QueueDelivery:
    deliveries = await queue.reclaim(
        topic=EXECUTION_TOPIC,
        consumer_id=consumer_id,
        min_idle=timedelta(milliseconds=1),
        limit=1,
    )
    assert len(deliveries) == 1
    assert deliveries[0].outbox_id == outbox_id, (
        deliveries[0].outbox_id,
        outbox_id,
    )
    return deliveries[0]


async def wait_for_backend(
    backend: Backend,
    redis_url: str,
    pool: PostgresPool,
) -> None:
    if backend == "redis":
        await wait_for_redis(redis_url)
    else:
        await wait_for_postgres(pool)


async def wait_for_run_completed(
    state: PostgresStateStore,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> None:
    async def poll() -> None:
        while True:
            run = await state.get_run(
                scope=scope,
                session_id=session_id,
                run_id=run_id,
            )
            if run is not None and run.status is RunStatus.COMPLETED:
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=5)


def redis_url() -> str:
    value = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not value:
        pytest.skip("set AGENTOS_TEST_REDIS_URL to run live Redis tests")
    return value
