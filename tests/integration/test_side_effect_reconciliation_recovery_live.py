from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos._waiting import AgentWaiting, WaitReason
from agentos.capabilities import RegisteredTool, SideEffectPolicy, ToolInvocation
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import fetchone
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall
from agentos.runtime.execution import RestoreAcceptedTurn
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import SideEffectStatus
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
    side_effect_records,
)


pytestmark = pytest.mark.integration


class _SingleResponseProvider:
    def __init__(self, response: ProviderResponse) -> None:
        self._response = response
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        return self._response


class _UnexpectedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        raise AssertionError("ambiguous recovery must not call the provider")


def test_live_ambiguous_takeover_adopts_the_new_claim_fence() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_ambiguous_takeover())


async def _verify_ambiguous_takeover() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_ambiguous_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    handler_calls = 0

    async def unsafe_tool(_invocation: ToolInvocation) -> str:
        nonlocal handler_calls
        handler_calls += 1
        raise RuntimeError("external outcome is unknown")

    tool = RegisteredTool(
        "unsafe_tool",
        "Perform an unsafe external operation.",
        {"type": "object"},
        unsafe_tool,
        SideEffectPolicy.NON_RETRYABLE,
    )
    tool_response = ProviderResponse(
        tool_calls=(ProviderToolCall("call_unsafe", "unsafe_tool", {}),),
    )
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "perform the external operation",
            ),
        )
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        claimed = await claims.claim_pending_turn(
            scope=scope,
            outbox_id=outbox_id,
            owner_id=f"worker_crash_{suffix}",
            ttl=timedelta(minutes=1),
        )
        assert claimed is not None
        first_provider = _SingleResponseProvider(tool_response)
        crashing_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(first_provider).tools([tool]),
            state=CrashingStateStore(
                pool,
                CrashController("waiting", "before"),
            ),
            artifacts=artifacts,
            protector=protector,
        )
        crashed_agent = await crashing_factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await crashed_agent.run(claimed.execution)

        assert first_provider.calls == 1
        assert handler_calls == 1
        records = await side_effect_records(
            pool,
            tenant_id=scope.tenant_id,
            session_id=session_id,
        )
        assert len(records) == 1
        ambiguous = records[0]
        assert ambiguous.status is SideEffectStatus.AMBIGUOUS
        assert ambiguous.claim_id == claimed.execution.guard.claim_id
        assert ambiguous.fencing_token == claimed.execution.guard.fencing_token

        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_recovered_{suffix}",
        )
        assert type(recovered.execution.preparation) is RestoreAcceptedTurn
        recovered_provider = _UnexpectedProvider()
        recovered_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(recovered_provider).tools([tool]),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        )
        recovered_agent = await recovered_factory.hydrate(claimed=recovered)
        reason = WaitReason(
            "side_effect_reconciliation",
            ambiguous.attempt_id.operation_id,
        )

        outcome = await recovered_agent.run(recovered.execution)

        assert outcome == AgentWaiting(receipt.run_id, reason)
        assert recovered_provider.calls == 0
        assert handler_calls == 1
        run = await PostgresStateStore(pool).get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert run is not None
        assert run.status is RunStatus.WAITING
        adopted = (
            await side_effect_records(
                pool,
                tenant_id=scope.tenant_id,
                session_id=session_id,
            )
        )[0]
        assert adopted.status is SideEffectStatus.AMBIGUOUS
        assert adopted.claim_id == recovered.execution.guard.claim_id
        assert adopted.fencing_token == recovered.execution.guard.fencing_token
        async with pool.connection() as connection:
            source = await fetchone(
                connection,
                """
                SELECT COUNT(*) AS count
                FROM agentos_distributed_reconciliation_sources
                WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                """,
                (scope.tenant_id, session_id, receipt.run_id),
            )
        assert source is not None
        assert source["count"] == 1
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()
