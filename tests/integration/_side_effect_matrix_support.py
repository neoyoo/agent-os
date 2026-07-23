from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
import os
from typing import Literal
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
)
from agentos.distributed.models import QueueDelivery, RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.leases import RedisLeaseAdapter
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter
from agentos.distributed.worker.relay import OutboxRelay
from agentos.distributed.worker.runner import WorkerRunner
from agentos.runtime.payloads import PayloadProtector
from agentos.security import FernetPayloadProtector
from tests.integration._backend_restart_support import (
    cleanup_redis,
    prioritize_outbox,
)
from tests.integration._distributed_failure_support import (
    NoopBlobStore,
    ProcessCrash,
    cleanup_tenant,
    execution_outbox_id,
    live_postgres_settings,
    open_migrated_pool,
)
from tests.integration._side_effect_resolution_support import scenario_agent_factory
from tests.integration._side_effect_matrix_doubles import (
    AckRecordingQueue,
    CrashAfterCompletedStore,
    CrashBeforeStartedStore,
    EffectProbe,
    FinalProvider,
    ToolCallProvider,
)


CrashStage = Literal["reserved", "started", "completed"]


@dataclass(slots=True)
class SideEffectMatrixScenario:
    pool: PostgresPool
    scope: RequestScope
    session_id: str
    run_id: str
    redis_url: str
    key_prefix: str
    artifacts: PostgresArtifactStore
    claims: PostgresClaimStore
    relay_queue: RedisQueueAdapter
    worker_queue: RedisQueueAdapter
    leases: RedisLeaseAdapter
    replay: RedisEventReplayAdapter
    relay: OutboxRelay
    ack_queue: AckRecordingQueue
    tool: RegisteredTool
    protector: PayloadProtector
    crash_store: PostgresSideEffectStore
    original_delivery: QueueDelivery
    initial_provider: ToolCallProvider
    recovery_provider: FinalProvider
    probe: EffectProbe
    suffix: str

    def initial_runner(self) -> WorkerRunner:
        factory = scenario_agent_factory(
            pool=self.pool,
            protector=self.protector,
            artifacts=self.artifacts,
            builder=AgentBuilder().provider(self.initial_provider).tools([self.tool]),
            side_effects=self.crash_store,
        )
        return self._runner(factory, f"worker_crash_{self.suffix}")

    def recovery_runner(self) -> WorkerRunner:
        factory = scenario_agent_factory(
            pool=self.pool,
            protector=self.protector,
            artifacts=self.artifacts,
            builder=AgentBuilder().provider(self.recovery_provider).tools([self.tool]),
        )
        return self._runner(factory, f"worker_recovery_{self.suffix}")

    def _runner(self, factory: object, worker_id: str) -> WorkerRunner:
        return WorkerRunner(
            claims=self.claims,
            queue=self.ack_queue,  # type: ignore[arg-type]
            leases=self.leases,
            agent_factory=factory,  # type: ignore[arg-type]
            event_sink=self.replay,
            worker_id=worker_id,
            topic=EXECUTION_TOPIC,
            claim_ttl=timedelta(seconds=30),
            lease_ttl=timedelta(seconds=10),
            heartbeat_interval=timedelta(seconds=2),
            heartbeat_cycle_timeout=timedelta(seconds=2),
        )

    async def close(self) -> None:
        await self.relay.close()
        await self.replay.close()
        await self.leases.close()
        await self.worker_queue.close()
        await self.relay_queue.close()
        await self.artifacts.close()
        await cleanup_redis(self.redis_url, self.key_prefix)
        await cleanup_tenant(self.pool, self.scope.tenant_id)
        await self.pool.close()


async def open_side_effect_matrix_scenario(
    policy: SideEffectPolicy,
    stage: CrashStage,
) -> SideEffectMatrixScenario:
    settings = live_postgres_settings()
    redis_url = _redis_url()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_matrix_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    key_prefix = f"agentos-side-effect-matrix-{suffix}"
    async with AsyncExitStack() as cleanup:
        pool = await open_migrated_pool(settings.dsn)
        cleanup.push_async_callback(pool.close)
        cleanup.push_async_callback(cleanup_tenant, pool, scope.tenant_id)
        cleanup.push_async_callback(cleanup_redis, redis_url, key_prefix)
        artifacts = PostgresArtifactStore(pool, NoopBlobStore())
        cleanup.push_async_callback(artifacts.close)
        claims = PostgresClaimStore(pool)
        relay_queue = RedisQueueAdapter(
            redis_url,
            key_prefix=key_prefix,
            group_name="workers",
        )
        cleanup.push_async_callback(relay_queue.close)
        worker_queue = RedisQueueAdapter(
            redis_url,
            key_prefix=key_prefix,
            group_name="workers",
        )
        cleanup.push_async_callback(worker_queue.close)
        leases = RedisLeaseAdapter(redis_url, key_prefix=key_prefix)
        cleanup.push_async_callback(leases.close)
        replay = RedisEventReplayAdapter(redis_url, key_prefix=key_prefix)
        cleanup.push_async_callback(replay.close)
        relay = OutboxRelay(
            outbox=PostgresOutboxStore(pool),
            queue=relay_queue,
            owner_id=f"relay_{suffix}",
            batch_size=1,
            claim_ttl=timedelta(minutes=1),
        )
        cleanup.push_async_callback(relay.close)
        probe = EffectProbe(stage == "started")
        tool = RegisteredTool(
            "external_effect",
            "Perform one policy-governed external effect.",
            {"type": "object"},
            probe.invoke,
            policy,
            compensation_handler=(
                probe.compensate
                if policy is SideEffectPolicy.COMPENSATABLE
                else None
            ),
        )
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "exercise the side effect recovery matrix",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        await prioritize_outbox(pool, outbox_id)
        assert await relay.relay_once() == 1
        deliveries = await worker_queue.receive(
            topic=EXECUTION_TOPIC,
            consumer_id=f"worker_crash_{suffix}",
            limit=2,
        )
        assert len(deliveries) == 1
        crash_store: PostgresSideEffectStore
        if stage == "reserved":
            crash_store = CrashBeforeStartedStore(pool)
        elif stage == "completed":
            crash_store = CrashAfterCompletedStore(pool)
        else:
            crash_store = PostgresSideEffectStore(pool)
        scenario = SideEffectMatrixScenario(
            pool=pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            redis_url=redis_url,
            key_prefix=key_prefix,
            artifacts=artifacts,
            claims=claims,
            relay_queue=relay_queue,
            worker_queue=worker_queue,
            leases=leases,
            replay=replay,
            relay=relay,
            ack_queue=AckRecordingQueue(
                worker_queue,
                pool,
                scope,
                session_id,
                receipt.run_id,
            ),
            tool=tool,
            protector=FernetPayloadProtector(
                FernetPayloadProtector.generate_key(),
            ),
            crash_store=crash_store,
            original_delivery=deliveries[0],
            initial_provider=ToolCallProvider(),
            recovery_provider=FinalProvider(),
            probe=probe,
            suffix=suffix,
        )
        cleanup.pop_all()
        return scenario


async def publish_recovery_delivery(
    scenario: SideEffectMatrixScenario,
) -> QueueDelivery:
    async with scenario.pool.transaction() as connection:
        await connection.execute(
            """
            UPDATE agentos_distributed_sessions
            SET active_claim_expires_at = clock_timestamp() - interval '1 second'
            WHERE tenant_id = %s AND session_id = %s
              AND active_claim_id IS NOT NULL
            """,
            (scenario.scope.tenant_id, scenario.session_id),
        )
    recovered = await scenario.claims.recover_expired(scope=scenario.scope, limit=1)
    assert len(recovered) == 1
    await prioritize_outbox(scenario.pool, recovered[0].outbox_id)
    assert await scenario.relay.relay_once() == 1
    deliveries = await scenario.worker_queue.receive(
        topic=EXECUTION_TOPIC,
        consumer_id=f"worker_recovery_{scenario.suffix}",
        limit=2,
    )
    assert len(deliveries) == 1
    assert deliveries[0].outbox_id == recovered[0].outbox_id
    return deliveries[0]


def _redis_url() -> str:
    if os.environ.get("AGENTOS_RUN_INTEGRATION") != "1":
        pytest.skip("set AGENTOS_RUN_INTEGRATION=1 to run live integration tests")
    redis_url = os.environ.get("AGENTOS_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set AGENTOS_TEST_REDIS_URL to run live Redis tests")
    return redis_url


__all__ = [
    "CrashStage",
    "ProcessCrash",
    "SideEffectMatrixScenario",
    "open_side_effect_matrix_scenario",
    "publish_recovery_delivery",
]
