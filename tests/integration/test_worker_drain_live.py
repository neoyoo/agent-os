from __future__ import annotations

import asyncio
from datetime import timedelta
import os
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.distributed.models import RequestScope, RunSubmission
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
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_state import RunStatus
from agentos.security import FernetPayloadProtector
from tests.integration._backend_restart_support import (
    cleanup_redis,
    prioritize_outbox,
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


class _GatedProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        return ProviderResponse("completed while draining")


class _ImmediateProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse("completed by replacement worker")


def test_live_drain_finishes_active_run_without_claiming_next_delivery() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_active_drain())


async def _verify_active_drain() -> None:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scopes = tuple(
        RequestScope(f"tenant_drain_{index}_{suffix}", "principal_1")
        for index in (1, 2)
    )
    session_ids = tuple(f"session_{index}_{suffix}" for index in (1, 2))
    key_prefix = f"agentos-worker-drain-{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    relay_queue = _queue(redis_url, key_prefix)
    first_queue = _queue(redis_url, key_prefix)
    second_queue = _queue(redis_url, key_prefix)
    replay = RedisEventReplayAdapter(redis_url, key_prefix=key_prefix)
    first_leases = RedisLeaseAdapter(redis_url, key_prefix=key_prefix)
    second_leases = RedisLeaseAdapter(redis_url, key_prefix=key_prefix)
    relay = OutboxRelay(
        outbox=PostgresOutboxStore(pool),
        queue=relay_queue,
        owner_id=f"relay_{suffix}",
        batch_size=2,
        claim_ttl=timedelta(minutes=1),
    )
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    first_provider = _GatedProvider()
    second_provider = _ImmediateProvider()
    first_worker = _worker(
        pool=pool,
        artifacts=artifacts,
        protector=protector,
        provider=first_provider,
        queue=first_queue,
        leases=first_leases,
        replay=replay,
        worker_id=f"worker_draining_{suffix}",
    )
    second_worker = _worker(
        pool=pool,
        artifacts=artifacts,
        protector=protector,
        provider=second_provider,
        queue=second_queue,
        leases=second_leases,
        replay=replay,
        worker_id=f"worker_replacement_{suffix}",
    )
    try:
        state = PostgresStateStore(pool)
        run_ids: list[str] = []
        outbox_ids: list[str] = []
        for index, (scope, session_id) in enumerate(
            zip(scopes, session_ids, strict=True),
            start=1,
        ):
            receipt = await state.submit(
                scope=scope,
                submission=RunSubmission(
                    session_id,
                    f"submission_{index}_{suffix}",
                    f"drain request {index}",
                ),
            )
            run_ids.append(receipt.run_id)
            outbox_ids.append(
                await execution_outbox_id(
                    pool,
                    scope=scope,
                    session_id=session_id,
                    run_id=receipt.run_id,
                ),
            )
        await prioritize_outbox(pool, outbox_ids[1])
        await prioritize_outbox(pool, outbox_ids[0])
        assert await relay.relay_once() == 2

        await first_worker.start()
        await asyncio.wait_for(first_provider.started.wait(), timeout=2)
        draining = asyncio.create_task(first_worker.drain(timeout=5))
        await _wait_for_draining(first_worker)
        assert first_worker.state.active_claim_count == 1

        first_provider.release.set()
        await draining
        assert len(first_provider.requests) == 1
        assert first_worker.state.active_claim_count == 0
        await _assert_statuses(
            state,
            scopes=scopes,
            session_ids=session_ids,
            run_ids=tuple(run_ids),
            expected=(RunStatus.COMPLETED, RunStatus.QUEUED),
        )
        await first_worker.close()

        await second_worker.start()
        await _wait_for_run_status(
            state,
            scope=scopes[1],
            session_id=session_ids[1],
            run_id=run_ids[1],
            status=RunStatus.COMPLETED,
        )
        await second_worker.drain(timeout=2)
        assert len(second_provider.requests) == 1
        await _assert_statuses(
            state,
            scopes=scopes,
            session_ids=session_ids,
            run_ids=tuple(run_ids),
            expected=(RunStatus.COMPLETED, RunStatus.COMPLETED),
        )
    finally:
        first_provider.release.set()
        await asyncio.gather(
            first_worker.close(),
            second_worker.close(),
            return_exceptions=True,
        )
        await relay.close()
        await replay.close()
        await first_leases.close()
        await second_leases.close()
        await relay_queue.close()
        await artifacts.close()
        await cleanup_redis(redis_url, key_prefix)
        for scope in scopes:
            await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def _worker(
    *,
    pool,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
    provider,
    queue: RedisQueueAdapter,
    leases: RedisLeaseAdapter,
    replay: RedisEventReplayAdapter,
    worker_id: str,
) -> DistributedWorker:
    runner = WorkerRunner(
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
        claim_ttl=timedelta(seconds=2),
        lease_ttl=timedelta(seconds=1),
        heartbeat_interval=timedelta(milliseconds=200),
        heartbeat_cycle_timeout=timedelta(milliseconds=300),
    )
    return DistributedWorker(
        runner=runner,
        queue=queue,
        worker_id=worker_id,
        topic=EXECUTION_TOPIC,
        max_concurrency=1,
    )


def _queue(redis_url: str, key_prefix: str) -> RedisQueueAdapter:
    return RedisQueueAdapter(
        redis_url,
        key_prefix=key_prefix,
        group_name="workers",
        block_ms=50,
    )


async def _wait_for_draining(worker: DistributedWorker) -> None:
    async def poll() -> None:
        while worker.state.status != "draining":
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=1)


async def _wait_for_run_status(
    state: PostgresStateStore,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    status: RunStatus,
) -> None:
    async def poll() -> None:
        while True:
            run = await state.get_run(
                scope=scope,
                session_id=session_id,
                run_id=run_id,
            )
            if run is not None and run.status is status:
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(poll(), timeout=3)


async def _assert_statuses(
    state: PostgresStateStore,
    *,
    scopes: tuple[RequestScope, ...],
    session_ids: tuple[str, ...],
    run_ids: tuple[str, ...],
    expected: tuple[RunStatus, ...],
) -> None:
    statuses = []
    for scope, session_id, run_id in zip(
        scopes,
        session_ids,
        run_ids,
        strict=True,
    ):
        run = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=run_id,
        )
        assert run is not None
        statuses.append(run.status)
    assert tuple(statuses) == expected


def _redis_url() -> str:
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set AGENTOS_TEST_REDIS_URL to run live Redis tests")
    return redis_url
