from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, SideEffectPolicy, ToolInvocation
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall
from agentos.runtime import AgentResult
from agentos.runtime.execution import RestoreAcceptedTurn
from agentos.runtime.payloads import PayloadProtector
from agentos.security import FernetPayloadProtector
from tests.integration._distributed_failure_support import (
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


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = responses
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        response = self._responses[self.calls]
        self.calls += 1
        return response


def test_live_partial_tool_batch_reuses_completed_ledger_result() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_partial_tool_recovery())


async def _verify_partial_tool_recovery() -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_partial_tool_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    calls = {"call_1": 0, "call_2": 0}
    crash_second = True

    async def lookup(invocation: ToolInvocation) -> str:
        nonlocal crash_second
        call_id = invocation.context.tool_call_id
        calls[call_id] += 1
        if call_id == "call_2" and crash_second:
            crash_second = False
            raise ProcessCrash
        return f"result:{call_id}"

    tool = RegisteredTool(
        "lookup",
        "Lookup a value.",
        {"type": "object"},
        lookup,
        SideEffectPolicy.PURE,
    )
    tool_response = ProviderResponse(
        tool_calls=(
            ProviderToolCall("call_1", "lookup", {"query": "first"}),
            ProviderToolCall("call_2", "lookup", {"query": "second"}),
        ),
    )
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "recover a partially completed tool batch",
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

        first_provider = _ScriptedProvider([tool_response])
        first_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(first_provider).tools([tool]),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        )
        first_agent = await first_factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await first_agent.run(claimed.execution)

        assert first_provider.calls == 1
        assert calls == {"call_1": 1, "call_2": 1}

        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_recovered_{suffix}",
        )
        preparation = recovered.execution.preparation
        assert type(preparation) is RestoreAcceptedTurn
        assert preparation.cursor.stage == "pending_tools"

        recovered_provider = _ScriptedProvider([ProviderResponse("recovered")])
        recovered_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(recovered_provider).tools([tool]),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        )
        recovered_agent = await recovered_factory.hydrate(claimed=recovered)
        outcome = await recovered_agent.run(recovered.execution)

        assert outcome == AgentResult("recovered")
        assert recovered_provider.calls == 1
        assert calls == {"call_1": 1, "call_2": 2}
        checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(
            session_id,
        )
        assert checkpoint is not None
        assert [message.role for message in checkpoint.messages] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "assistant",
        ]
        assert [
            checkpoint.messages[2].tool_call_id,
            checkpoint.messages[3].tool_call_id,
        ] == ["call_1", "call_2"]
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()
