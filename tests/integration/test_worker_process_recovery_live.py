from __future__ import annotations

import asyncio
from datetime import timedelta
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.distributed.models import QueueDelivery, RequestScope, RunSubmission
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
from agentos.durable.serialization import execution_cursor_from_json
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


class _RecoveryProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def async_complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse("recovered after process kill")


def test_live_worker_process_kill_recovers_pending_delivery(
    tmp_path: Path,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_worker_process_kill(tmp_path))


async def _verify_worker_process_kill(tmp_path: Path) -> None:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_process_kill_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-process-kill-{suffix}"
    marker = tmp_path / "provider-entered"
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
    leases = RedisLeaseAdapter(redis_url, key_prefix=key_prefix)
    relay = OutboxRelay(
        outbox=PostgresOutboxStore(pool),
        queue=relay_queue,
        owner_id=f"relay_{suffix}",
        batch_size=1,
        claim_ttl=timedelta(minutes=1),
    )
    process: subprocess.Popen[bytes] | None = None
    try:
        state = PostgresStateStore(pool)
        receipt = await state.submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "recover a killed worker process",
            ),
        )
        original_outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await _prioritize_outbox(pool, original_outbox_id)
        assert await relay.relay_once() == 1

        process = _start_worker_process(
            marker=marker,
            key_prefix=key_prefix,
            worker_id=f"worker_killed_{suffix}",
        )
        await _wait_for_marker(process, marker)
        await _assert_in_flight_truth(pool, scope, session_id, receipt.run_id)

        process.kill()
        await asyncio.wait_for(
            asyncio.to_thread(process.communicate),
            timeout=5,
        )
        assert process.returncode not in (None, 0)
        original_delivery = await _reclaim_one(
            worker_queue,
            consumer_id=f"reclaimer_{suffix}",
        )
        assert original_delivery.outbox_id == original_outbox_id
        await _assert_in_flight_truth(pool, scope, session_id, receipt.run_id)

        claims = PostgresClaimStore(pool)
        await _expire_claim(pool, scope, session_id)
        recovered = await claims.recover_expired(scope=scope, limit=1)
        assert len(recovered) == 1
        recovery_outbox_id = recovered[0].outbox_id
        await _prioritize_outbox(pool, recovery_outbox_id)
        assert await relay.relay_once() == 1
        recovery_deliveries = await worker_queue.receive(
            topic=EXECUTION_TOPIC,
            consumer_id=f"worker_recovery_{suffix}",
            limit=2,
        )
        assert tuple(item.outbox_id for item in recovery_deliveries) == (
            recovery_outbox_id,
        )

        await _wait_for_lease_expiry(
            leases,
            scope=scope,
            session_id=session_id,
        )
        provider = _RecoveryProvider()
        worker = _recovery_runner(
            pool=pool,
            artifacts=artifacts,
            protector=FernetPayloadProtector(
                FernetPayloadProtector.generate_key(),
            ),
            provider=provider,
            queue=worker_queue,
            leases=leases,
            replay=replay,
            worker_id=f"worker_recovery_{suffix}",
        )
        assert await worker.run_delivery(recovery_deliveries[0]) is True
        assert len(provider.requests) == 1
        assert await worker.run_delivery(original_delivery) is True
        assert len(provider.requests) == 1

        run = await state.get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert run is not None
        assert run.status is RunStatus.COMPLETED
        assert run.result is not None
        assert run.result.content == "recovered after process kill"
        await _assert_terminal_truth(pool, scope, session_id, receipt.run_id)
        assert await worker_queue.reclaim(
            topic=EXECUTION_TOPIC,
            consumer_id=f"final_reclaimer_{suffix}",
            min_idle=timedelta(milliseconds=1),
            limit=4,
        ) == ()
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await asyncio.wait_for(
                asyncio.to_thread(process.communicate),
                timeout=5,
            )
        await relay.close()
        await replay.close()
        await leases.close()
        await worker_queue.close()
        await relay_queue.close()
        await artifacts.close()
        await _cleanup_redis(redis_url, key_prefix)
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def _start_worker_process(
    *,
    marker: Path,
    key_prefix: str,
    worker_id: str,
) -> subprocess.Popen[bytes]:
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    inherited_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(project_root),
            str(project_root / "src"),
            *((inherited_path,) if inherited_path else ()),
        ),
    )
    environment.update(
        AGENTOS_PROCESS_MARKER=str(marker),
        AGENTOS_PROCESS_KEY_PREFIX=key_prefix,
        AGENTOS_PROCESS_WORKER_ID=worker_id,
        PYTHONUNBUFFERED="1",
    )
    return subprocess.Popen(
        (
            sys.executable,
            "-m",
            "tests.integration._worker_process_fixture",
        ),
        env=environment,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


async def _wait_for_marker(
    process: subprocess.Popen[bytes],
    marker: Path,
) -> None:
    async def poll() -> None:
        while not marker.exists():
            if process.poll() is not None:
                stdout, stderr = await asyncio.to_thread(process.communicate)
                raise AssertionError(
                    "worker process exited before Provider entry: "
                    f"stdout={stdout.decode(errors='replace')!r}, "
                    f"stderr={stderr.decode(errors='replace')!r}",
                )
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout=10)


def _recovery_runner(
    *,
    pool: PostgresPool,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
    provider: _RecoveryProvider,
    queue: RedisQueueAdapter,
    leases: RedisLeaseAdapter,
    replay: RedisEventReplayAdapter,
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
        claim_ttl=timedelta(seconds=5),
        lease_ttl=timedelta(seconds=1),
        heartbeat_interval=timedelta(milliseconds=200),
        heartbeat_cycle_timeout=timedelta(milliseconds=300),
    )


async def _reclaim_one(
    queue: RedisQueueAdapter,
    *,
    consumer_id: str,
) -> QueueDelivery:
    async def poll() -> QueueDelivery:
        while True:
            deliveries = await queue.reclaim(
                topic=EXECUTION_TOPIC,
                consumer_id=consumer_id,
                min_idle=timedelta(milliseconds=1),
                limit=2,
            )
            if deliveries:
                assert len(deliveries) == 1
                return deliveries[0]
            await asyncio.sleep(0)

    return await asyncio.wait_for(poll(), timeout=2)


async def _wait_for_lease_expiry(
    leases: RedisLeaseAdapter,
    *,
    scope: RequestScope,
    session_id: str,
) -> None:
    async def poll() -> None:
        while True:
            lease = await leases.acquire(
                scope=scope,
                session_id=session_id,
                owner_id="lease_expiry_probe",
                ttl=timedelta(seconds=1),
            )
            if lease is not None:
                await leases.release(scope=scope, lease=lease)
                return
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout=3)


async def _expire_claim(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
) -> None:
    async with pool.transaction() as connection:
        await connection.execute(
            """
            UPDATE agentos_distributed_sessions
            SET active_claim_expires_at = clock_timestamp() - interval '1 second'
            WHERE tenant_id = %s AND session_id = %s
              AND active_claim_id IS NOT NULL
            """,
            (scope.tenant_id, session_id),
        )


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


async def _assert_in_flight_truth(
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
              (SELECT status FROM agentos_distributed_runs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS run_status,
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS input_status,
              (SELECT payload_json FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS provider_cursor_payload
            """,
            _identity(scope, session_id, run_id) * 3,
        )
    assert truth["run_status"] == "running"
    assert truth["input_status"] == "claimed"
    cursor_payload = truth["provider_cursor_payload"]
    assert type(cursor_payload) is str
    assert execution_cursor_from_json(cursor_payload).stage == "before_provider"


async def _assert_terminal_truth(
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
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS input_status,
              (SELECT COUNT(*) FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS cursor_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND payload->>'kind' = 'terminal') AS terminal_outbox_count
            """,
            _identity(scope, session_id, run_id) * 3,
        )
    assert truth == {
        "input_status": "committed",
        "cursor_count": 0,
        "terminal_outbox_count": 1,
    }


def _identity(
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> tuple[str, str, str]:
    return scope.tenant_id, session_id, run_id


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
