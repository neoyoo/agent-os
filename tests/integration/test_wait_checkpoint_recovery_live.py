from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos._waiting import AgentWaiting, WaitReason, WaitRequest
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolConcurrencyPolicy,
    ToolInvocation,
)
from agentos.distributed.models import RequestScope, RunSubmission
from agentos.distributed.postgres._database import PostgresPool, fetchall
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.claims import PostgresClaimStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderRequest, ProviderResponse, ProviderToolCall
from agentos.runtime import AgentResult
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.execution import ApplyAcceptedInput, RestoreAcceptedTurn
from agentos.runtime.payloads import PayloadProtector
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import (
    SideEffectOutcomeKind,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)
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


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = responses
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        response = self._responses[self.calls]
        self.calls += 1
        return response


class _UnexpectedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        raise AssertionError("pending wait recovery must not call the provider")


@pytest.mark.parametrize("timing", ("before", "after"))
def test_live_waiting_commit_window_recovers_without_dangling_tool_use(
    timing: Literal["before", "after"],
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_waiting_window(timing))


async def _verify_waiting_window(timing: Literal["before", "after"]) -> None:
    settings = live_postgres_settings()
    suffix = uuid4().hex
    scope = RequestScope(f"tenant_waiting_{suffix}", "principal_1")
    session_id = f"session_{suffix}"
    pool = await open_migrated_pool(settings.dsn)
    artifacts = PostgresArtifactStore(pool, NoopBlobStore())
    protector: PayloadProtector = FernetPayloadProtector(
        FernetPayloadProtector.generate_key(),
    )
    claims = PostgresClaimStore(pool)
    reason = WaitReason("human_input", f"approval_{suffix}")
    invocations: list[ToolInvocation] = []

    async def request_waiting(invocation: ToolInvocation) -> WaitRequest:
        invocations.append(invocation)
        return WaitRequest(reason)

    wait_tool = RegisteredTool(
        "request_waiting",
        "Request authoritative waiting.",
        {"type": "object"},
        request_waiting,
        SideEffectPolicy.PURE,
        concurrency_policy=ToolConcurrencyPolicy.EXCLUSIVE,
        wait_capable=True,
    )
    tool_response = ProviderResponse(
        tool_calls=(
            ProviderToolCall("call_wait", "request_waiting", {}),
        ),
    )
    try:
        receipt = await PostgresStateStore(pool).submit(
            scope=scope,
            submission=RunSubmission(
                session_id,
                f"submission_{suffix}",
                "wait for approval",
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
            builder=AgentBuilder().provider(first_provider).tools([wait_tool]),
            state=CrashingStateStore(
                pool,
                CrashController("waiting", timing),
            ),
            artifacts=artifacts,
            protector=protector,
        )
        crashed_agent = await crashing_factory.hydrate(claimed=claimed)
        with pytest.raises(ProcessCrash):
            await crashed_agent.run(claimed.execution)
        assert first_provider.calls == 1
        assert len(invocations) == 1

        crashed_run = await PostgresStateStore(pool).get_run(
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
        )
        assert crashed_run is not None
        crashed_checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(
            session_id,
        )
        assert crashed_checkpoint is not None
        crashed_records = await side_effect_records(
            pool,
            tenant_id=scope.tenant_id,
            session_id=session_id,
        )
        assert len(crashed_records) == 1

        if timing == "before":
            assert crashed_run.status is RunStatus.RUNNING
            assert crashed_checkpoint.execution_cursor is not None
            assert crashed_checkpoint.execution_cursor.stage == "pending_tools"
            assert crashed_records[0].status is SideEffectStatus.STARTED
            assert await _accepted_statuses(pool, scope, session_id) == ("claimed",)
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
            recovered_provider = _UnexpectedProvider()
            recovered_factory = claim_scoped_agent_factory(
                pool=pool,
                builder=(
                    AgentBuilder().provider(recovered_provider).tools([wait_tool])
                ),
                state=PostgresStateStore(pool),
                artifacts=artifacts,
                protector=protector,
            )
            recovered_agent = await recovered_factory.hydrate(claimed=recovered)
            outcome = await recovered_agent.run(recovered.execution)
            assert outcome == AgentWaiting(receipt.run_id, reason)
            assert recovered_provider.calls == 0
            assert len(invocations) == 2
        else:
            assert crashed_run.status is RunStatus.WAITING
            assert crashed_checkpoint.execution_cursor is None
            assert crashed_records[0].status is SideEffectStatus.COMPLETED
            assert await _accepted_statuses(pool, scope, session_id) == ("committed",)
            assert await claims.recover_expired(scope=scope, limit=1) == ()
            assert await claims.claim_pending_turn(
                scope=scope,
                outbox_id=outbox_id,
                owner_id=f"worker_duplicate_{suffix}",
                ttl=timedelta(minutes=1),
            ) is None

        await _assert_waiting_truth(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            user_message_id=claimed.execution.input.user_message_id,
            expected_attempts=2 if timing == "before" else 1,
        )
        if timing == "before":
            first, second = invocations
            assert first.context.invocation_id == second.context.invocation_id
            assert first.context.operation_id == second.context.operation_id
            assert (first.context.attempt, second.context.attempt) == (1, 2)

        command = DurableRunCommand(
            receipt.run_id,
            f"command_{suffix}",
            "resume",
        )
        command_receipt = await PostgresStateStore(pool).submit_command(
            scope=scope,
            session_id=session_id,
            command=command,
        )
        assert command_receipt.duplicate is False
        continuation_outbox = await execution_outbox_id(
            pool,
            scope=scope,
            session_id=session_id,
            run_id=receipt.run_id,
            latest=True,
        )
        continuation = await claims.claim_pending_turn(
            scope=scope,
            outbox_id=continuation_outbox,
            owner_id=f"worker_continuation_{suffix}",
            ttl=timedelta(minutes=1),
        )
        assert continuation is not None
        assert type(continuation.execution.preparation) is ApplyAcceptedInput
        final_provider = _ScriptedProvider([ProviderResponse("approved")])
        continuation_factory = claim_scoped_agent_factory(
            pool=pool,
            builder=AgentBuilder().provider(final_provider).tools([wait_tool]),
            state=PostgresStateStore(pool),
            artifacts=artifacts,
            protector=protector,
        )
        continuation_agent = await continuation_factory.hydrate(claimed=continuation)
        final_outcome = await continuation_agent.run(continuation.execution)
        assert final_outcome == AgentResult("approved")
        assert final_provider.calls == 1
        assert len(invocations) == (2 if timing == "before" else 1)

        final_checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(
            session_id,
        )
        assert final_checkpoint is not None
        assert final_checkpoint.execution_cursor is None
        assert [message.role for message in final_checkpoint.messages] == [
            "user",
            "assistant",
            "assistant",
        ]
        old_assistant = final_checkpoint.messages[1]
        assert old_assistant.tool_calls[0].id == "call_wait"
        assert old_assistant.id not in final_checkpoint.active_refs
        assert final_checkpoint.active_refs == (
            final_checkpoint.messages[0].id,
            final_checkpoint.messages[2].id,
        )
        assert await _accepted_statuses(pool, scope, session_id) == (
            "committed",
            "committed",
        )
    finally:
        await artifacts.close()
        await cleanup_tenant(pool, scope.tenant_id)
        await pool.close()


async def _assert_waiting_truth(
    pool: PostgresPool,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    user_message_id: str,
    expected_attempts: int,
) -> None:
    run = await PostgresStateStore(pool).get_run(
        scope=scope,
        session_id=session_id,
        run_id=run_id,
    )
    assert run is not None
    assert run.status is RunStatus.WAITING
    checkpoint = await PostgresStateStore(pool).bind(scope).load_checkpoint(session_id)
    assert checkpoint is not None
    assert checkpoint.execution_cursor is None
    assert [message.role for message in checkpoint.messages] == ["user", "assistant"]
    assert checkpoint.active_refs == (user_message_id,)
    records = await side_effect_records(
        pool,
        tenant_id=scope.tenant_id,
        session_id=session_id,
    )
    assert len(records) == expected_attempts
    current = records[-1]
    assert current.status is SideEffectStatus.COMPLETED
    assert current.outcome_kind is SideEffectOutcomeKind.WAIT_CONTROL
    if expected_attempts == 2:
        assert records[0].status is SideEffectStatus.RESOLVED
        assert records[0].resolution is SideEffectResolutionOutcome.SUPERSEDED


async def _accepted_statuses(
    pool: PostgresPool,
    scope: RequestScope,
    session_id: str,
) -> tuple[str, ...]:
    async with pool.connection() as connection:
        rows = await fetchall(
            connection,
            """
            SELECT status FROM agentos_distributed_accepted_inputs
            WHERE tenant_id = %s AND session_id = %s
            ORDER BY accepted_at, turn_id
            """,
            (scope.tenant_id, session_id),
        )
    return tuple(str(row["status"]) for row in rows)
