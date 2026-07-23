from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal
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
from agentos.runtime.execution import ApplyAcceptedInput, RestoreAcceptedTurn
from agentos.runtime.payloads import PayloadProtector
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


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = responses
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        response = self._responses[self.calls]
        self.calls += 1
        return response


@pytest.mark.parametrize("timing", ("before", "after"))
def test_live_before_provider_checkpoint_recovers_exact_turn(
    timing: Literal["before", "after"],
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_before_provider_recovery(timing))


async def _verify_before_provider_recovery(
    timing: Literal["before", "after"],
) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_checkpoint_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    blob_store = NoopBlobStore()
    artifacts = PostgresArtifactStore(pool, blob_store)
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "recover the exact accepted turn",
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
        assert type(claimed.execution.preparation) is ApplyAcceptedInput

        first_provider = _CountingProvider("must not be called")
        crashing_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(first_provider),
            state=CrashingStateStore(
                pool,
                CrashController("before_provider", timing),
            ),
            artifacts=artifacts,
            protector=protector,
        )
        crashed_agent = await crashing_factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await crashed_agent.run(claimed.execution)
        assert first_provider.calls == 0

        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_recovered_{suffix}",
        )
        expected_preparation = (
            ApplyAcceptedInput if timing == "before" else RestoreAcceptedTurn
        )
        assert type(recovered.execution.preparation) is expected_preparation

        recovered_provider = _CountingProvider("recovered")
        recovered_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(recovered_provider),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        )
        recovered_agent = await recovered_factory.hydrate(claimed=recovered)
        outcome = await recovered_agent.run(recovered.execution)

        assert outcome == AgentResult("recovered")
        assert recovered_provider.calls == 1
        checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(
            session_id,
        )
        assert checkpoint is not None
        assert [message.role for message in checkpoint.messages] == [
            "user",
            "assistant",
        ]
        assert checkpoint.messages[0].id == claimed.execution.input.user_message_id
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


@pytest.mark.parametrize(
    ("stage", "timing", "recovered_stage", "recovered_provider_calls"),
    (
        ("pending_tools", "before", "before_provider", 2),
        ("pending_tools", "after", "pending_tools", 1),
        ("after_tools", "before", "pending_tools", 1),
        ("after_tools", "after", "after_tools", 1),
    ),
)
def test_live_tool_cursor_window_recovers_without_duplicate_effect(
    stage: Literal["pending_tools", "after_tools"],
    timing: Literal["before", "after"],
    recovered_stage: Literal["before_provider", "pending_tools", "after_tools"],
    recovered_provider_calls: int,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(
            _verify_tool_cursor_recovery(
                stage,
                timing,
                recovered_stage,
                recovered_provider_calls,
            ),
        )


async def _verify_tool_cursor_recovery(
    stage: Literal["pending_tools", "after_tools"],
    timing: Literal["before", "after"],
    recovered_stage: Literal["before_provider", "pending_tools", "after_tools"],
    recovered_provider_calls: int,
) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_cursor_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    tool_calls = 0

    async def lookup(_invocation: ToolInvocation) -> str:
        nonlocal tool_calls
        tool_calls += 1
        return "found"

    tool = RegisteredTool(
        "lookup",
        "Lookup a value.",
        {"type": "object"},
        lookup,
        SideEffectPolicy.PURE,
    )
    tool_response = ProviderResponse(
        tool_calls=(
            ProviderToolCall("call_1", "lookup", {"query": "saved"}),
        ),
    )
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "recover the tool batch",
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
        crashing_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(first_provider).tools([tool]),
            state=CrashingStateStore(
                pool,
                CrashController(stage, timing),
            ),
            artifacts=artifacts,
            protector=protector,
        )
        crashed_agent = await crashing_factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await crashed_agent.run(claimed.execution)
        assert first_provider.calls == 1
        assert tool_calls == (1 if stage == "after_tools" else 0)

        recovered = await expire_and_reclaim(
            pool,
            claims=claims,
            scope=scope,
            session_id=session_id,
            owner_id=f"worker_recovered_{suffix}",
        )
        preparation = recovered.execution.preparation
        assert type(preparation) is RestoreAcceptedTurn
        assert preparation.cursor.stage == recovered_stage

        recovered_responses = (
            [tool_response, ProviderResponse("recovered")]
            if recovered_stage == "before_provider"
            else [ProviderResponse("recovered")]
        )
        recovered_provider = _ScriptedProvider(recovered_responses)
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
        assert recovered_provider.calls == recovered_provider_calls
        assert tool_calls == 1
        checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(
            session_id,
        )
        assert checkpoint is not None
        assert [message.role for message in checkpoint.messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
        assert checkpoint.messages[1].tool_calls[0].id == "call_1"
        assert checkpoint.messages[2].tool_call_id == "call_1"
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()
