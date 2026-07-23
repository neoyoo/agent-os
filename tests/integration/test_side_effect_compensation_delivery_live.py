from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from importlib import import_module
import os
from typing import Never
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.capabilities import SideEffectPolicy
from agentos.distributed.models import ClaimedExecution, QueueDelivery
from agentos.distributed.postgres._database import Row, fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.outbox import PostgresOutboxStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.distributed.redis.leases import RedisLeaseAdapter
from agentos.distributed.redis.queue import RedisQueueAdapter
from agentos.distributed.redis.replay import RedisEventReplayAdapter
from agentos.distributed.services import RunCommandService
from agentos.distributed.worker.relay import OutboxRelay
from agentos.distributed.worker.runner import WorkerRunner
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.side_effect_types import (
    SideEffectResolution,
    SideEffectResolutionKind,
)
from tests.integration._distributed_failure_support import (
    ProcessCrash,
    execution_outbox_id,
)
from tests.integration._side_effect_resolution_support import (
    AllowResolutionAuthorizer,
    ReconciliationScenario,
    open_reconciliation_scenario,
    scenario_agent_factory,
)
from tests.integration._side_effect_resolution_truth import (
    assert_resolution_terminal_truth,
)


pytestmark = pytest.mark.integration


class _UnexpectedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        raise AssertionError("compensation must not call the provider")


@dataclass(slots=True)
class _CommitCheckingQueue:
    delegate: RedisQueueAdapter
    scenario: ReconciliationScenario
    crash_before_ack: bool = False
    truth_at_ack: list[Row] = field(default_factory=list)

    async def ack(self, *, topic: str, delivery: QueueDelivery) -> None:
        async with self.scenario.pool.connection() as connection:
            truth = await fetchone(
                connection,
                """
                SELECT
                  (SELECT status FROM agentos_distributed_runs
                   WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                    AS run_status,
                  (SELECT status FROM agentos_distributed_accepted_inputs
                   WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                   ORDER BY accepted_at DESC LIMIT 1) AS input_status,
                  (SELECT status FROM agentos_distributed_side_effects
                   WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                   ORDER BY attempt DESC LIMIT 1) AS side_effect_status,
                  (SELECT COUNT(*) FROM agentos_distributed_outbox
                   WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                     AND payload->>'kind' = 'terminal'
                     AND payload->>'status' = 'failed') AS terminal_outbox_count
                """,
                _truth_params(self.scenario),
            )
        assert truth == {
            "run_status": "failed",
            "input_status": "committed",
            "side_effect_status": "compensated",
            "terminal_outbox_count": 1,
        }
        self.truth_at_ack.append(truth)
        if self.crash_before_ack:
            raise ProcessCrash
        await self.delegate.ack(topic=topic, delivery=delivery)


class _UnexpectedAgentFactory:
    def __init__(self) -> None:
        self.calls = 0

    async def hydrate(self, *, claimed: ClaimedExecution) -> Never:
        del claimed
        self.calls += 1
        raise AssertionError("committed delivery recovery must not hydrate an agent")


@pytest.mark.parametrize("lose_ack", (False, True), ids=("ack", "reclaim"))
def test_live_compensation_commits_postgres_before_redis_ack(
    lose_ack: bool,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_compensation_delivery(lose_ack))


async def _verify_compensation_delivery(lose_ack: bool) -> None:
    redis_url = _redis_url()
    scenario = await open_reconciliation_scenario(
        policy=SideEffectPolicy.COMPENSATABLE,
    )
    suffix = uuid4().hex
    key_prefix = f"agentos-compensation-delivery-{suffix}"
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
    leases = RedisLeaseAdapter(redis_url, key_prefix=key_prefix)
    replay = RedisEventReplayAdapter(redis_url, key_prefix=key_prefix)
    relay = OutboxRelay(
        outbox=PostgresOutboxStore(scenario.pool),
        queue=relay_queue,
        owner_id=f"relay_{suffix}",
        batch_size=1,
        claim_ttl=timedelta(minutes=1),
    )
    provider = _UnexpectedProvider()
    checking_queue = _CommitCheckingQueue(
        worker_queue,
        scenario,
        crash_before_ack=lose_ack,
    )
    recovered_queue: RedisQueueAdapter | None = None
    try:
        await _mark_existing_outbox_published(scenario)
        await _submit_compensation(scenario, suffix)
        outbox_id = await execution_outbox_id(
            scenario.pool,
            scope=scenario.scope,
            session_id=scenario.session_id,
            run_id=scenario.run_id,
            latest=True,
        )
        await _prioritize_outbox(scenario, outbox_id)

        assert await relay.relay_once() == 1
        deliveries = await worker_queue.receive(
            topic=EXECUTION_TOPIC,
            consumer_id=f"worker_{suffix}",
            limit=4,
        )
        assert tuple(item.outbox_id for item in deliveries) == (outbox_id,)

        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=AgentBuilder().provider(provider).tools([scenario.tool]),
        )
        worker = WorkerRunner(
            claims=PostgresClaimStore(scenario.pool),
            queue=checking_queue,  # type: ignore[arg-type]
            leases=leases,
            agent_factory=factory,
            event_sink=replay,
            worker_id=f"worker_{suffix}",
            topic=EXECUTION_TOPIC,
            claim_ttl=timedelta(minutes=1),
            lease_ttl=timedelta(seconds=30),
            heartbeat_interval=timedelta(seconds=10),
        )

        if lose_ack:
            with pytest.raises(ProcessCrash):
                await worker.run_delivery(deliveries[0])
        else:
            assert await worker.run_delivery(deliveries[0]) is True
        assert len(checking_queue.truth_at_ack) == 1
        assert provider.calls == 0
        assert len(scenario.probe.compensation_invocations) == 1
        assert scenario.probe.compensation_effects == set(
            scenario.probe.compensation_invocations,
        )
        await assert_resolution_terminal_truth(
            scenario,
            "failed",
            source_consumed=False,
        )
        if lose_ack:
            await worker_queue.close()
            recovered_queue = RedisQueueAdapter(
                redis_url,
                key_prefix=key_prefix,
                group_name="workers",
            )
            reclaimed = await _reclaim_delivery(
                recovered_queue,
                consumer_id=f"worker_recovered_{suffix}",
            )
            assert tuple(item.outbox_id for item in reclaimed) == (outbox_id,)
            recovery_factory = _UnexpectedAgentFactory()
            recovered_runner = WorkerRunner(
                claims=PostgresClaimStore(scenario.pool),
                queue=_CommitCheckingQueue(
                    recovered_queue,
                    scenario,
                ),  # type: ignore[arg-type]
                leases=leases,
                agent_factory=recovery_factory,
                event_sink=replay,
                worker_id=f"worker_recovered_{suffix}",
                topic=EXECUTION_TOPIC,
                claim_ttl=timedelta(minutes=1),
                lease_ttl=timedelta(seconds=30),
                heartbeat_interval=timedelta(seconds=10),
            )
            assert await recovered_runner.run_delivery(reclaimed[0]) is True
            assert recovery_factory.calls == 0
        queue = recovered_queue or worker_queue
        assert await queue.reclaim(
            topic=EXECUTION_TOPIC,
            consumer_id=f"worker_{suffix}",
            min_idle=timedelta(milliseconds=1),
            limit=4,
        ) == ()
    finally:
        await relay.close()
        await replay.close()
        await leases.close()
        if recovered_queue is not None:
            await recovered_queue.close()
        await worker_queue.close()
        await relay_queue.close()
        await _cleanup_redis(redis_url, key_prefix)
        await scenario.close()

async def _submit_compensation(
    scenario: ReconciliationScenario,
    suffix: str,
) -> None:
    resolution = SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.COMPENSATE,
    )
    authorizer = AllowResolutionAuthorizer()
    receipt = await RunCommandService(
        PostgresStateStore(scenario.pool),
        authorizer,
    ).submit(
        scenario.scope,
        scenario.session_id,
        DurableRunCommand(
            scenario.run_id,
            f"resolution_{suffix}",
            "resolve_side_effect",
            resolution,
        ),
    )
    assert receipt.duplicate is False
    assert authorizer.calls == [resolution]


async def _mark_existing_outbox_published(
    scenario: ReconciliationScenario,
) -> None:
    async with scenario.pool.transaction() as connection:
        await connection.execute(
            """
            UPDATE agentos_distributed_outbox
            SET published_at = CURRENT_TIMESTAMP,
                queue_entry_id = COALESCE(queue_entry_id, 'test-history')
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
              AND published_at IS NULL
            """,
            (
                scenario.scope.tenant_id,
                scenario.session_id,
                scenario.run_id,
            ),
        )


async def _prioritize_outbox(
    scenario: ReconciliationScenario,
    outbox_id: str,
) -> None:
    async with scenario.pool.transaction() as connection:
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


async def _reclaim_delivery(
    queue: RedisQueueAdapter,
    *,
    consumer_id: str,
) -> tuple[QueueDelivery, ...]:
    async def poll() -> tuple[QueueDelivery, ...]:
        while True:
            deliveries = await queue.reclaim(
                topic=EXECUTION_TOPIC,
                consumer_id=consumer_id,
                min_idle=timedelta(milliseconds=1),
                limit=4,
            )
            if deliveries:
                return deliveries
            await asyncio.sleep(0)

    return await asyncio.wait_for(poll(), timeout=2)


def _truth_params(scenario: ReconciliationScenario) -> tuple[str, ...]:
    identity = (
        scenario.scope.tenant_id,
        scenario.session_id,
        scenario.run_id,
    )
    return identity * 4


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
