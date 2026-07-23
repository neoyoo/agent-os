from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.capabilities import RegisteredTool, SideEffectPolicy, ToolInvocation
from agentos.capabilities.executor import ToolExecutionError
from agentos.distributed.errors import SideEffectInFlightError
from agentos.distributed.postgres._database import PostgresPool
from agentos.distributed.postgres.artifacts import PostgresArtifactStore
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.providers import ProviderResponse, ProviderToolCall
from agentos.runtime import AgentResult
from agentos.runtime.durable_commands import DurableRunCommand
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)
from tests.integration._cancel_recovery_support import (
    ScriptedProvider,
    cancel_agent_factory,
    open_claimed_run,
)
from tests.integration._cancel_recovery_truth import (
    assert_cancelled_truth,
    cancel_truth_snapshot,
    pending_assistant_message_id,
)
from tests.integration._distributed_failure_support import (
    CrashController,
    CrashingStateStore,
    NoopBlobStore,
    ProcessCrash,
    side_effect_records,
)


pytestmark = pytest.mark.integration


class _BeforeStartBarrierStore(PostgresSideEffectStore):
    def __init__(self, database: PostgresPool) -> None:
        super().__init__(database)
        self.reserved = asyncio.Event()
        self.release = asyncio.Event()

    async def mark_started(
        self,
        *,
        attempt_id: SideEffectAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        self.reserved.set()
        await self.release.wait()
        return await super().mark_started(attempt_id=attempt_id, guard=guard)


def test_live_running_cancel_resolves_reserved_before_handler() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_reserved_cancel())


async def _verify_reserved_cancel() -> None:
    run = await open_claimed_run("reserved")
    artifacts = PostgresArtifactStore(run.pool, NoopBlobStore())
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_charge", "charge", {}),),
            ),
        ],
    )
    handler_calls = 0

    async def charge(_invocation: ToolInvocation) -> str:
        nonlocal handler_calls
        handler_calls += 1
        return "charged"

    side_effects = _BeforeStartBarrierStore(run.pool)
    factory = cancel_agent_factory(
        run=run,
        builder=AgentBuilder().provider(provider).tools(
            [
                RegisteredTool(
                    "charge",
                    "Charge once.",
                    {"type": "object"},
                    charge,
                    SideEffectPolicy.DEDUPLICATED,
                ),
            ],
        ),
        state=PostgresStateStore(run.pool),
        artifacts=artifacts,
        side_effects=side_effects,
    )
    agent = await factory.hydrate(claimed=run.claimed)
    execution = asyncio.create_task(agent.run(run.claimed.execution))
    command = DurableRunCommand(run.run_id, f"cancel_{uuid4().hex}", "cancel")
    try:
        await asyncio.wait_for(side_effects.reserved.wait(), timeout=5)
        records = await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        )
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.RESERVED
        pending_assistant_id = await pending_assistant_message_id(run)

        accepted = await PostgresStateStore(run.pool).submit_command(
            scope=run.scope,
            session_id=run.session_id,
            command=command,
        )

        records = await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        )
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.RESOLVED
        assert (
            records[0].resolution
            is SideEffectResolutionOutcome.CANCELLED_BEFORE_START
        )
        assert handler_calls == 0
        await assert_cancelled_truth(
            run.pool,
            scope=run.scope,
            session_id=run.session_id,
            run_id=run.run_id,
            command_id=command.command_id,
            aggregate_version=accepted.aggregate_version,
            cleared_pending_assistant_id=pending_assistant_id,
        )
        side_effects.release.set()
        with pytest.raises(ToolExecutionError):
            await asyncio.wait_for(execution, timeout=5)
        assert handler_calls == 0
        records = await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        )
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.RESOLVED
    finally:
        side_effects.release.set()
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await artifacts.close()
        await run.close()


def test_live_cancel_rejects_started_effect_and_worker_can_complete() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_started_cancel())


async def _verify_started_cancel() -> None:
    run = await open_claimed_run("started")
    artifacts = PostgresArtifactStore(run.pool, NoopBlobStore())
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_charge", "charge", {}),),
            ),
            ProviderResponse("effect committed"),
        ],
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    handler_calls = 0

    async def charge(_invocation: ToolInvocation) -> str:
        nonlocal handler_calls
        handler_calls += 1
        entered.set()
        await release.wait()
        return "charged"

    side_effects = PostgresSideEffectStore(run.pool)
    factory = cancel_agent_factory(
        run=run,
        builder=AgentBuilder().provider(provider).tools(
            [
                RegisteredTool(
                    "charge",
                    "Charge once.",
                    {"type": "object"},
                    charge,
                    SideEffectPolicy.DEDUPLICATED,
                ),
            ],
        ),
        state=PostgresStateStore(run.pool),
        artifacts=artifacts,
        side_effects=side_effects,
    )
    agent = await factory.hydrate(claimed=run.claimed)
    execution = asyncio.create_task(agent.run(run.claimed.execution))
    command = DurableRunCommand(run.run_id, f"cancel_{uuid4().hex}", "cancel")
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        records = await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        )
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.STARTED
        before = await cancel_truth_snapshot(run, command.command_id)

        with pytest.raises(SideEffectInFlightError):
            await PostgresStateStore(run.pool).submit_command(
                scope=run.scope,
                session_id=run.session_id,
                command=command,
            )

        assert await cancel_truth_snapshot(run, command.command_id) == before
        release.set()
        outcome = await asyncio.wait_for(execution, timeout=5)

        assert outcome == AgentResult("effect committed")
        assert provider.calls == 2
        assert handler_calls == 1
        records = await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        )
        assert len(records) == 1
        assert records[0].status is SideEffectStatus.COMPLETED
        final = await PostgresStateStore(run.pool).get_run(
            scope=run.scope,
            session_id=run.session_id,
            run_id=run.run_id,
        )
        assert final is not None
        assert final.status is RunStatus.COMPLETED
    finally:
        release.set()
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
        await artifacts.close()
        await run.close()


def test_live_cancel_after_ledger_completion_preserves_result() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_completed_cancel())


async def _verify_completed_cancel() -> None:
    run = await open_claimed_run("completed")
    artifacts = PostgresArtifactStore(run.pool, NoopBlobStore())
    provider = ScriptedProvider(
        [
            ProviderResponse(
                tool_calls=(ProviderToolCall("call_charge", "charge", {}),),
            ),
        ],
    )
    handler_calls = 0

    async def charge(_invocation: ToolInvocation) -> str:
        nonlocal handler_calls
        handler_calls += 1
        return "charged"

    factory = cancel_agent_factory(
        run=run,
        builder=AgentBuilder().provider(provider).tools(
            [
                RegisteredTool(
                    "charge",
                    "Charge once.",
                    {"type": "object"},
                    charge,
                    SideEffectPolicy.DEDUPLICATED,
                ),
            ],
        ),
        state=CrashingStateStore(
            run.pool,
            CrashController("after_tools", "before"),
        ),
        artifacts=artifacts,
        side_effects=PostgresSideEffectStore(run.pool),
    )
    try:
        agent = await factory.hydrate(claimed=run.claimed)
        with pytest.raises(ProcessCrash):
            await agent.run(run.claimed.execution)

        completed = await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        )
        assert len(completed) == 1
        assert completed[0].status is SideEffectStatus.COMPLETED
        assert handler_calls == 1
        pending_assistant_id = await pending_assistant_message_id(run)
        command = DurableRunCommand(
            run.run_id,
            f"cancel_{uuid4().hex}",
            "cancel",
        )

        accepted = await PostgresStateStore(run.pool).submit_command(
            scope=run.scope,
            session_id=run.session_id,
            command=command,
        )

        assert await side_effect_records(
            run.pool,
            tenant_id=run.scope.tenant_id,
            session_id=run.session_id,
        ) == completed
        assert handler_calls == 1
        await assert_cancelled_truth(
            run.pool,
            scope=run.scope,
            session_id=run.session_id,
            run_id=run.run_id,
            command_id=command.command_id,
            aggregate_version=accepted.aggregate_version,
            cleared_pending_assistant_id=pending_assistant_id,
        )
    finally:
        await artifacts.close()
        await run.close()
