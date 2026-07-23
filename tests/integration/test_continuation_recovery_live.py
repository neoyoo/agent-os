from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos._builder_distributed import ClaimScopedAgentFactory
from agentos._waiting import AgentWaiting, WaitReason, WaitRequest
from agentos.capabilities import RegisteredTool, SideEffectPolicy, ToolInvocation
from agentos.distributed.models import (
    ClaimedExecution,
    RequestScope,
    RunSubmission,
)
from agentos.distributed.postgres._database import PostgresPool, fetchall
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall
from agentos.runtime import AgentResult
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableRunCommand,
)
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
CrashPoint = Literal["claim", "hydrate", "run_start", "provider"]
CrashTiming = Literal["before", "after"]


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = responses
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class _CrashOnceProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            raise ProcessCrash
        return ProviderResponse("continuation recovered")


class _CrashingClaimStore(PostgresClaimStore):
    def __init__(self, database: PostgresPool, timing: CrashTiming) -> None:
        super().__init__(database)
        self.timing = timing
        self.fired = False

    async def claim_pending_turn(
        self,
        *,
        scope: RequestScope,
        outbox_id: str,
        owner_id: str,
        ttl: timedelta,
    ) -> ClaimedExecution | None:
        self._crash("before")
        claimed = await super().claim_pending_turn(
            scope=scope,
            outbox_id=outbox_id,
            owner_id=owner_id,
            ttl=ttl,
        )
        self._crash("after")
        return claimed

    def _crash(self, timing: CrashTiming) -> None:
        if not self.fired and self.timing == timing:
            self.fired = True
            raise ProcessCrash


class _CrashBeforeHydration:
    def __init__(self, factory: ClaimScopedAgentFactory) -> None:
        self.factory = factory

    async def hydrate(self, *, claimed: ClaimedExecution):  # type: ignore[no-untyped-def]
        del claimed
        raise ProcessCrash


@pytest.mark.parametrize(
    ("point", "timing"),
    (
        ("claim", "before"),
        ("claim", "after"),
        ("hydrate", "before"),
        ("run_start", "before"),
        ("run_start", "after"),
        ("provider", "after"),
    ),
)
def test_live_accepted_continuation_crash_windows_recover(
    point: CrashPoint,
    timing: CrashTiming,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_continuation_window(point, timing))


async def _verify_continuation_window(
    point: CrashPoint,
    timing: CrashTiming,
) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_continuation_{point}_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    wait_tool = _wait_tool(suffix)
    try:
        run_id = await _enter_waiting(
            pool,
            artifacts=artifacts,
            protector=protector,
            scope=scope,
            session_id=session_id,
            suffix=suffix,
            wait_tool=wait_tool,
        )
        command = DurableRunCommand(run_id, f"command_{suffix}", "resume")
        receipt = await PostgresStateStore(pool).submit_command(
            scope=scope,
            session_id=session_id,
            command=command,
        )
        assert receipt.duplicate is False
        outbox_id = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=run_id,
            latest=True,
        )

        recovered: ClaimedExecution
        provider: _ScriptedProvider | _CrashOnceProvider
        if point == "claim":
            crashing = _CrashingClaimStore(pool, timing)
            with pytest.raises(ProcessCrash):
                await _claim(crashing, scope, outbox_id, suffix)
            if timing == "before":
                recovered = await _require_claim(
                    claims,
                    scope,
                    outbox_id,
                    suffix,
                )
            else:
                recovered = await expire_and_reclaim(
                    pool,
                    claims=claims,
                    scope=scope,
                    session_id=session_id,
                    owner_id=f"worker_recovered_{suffix}",
                )
            provider = _ScriptedProvider([ProviderResponse("continuation recovered")])
        else:
            claimed = await _require_claim(claims, scope, outbox_id, suffix)
            base_provider = _ScriptedProvider(
                [ProviderResponse("continuation recovered")],
            )
            state: PostgresStateStore = PostgresStateStore(pool)
            if point == "run_start":
                state = CrashingStateStore(
                    pool,
                    CrashController("run_start", timing),
                )
            factory = _factory(
                pool,
                artifacts,
                protector,
                base_provider,
                wait_tool,
                state,
            )
            if point == "hydrate":
                with pytest.raises(ProcessCrash):
                    await _CrashBeforeHydration(factory).hydrate(claimed=claimed)
                provider = base_provider
            elif point == "run_start":
                agent = await factory.hydrate(claimed=claimed)
                with pytest.raises(ProcessCrash):
                    await agent.run(claimed.execution)
                assert base_provider.calls == 0
                provider = base_provider
            else:
                provider = _CrashOnceProvider()
                crashing_factory = _factory(
                    pool,
                    artifacts,
                    protector,
                    provider,
                    wait_tool,
                    PostgresStateStore(pool),
                )
                agent = await crashing_factory.hydrate(claimed=claimed)
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

        assert type(recovered.execution.input) is AcceptedContinuationInput
        if point == "provider":
            preparation = recovered.execution.preparation
            assert type(preparation) is RestoreAcceptedTurn
            assert preparation.cursor.stage == "before_provider"
        else:
            assert type(recovered.execution.preparation) is ApplyAcceptedInput

        final_factory = _factory(
            pool,
            artifacts,
            protector,
            provider,
            wait_tool,
            PostgresStateStore(pool),
        )
        final_agent = await final_factory.hydrate(claimed=recovered)
        outcome = await final_agent.run(recovered.execution)
        assert outcome == AgentResult("continuation recovered")
        expected_calls = 2 if point == "provider" else 1
        assert provider.calls == expected_calls
        await _assert_final_truth(pool, scope, session_id)
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


async def _enter_waiting(
    pool: PostgresPool,
    *,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
    scope: RequestScope,
    session_id: str,
    suffix: str,
    wait_tool: RegisteredTool,
) -> str:
    receipt = await PostgresStateStore(pool).submit(
        scope=scope,
        submission=RunSubmission(
            session_id,
            f"submission_{suffix}",
            "wait before continuation recovery",
        ),
    )
    outbox_id = await execution_outbox_id(
        pool,
        scope=scope,
        session_id=session_id,
        run_id=receipt.run_id,
    )
    claimed = await _require_claim(
        PostgresClaimStore(pool),
        scope,
        outbox_id,
        suffix,
    )
    provider = _ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_wait", "wait", {}),),
            ),
        ],
    )
    factory = _factory(
        pool,
        artifacts,
        protector,
        provider,
        wait_tool,
        PostgresStateStore(pool),
    )
    agent = await factory.hydrate(claimed=claimed)
    outcome = await agent.run(claimed.execution)
    assert outcome == AgentWaiting(
        receipt.run_id,
        WaitReason("human_input", f"approval_{suffix}"),
    )
    return receipt.run_id


def _wait_tool(suffix: str) -> RegisteredTool:
    async def wait(_invocation: ToolInvocation) -> WaitRequest:
        return WaitRequest(WaitReason("human_input", f"approval_{suffix}"))

    return RegisteredTool(
        "wait",
        "Wait for approval.",
        {"type": "object"},
        wait,
        SideEffectPolicy.PURE,
        wait_capable=True,
    )


def _factory(
    pool: PostgresPool,
    artifacts: PostgresArtifactStore,
    protector: PayloadProtector,
    provider: _ScriptedProvider | _CrashOnceProvider,
    wait_tool: RegisteredTool,
    state: PostgresStateStore,
) -> ClaimScopedAgentFactory:
    return claim_scoped_agent_factory(
        pool=pool,
        builder=AgentBuilder().provider(provider).tools([wait_tool]),
        state=state,
        artifacts=artifacts,
        protector=protector,
    )


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


async def _require_claim(
    claims: PostgresClaimStore,
    scope: RequestScope,
    outbox_id: str,
    suffix: str,
) -> ClaimedExecution:
    claimed = await _claim(claims, scope, outbox_id, suffix)
    assert claimed is not None
    return claimed


async def _assert_final_truth(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
) -> None:
    checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(session_id)
    assert checkpoint is not None
    assert checkpoint.execution_cursor is None
    assert [message.role for message in checkpoint.messages] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert checkpoint.active_refs == (
        checkpoint.messages[0].id,
        checkpoint.messages[2].id,
    )
    async with pool.connection() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT source_kind, status FROM agentos_distributed_accepted_inputs
            WHERE tenant_id = %s AND session_id = %s
            ORDER BY accepted_at, turn_id
            """,
            (scope.tenant_id, session_id),
        )
    assert rows == [
        {"source_kind": "submission", "status": "committed"},
        {"source_kind": "command", "status": "committed"},
    ]
