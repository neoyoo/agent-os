from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
import os
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.distributed.models import (
    RequestScope,
    RunSubmission,
    SessionLease,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter
from agentos.distributed.worker.relay import OutboxRelay
from agentos.distributed.worker.runner import WorkerRunner
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_state import RunStatus
from agentos.security import FernetPayloadProtector
from tests.integration._distributed_failure_support import (
    NoopBlobStore,
    claim_scoped_agent_factory,
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
)


pytestmark = pytest.mark.integration


class _GatedProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return ProviderResponse("claimed once")


@dataclass(slots=True)
class _ConcurrentLeases:
    arrivals: int = 0
    released: list[str] = field(default_factory=list)
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(
        self,
        *,
        scope: RequestScope,
        session_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> SessionLease:
        self.arrivals += 1
        if self.arrivals == 2:
            self.ready.set()
        await self.ready.wait()
        return SessionLease(
            scope,
            session_id,
            owner_id,
            f"lease_{owner_id}_{uuid4().hex}",
            datetime.now(UTC) + ttl,
        )

    async def renew(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
        ttl: timedelta,
    ) -> SessionLease:
        return SessionLease(
            scope,
            lease.session_id,
            lease.owner_id,
            lease.lease_id,
            datetime.now(UTC) + ttl,
        )

    async def ensure_owned(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None:
        assert lease.scope == scope

    async def release(
        self,
        *,
        scope: RequestScope,
        lease: SessionLease,
    ) -> None:
        assert lease.scope == scope
        self.released.append(lease.owner_id)


def test_live_two_workers_assign_one_postgres_claim_and_one_effect() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_worker_claim_race())


async def _verify_worker_claim_race() -> None:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_worker_race_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-worker-race-{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    relay_queue = RedisQueueAdapter(
        redis_url,
        key_prefix=key_prefix,
        group_name="workers",
    )
    worker_queue = RedisQueueAdapter(
        redis_url,
        key_prefix=key_prefix,
        group_name="workers",
    )
    replay = RedisEventReplayAdapter(redis_url, key_prefix=key_prefix)
    relay = OutboxRelay(
        outbox=PostgresOutboxStore(pool),
        queue=relay_queue,
        owner_id=f"relay_{suffix}",
        batch_size=1,
        claim_ttl=timedelta(minutes=1),
    )
    try:
        state = PostgresStateStore(pool)
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "allow exactly one worker to execute",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await _prioritize_outbox(pool, outbox_id)
        assert await relay.relay_once() == 1
        deliveries = await worker_queue.receive(
            topic=EXECUTION_TOPIC,
            consumer_id=f"receiver_{suffix}",
            limit=1,
        )
        assert len(deliveries) == 1

        provider = _GatedProvider()
        protector: PayloadProtector = FernetPayloadProtector(
            FernetPayloadProtector.generate_key(),
        )
        leases = _ConcurrentLeases()
        runners = tuple(
            _runner(
                pool=pool,
                artifacts=artifacts,
                protector=protector,
                provider=provider,
                queue=worker_queue,
                replay=replay,
                leases=leases,
                worker_id=f"worker_{index}_{suffix}",
            )
            for index in (1, 2)
        )
        tasks = tuple(
            asyncio.create_task(runner.run_delivery(deliveries[0]))
            for runner in runners
        )
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        loser = await _wait_for_one_done(tasks)
        assert await loser is False
        provider.release.set()
        results = await asyncio.gather(*tasks)

        assert sorted(results) == [False, True]
        assert len(provider.requests) == 1
        assert leases.arrivals == 2
        assert sorted(leases.released) == sorted(
            (f"worker_1_{suffix}", f"worker_2_{suffix}"),
        )
        run = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert run is not None
        assert run.status is RunStatus.COMPLETED
        await _assert_single_claim_truth(pool, scope, session_id, receipt.run_id)
        assert await worker_queue.reclaim(
            topic=EXECUTION_TOPIC,
            consumer_id=f"reclaimer_{suffix}",
            min_idle=timedelta(milliseconds=1),
            limit=2,
        ) == ()
    finally:
        await relay.close()
        await replay.close()
        await worker_queue.close()
        await relay_queue.close()
        await artifacts.close()
        await _cleanup_redis(redis_url, key_prefix)
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def _runner(
    *,
    pool: PostgresPool,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
    provider: _GatedProvider,
    queue: RedisQueueAdapter,
    replay: RedisEventReplayAdapter,
    leases: _ConcurrentLeases,
    worker_id: str,
) -> WorkerRunner:
    return WorkerRunner(
        claims=PostgresClaimStore(pool),
        queue=queue,
        leases=leases,
        agent_factory=claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(provider),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        ),
        event_sink=replay,
        worker_id=worker_id,
        topic=EXECUTION_TOPIC,
        claim_ttl=timedelta(minutes=1),
        lease_ttl=timedelta(seconds=30),
        heartbeat_interval=timedelta(seconds=10),
    )


async def _wait_for_one_done(
    tasks: tuple[asyncio.Task[bool], ...],
) -> asyncio.Task[bool]:
    done, _ = await asyncio.wait(
        tasks,
        timeout=2,
        return_when=asyncio.FIRST_COMPLETED,
    )
    assert len(done) == 1
    return next(iter(done))


async def _prioritize_outbox(pool: PostgresPool, outbox_id: str) -> None:
    async with pool.transaction() as connection:
        await connection.execute(
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
            WHERE target.outbox_id = %s
            """,
            (outbox_id,),
        )


async def _assert_single_claim_truth(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> None:
    async with pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT fencing_token FROM agentos_distributed_sessions
               WHERE tenant_id = %s AND session_id = %s) AS fencing_token,
              (SELECT COUNT(*) FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND status = 'committed') AS committed_input_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND payload->>'kind' = 'terminal') AS terminal_outbox_count
            """,
            (
                scope.tenant_id,
                session_id,
                scope.tenant_id,
                session_id,
                run_id,
                scope.tenant_id,
                session_id,
                run_id,
            ),
        )
    assert truth == {
        "fencing_token": 1,
        "committed_input_count": 1,
        "terminal_outbox_count": 1,
    }


def _redis_url() -> str:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set AGENTOS_TEST_REDIS_URL to run live Redis tests")
    return redis_url


async def _cleanup_redis(redis_url: str, key_prefix: str) -> None:
    redis_asyncio = import_module("redis.asyncio")
    client = redis_asyncio.Redis.from_url(redis_url)
    try:
        keys = [key async for key in client.scan_iter(match=f"{key_prefix}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()
