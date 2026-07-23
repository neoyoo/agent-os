from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from agentos.distributed.errors import DistributedError
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.worker.supervisor import DistributedWorker
from agentos.runtime.run_state import RunStatus
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
    GatedProvider,
    IO_TIMEOUT,
    ImmediateProvider,
    NetworkTimeoutResources,
    expire_and_recover_claim,
    reclaim_pending_delivery,
    redis_url,
    wait_for_backend,
    wait_for_run_completed,
)


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("backend", ["redis", "postgres"])
def test_live_backend_pause_times_out_heartbeat_and_recovers(
    backend: Backend,
) -> None:
    environment = (
        "AGENTOS_TEST_REDIS_CONTAINER"
        if backend == "redis"
        else "AGENTOS_TEST_POSTGRES_CONTAINER"
    )
    service = DockerTestService.from_environment(environment)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_backend_pause(backend, service))


async def _verify_backend_pause(
    backend: Backend,
    service: DockerTestService,
) -> None:
    settings = live_postgres_settings()
    selected_redis_url = redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_{backend}_pause_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-{backend}-pause-{suffix}"
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
    state = PostgresStateStore(pool)
    old_provider = GatedProvider()
    old_worker: DistributedWorker | None = None
    recovered_worker: DistributedWorker | None = None
    paused = False
    try:
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                f"recover after {backend} network timeout",
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
        old_worker = resources.worker(old_provider, f"worker_old_{suffix}")
        await old_worker.start()
        await asyncio.wait_for(old_provider.started.wait(), timeout=2)

        service.pause()
        paused = True
        with pytest.raises(DistributedError):
            await asyncio.wait_for(old_worker.wait(), timeout=3)
        assert old_provider.cancelled.is_set()
        with pytest.raises(DistributedError):
            await asyncio.wait_for(old_worker.drain(timeout=0), timeout=2)
        with pytest.raises(DistributedError):
            await asyncio.wait_for(old_worker.close(), timeout=2)
        assert old_worker.state.status == "closed"

        service.unpause()
        paused = False
        await wait_for_backend(backend, selected_redis_url, pool)
        stale_delivery = await reclaim_pending_delivery(
            resources.queue,
            consumer_id=f"no_ack_proof_{suffix}",
            outbox_id=outbox_id,
        )
        # The reclaim proves the failed Worker did not ACK; remove that evidence
        # delivery so only the recovery outbox drives the next execution.
        await resources.queue.ack(
            topic=EXECUTION_TOPIC,
            delivery=stale_delivery,
        )
        recovery_outbox_id = await expire_and_recover_claim(
            pool,
            scope=scope,
            claims=PostgresClaimStore(pool),
        )
        await prioritize_outbox(pool, recovery_outbox_id)
        if backend == "redis":
            await asyncio.sleep(1.05)
        assert await resources.relay.relay_once() == 1

        content = f"recovered after {backend} network timeout"
        recovered_provider = ImmediateProvider(content)
        recovered_worker = resources.worker(
            recovered_provider,
            f"worker_recovered_{suffix}",
        )
        await recovered_worker.start()
        await wait_for_run_completed(
            state,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await asyncio.wait_for(recovered_worker.drain(timeout=2), timeout=3)
        await asyncio.wait_for(recovered_worker.close(), timeout=2)
        assert len(recovered_provider.requests) == 1

        run = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert run is not None
        assert run.status is RunStatus.COMPLETED
        assert run.result is not None
        assert run.result.content == content
    finally:
        old_provider.release.set()
        workers = tuple(
            worker
            for worker in (old_worker, recovered_worker)
            if worker is not None
        )
        await asyncio.gather(
            *(worker.close() for worker in workers),
            return_exceptions=True,
        )
        if paused:
            service.unpause()
            await wait_for_backend(backend, selected_redis_url, pool)
        await resources.close()
        await cleanup_redis(selected_redis_url, key_prefix)
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()
