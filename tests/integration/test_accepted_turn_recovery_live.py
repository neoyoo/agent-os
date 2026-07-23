from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos.distributed.models import (
    ClaimedExecution,
    RequestScope,
    RunDeliveryTarget,
    RunSubmission,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderRequest, ProviderResponse
from agentos.runtime import AgentResult
from agentos.runtime.execution import ApplyAcceptedInput, RestoreAcceptedTurn
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_state import RunStatus
from agentos.security import FernetPayloadProtector
from tests.integration._distributed_failure_support import (
    CrashController,
    CrashingStateStore,
    NoopBlobStore,
    ProcessCrash,
    claim_scoped_agent_factory,
    cleanup_tenant,
    execution_outbox_id,
    expire_and_reclaim,
    live_postgres_settings,
    open_migrated_pool,
)


pytestmark = pytest.mark.integration


class _CountingProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(self.content)


class _CrashOnceProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            raise ProcessCrash
        return ProviderResponse("provider recovered")


class _CrashingClaimStore(PostgresClaimStore):
    def __init__(
        self,
        database: PostgresPool,
        *,
        point: Literal["resolve", "claim"],
        timing: Literal["before", "after"],
    ) -> None:
        super().__init__(database)
        self._point = point
        self._timing = timing
        self._fired = False

    def _crash(self, point: Literal["resolve", "claim"], timing: str) -> None:
        if not self._fired and self._point == point and self._timing == timing:
            self._fired = True
            raise ProcessCrash

    async def resolve_delivery(self, *, outbox_id: str) -> RunDeliveryTarget | None:
        self._crash("resolve", "before")
        target = await super().resolve_delivery(outbox_id=outbox_id)
        self._crash("resolve", "after")
        return target

    async def claim_pending_turn(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> ClaimedExecution | None:
        self._crash("claim", "before")
        claimed = await super().claim_pending_turn(
            scope=scope,
            outbox_id=outbox_id,
            owner_id=owner_id,
            ttl=ttl,
        )
        self._crash("claim", "after")
        return claimed


class _CrashBeforeHydration:
    def __init__(self, factory: ClaimScopedAgentFactory) -> None:
        self._factory = factory

    async def hydrate(self, *, claimed: ClaimedExecution):  # type: ignore[no-untyped-def]
        del claimed
        raise ProcessCrash


@pytest.mark.parametrize(
    ("point", "timing"),
    (
        ("resolve", "before"),
        ("resolve", "after"),
        ("claim", "before"),
        ("claim", "after"),
        ("hydrate", "before"),
    ),
)
def test_live_accepted_turn_claim_boundaries_remain_recoverable(
    point: Literal["resolve", "claim", "hydrate"],
    timing: Literal["before", "after"],
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_claim_boundary(point, timing))


async def _verify_claim_boundary(
    point: Literal["resolve", "claim", "hydrate"],
    timing: Literal["before", "after"],
) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_claim_window_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    provider = _CountingProvider("claim recovered")
    try:
        run_id, outbox_id = await _submit_run(pool, scope, session_id, suffix)
        claimed: ClaimedExecution | None
        if point == "resolve":
            crashing = _CrashingClaimStore(pool, point="resolve", timing=timing)
            with pytest.raises(ProcessCrash):
                await crashing.resolve_delivery(outbox_id=outbox_id)
            assert await _accepted_status(pool, scope, session_id, run_id) == "accepted"
            assert await claims.resolve_delivery(outbox_id=outbox_id) is not None
            claimed = await _claim(claims, scope, outbox_id, suffix)
        elif point == "claim":
            crashing = _CrashingClaimStore(pool, point="claim", timing=timing)
            with pytest.raises(ProcessCrash):
                await _claim(crashing, scope, outbox_id, suffix)
            expected = "accepted" if timing == "before" else "claimed"
            assert await _accepted_status(pool, scope, session_id, run_id) == expected
            if timing == "before":
                claimed = await _claim(claims, scope, outbox_id, suffix)
            else:
                claimed = await expire_and_reclaim(
                    pool,
                    claims=claims,
                    scope=scope,
                    session_id=session_id,
                    owner_id=f"worker_recovered_{suffix}",
                )
        else:
            claimed = await _claim(claims, scope, outbox_id, suffix)
            assert claimed is not None
            base_factory = claim_scoped_agent_factory(
                pool=pool,
                builder=AgentBuilder().provider(provider),
                state=PostgresStateStore(pool),
                artifacts=artifacts,
                protector=protector,
            )
            with pytest.raises(ProcessCrash):
                await _CrashBeforeHydration(base_factory).hydrate(claimed=claimed)
            claimed = await expire_and_reclaim(
                pool,
                claims=claims,
                scope=scope,
                session_id=session_id,
                owner_id=f"worker_recovered_{suffix}",
            )

        assert claimed is not None
        assert type(claimed.execution.preparation) is ApplyAcceptedInput
        await _complete_claim(pool, artifacts, protector, claimed, provider)
        assert provider.calls == 1
        await _assert_single_user_message(pool, scope, session_id, claimed)
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


@pytest.mark.parametrize("timing", ("before", "after"))
def test_live_run_start_commit_window_reapplies_the_same_accepted_input(
    timing: Literal["before", "after"],
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_run_start_window(timing))


async def _verify_run_start_window(timing: Literal["before", "after"]) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_run_start_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    try:
        run_id, outbox_id = await _submit_run(pool, scope, session_id, suffix)
        claimed = await _claim(claims, scope, outbox_id, suffix)
        assert claimed is not None
        unused_provider = _CountingProvider("must not be called")
        factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(unused_provider),
            state=CrashingStateStore(
                pool,
                CrashController("run_start", timing),
            ),
            artifacts=artifacts,
            protector=protector,
        )
        agent = await factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await agent.run(claimed.execution)
        assert unused_provider.calls == 0
        run = await PostgresStateStore(pool).get_run(
            scope=scope,
            session_id=session_id,
            run_id=run_id,
        )
        assert run is not None
        expected_status = RunStatus.QUEUED if timing == "before" else RunStatus.RUNNING
        assert run.status is expected_status

        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_recovered_{suffix}",
        )
        assert type(recovered.execution.preparation) is ApplyAcceptedInput
        provider = _CountingProvider("start recovered")
        await _complete_claim(pool, artifacts, protector, recovered, provider)
        assert provider.calls == 1
        await _assert_single_user_message(pool, scope, session_id, recovered)
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


def test_live_provider_in_flight_restarts_from_before_provider_checkpoint() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_provider_in_flight_window())


async def _verify_provider_in_flight_window() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_provider_inflight_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    provider = _CrashOnceProvider()
    try:
        _, outbox_id = await _submit_run(pool, scope, session_id, suffix)
        claimed = await _claim(claims, scope, outbox_id, suffix)
        assert claimed is not None
        factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(provider),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        )
        agent = await factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await agent.run(claimed.execution)
        assert provider.calls == 1

        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_recovered_{suffix}",
        )
        preparation = recovered.execution.preparation
        assert type(preparation) is RestoreAcceptedTurn
        assert preparation.cursor.stage == "before_provider"
        await _complete_claim(pool, artifacts, protector, recovered, provider)
        assert provider.calls == 2
        await _assert_single_user_message(pool, scope, session_id, recovered)
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


async def _submit_run(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
    suffix: str,
) -> tuple[str, str]:
    receipt = await PostgresStateStore(pool).submit(
        scope=scope,
        submission=RunSubmission(
            session_id,
            f"submission_{suffix}",
            "recover the accepted turn",
        ),
    )
    outbox_id = await execution_outbox_id(
        pool,
        scope=scope,
        session_id=session_id,
        run_id=receipt.run_id,
    )
    return receipt.run_id, outbox_id


async def _claim(
    claims: PostgresClaimStore,
    scope: RequestScope,
    outbox_id: str,
    suffix: str,
) -> ClaimedExecution | None:
    return await claims.claim_pending_turn(
        scope=scope,
        outbox_id=outbox_id,
        owner_id=f"worker_{suffix}",
        ttl=timedelta(minutes=1),
    )


async def _complete_claim(
    pool: PostgresPool,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
    claimed: ClaimedExecution,
    provider: _CountingProvider | _CrashOnceProvider,
) -> None:
    factory = claim_scoped_agent_factory(
        pool=pool,
        builder=AgentBuilder().provider(provider),
        state=PostgresStateStore(pool),
        artifacts=artifacts,
        protector=protector,
    )
    agent = await factory.hydrate(claimed=claimed)
    outcome = await agent.run(claimed.execution)
    assert outcome in {
        AgentResult("claim recovered"),
        AgentResult("start recovered"),
        AgentResult("provider recovered"),
    }


async def _accepted_status(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
    run_id: str,
) -> str:
    async with pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT status FROM agentos_distributed_accepted_inputs
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
            """,
            (scope.tenant_id, session_id, run_id),
        )
    assert row is not None
    return str(row["status"])


async def _assert_single_user_message(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
    claimed: ClaimedExecution,
) -> None:
    checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(session_id)
    assert checkpoint is not None
    assert [message.role for message in checkpoint.messages] == ["user", "assistant"]
    assert checkpoint.messages[0].id == claimed.execution.input.user_message_id
