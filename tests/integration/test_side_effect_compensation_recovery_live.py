from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

from agentos import AgentBuilder
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCompensationInvocation,
)
from agentos.capabilities.backend import ExecutionBackend, InProcessExecutionBackend
from agentos.capabilities.executor import ToolExecutionError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres.side_effects import PostgresSideEffectStore
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.policies import ResourcePolicy
from agentos.providers import Provider, ProviderRequest, ProviderResponse
from agentos.runtime.checkpoint import RunCheckpoint, SessionCheckpoint
from agentos.runtime.run_commit import RunTerminalStatus
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import (
    CompensationAttemptId,
    SideEffectRecord,
    SideEffectResolution,
    SideEffectResolutionKind,
    SideEffectStatus,
)
from tests.integration._distributed_failure_support import (
    ProcessCrash,
    expire_and_reclaim,
)
from tests.integration._side_effect_resolution_support import (
    ReconciliationScenario,
    open_reconciliation_scenario,
    scenario_agent_factory,
    submit_resolution,
)
from tests.integration._side_effect_resolution_truth import (
    assert_resolution_terminal_truth,
    resolution_records,
)


pytestmark = pytest.mark.integration


class _UnexpectedProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def async_complete(self, _request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        raise AssertionError("compensation must not call the provider")


@dataclass(slots=True)
class _CrashBeforeCompensationBackend(InProcessExecutionBackend):
    fired: bool = False

    async def execute_compensation(
        self,
        tool: RegisteredTool,
        invocation: ToolCompensationInvocation,
        *,
        resource_policy: ResourcePolicy,
    ) -> None:
        if not self.fired:
            self.fired = True
            raise ProcessCrash
        await super().execute_compensation(
            tool,
            invocation,
            resource_policy=resource_policy,
        )


class _CrashBeforeCompensationCommitStore(PostgresSideEffectStore):
    def __init__(self, database: PostgresPool) -> None:
        super().__init__(database)
        self.fired = False

    async def complete_compensation(
        self,
        *,
        attempt_id: CompensationAttemptId,
        guard: RunWriteGuard,
    ) -> SideEffectRecord:
        if not self.fired:
            self.fired = True
            raise ProcessCrash
        return await super().complete_compensation(
            attempt_id=attempt_id,
            guard=guard,
        )


@dataclass(slots=True)
class _TerminalCrash:
    fired: bool = False


class _CrashBeforeTerminalStore(PostgresStateStore):
    def __init__(
        self,
        database: PostgresPool,
        crash: _TerminalCrash,
        *,
        scope: RequestScope | None = None,
    ) -> None:
        super().__init__(database, scope=scope)
        self._crash = crash

    def bind(self, scope: RequestScope) -> _CrashBeforeTerminalStore:
        return _CrashBeforeTerminalStore(
            self._database,
            self._crash,
            scope=scope,
        )

    async def commit_terminal(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        status: RunTerminalStatus,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        if not self._crash.fired:
            self._crash.fired = True
            raise ProcessCrash
        return await super().commit_terminal(
            checkpoint=checkpoint,
            run_id=run_id,
            turn_id=turn_id,
            status=status,
            guard=guard,
        )


def test_live_compensation_recovers_when_crashed_before_handler() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_crash_before_handler())


async def _verify_crash_before_handler() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.COMPENSATABLE)
    try:
        claimed = await submit_resolution(scenario, _resolution(scenario))
        backend = _CrashBeforeCompensationBackend()
        provider = _UnexpectedProvider()
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=_builder(scenario, provider, backend),
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        with pytest.raises(ProcessCrash):
            await agent.run(claimed.claimed.execution)

        record = await _assert_intermediate(scenario, SideEffectStatus.COMPENSATING)
        operation_id = record.compensation_operation_id
        assert operation_id is not None
        assert record.compensation_attempt == 1
        assert scenario.probe.compensation_invocations == []

        await _recover_and_finish(scenario)

        final = (await resolution_records(scenario))[0]
        assert final.status is SideEffectStatus.COMPENSATED
        assert final.compensation_operation_id == operation_id
        assert final.compensation_attempt == 2
        assert scenario.probe.compensation_invocations == [operation_id]
        assert scenario.probe.compensation_effects == {operation_id}
        await assert_resolution_terminal_truth(
            scenario,
            "failed",
            source_consumed=False,
        )
    finally:
        await scenario.close()


def test_live_compensation_retries_same_id_after_effect_before_commit() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_crash_after_effect())


async def _verify_crash_after_effect() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.COMPENSATABLE)
    try:
        claimed = await submit_resolution(scenario, _resolution(scenario))
        provider = _UnexpectedProvider()
        store = _CrashBeforeCompensationCommitStore(scenario.pool)
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=_builder(scenario, provider),
            side_effects=store,
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        with pytest.raises(ProcessCrash):
            await agent.run(claimed.claimed.execution)

        record = await _assert_intermediate(scenario, SideEffectStatus.COMPENSATING)
        operation_id = record.compensation_operation_id
        assert operation_id is not None
        assert record.compensation_attempt == 1
        assert scenario.probe.compensation_invocations == [operation_id]
        assert scenario.probe.compensation_effects == {operation_id}

        await _recover_and_finish(scenario)

        final = (await resolution_records(scenario))[0]
        assert final.status is SideEffectStatus.COMPENSATED
        assert final.compensation_operation_id == operation_id
        assert final.compensation_attempt == 2
        assert scenario.probe.compensation_invocations == [operation_id, operation_id]
        assert scenario.probe.compensation_effects == {operation_id}
        await assert_resolution_terminal_truth(
            scenario,
            "failed",
            source_consumed=False,
        )
    finally:
        await scenario.close()


def test_live_compensated_recovery_only_retries_failed_terminal_commit() -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_crash_before_terminal())


async def _verify_crash_before_terminal() -> None:
    scenario = await open_reconciliation_scenario(SideEffectPolicy.COMPENSATABLE)
    try:
        claimed = await submit_resolution(scenario, _resolution(scenario))
        provider = _UnexpectedProvider()
        crash = _TerminalCrash()
        factory = scenario_agent_factory(
            pool=scenario.pool,
            protector=scenario.protector,
            artifacts=scenario.artifacts,
            builder=_builder(scenario, provider),
            state=_CrashBeforeTerminalStore(scenario.pool, crash),
        )
        agent = await factory.hydrate(claimed=claimed.claimed)

        with pytest.raises(ProcessCrash):
            await agent.run(claimed.claimed.execution)

        record = await _assert_intermediate(scenario, SideEffectStatus.COMPENSATED)
        operation_id = record.compensation_operation_id
        assert operation_id is not None
        assert record.compensation_attempt == 1
        assert scenario.probe.compensation_invocations == [operation_id]
        assert scenario.probe.compensation_effects == {operation_id}

        await _recover_and_finish(scenario)

        final = (await resolution_records(scenario))[0]
        assert final.status is SideEffectStatus.COMPENSATED
        assert final.compensation_operation_id == operation_id
        assert final.compensation_attempt == 1
        assert scenario.probe.compensation_invocations == [operation_id]
        assert scenario.probe.compensation_effects == {operation_id}
        await assert_resolution_terminal_truth(
            scenario,
            "failed",
            source_consumed=False,
        )
    finally:
        await scenario.close()


def _resolution(scenario: ReconciliationScenario) -> SideEffectResolution:
    return SideEffectResolution(
        scenario.ambiguous.attempt_id.operation_id,
        SideEffectResolutionKind.COMPENSATE,
    )


def _builder(
    scenario: ReconciliationScenario,
    provider: Provider,
    backend: ExecutionBackend | None = None,
) -> AgentBuilder:
    if backend is None:
        tool = scenario.tool
    else:
        async def compensate(invocation: ToolCompensationInvocation) -> None:
            await backend.execute_compensation(
                scenario.tool,
                invocation,
                resource_policy=ResourcePolicy(),
            )

        tool = replace(
            scenario.tool,
            compensation_handler=compensate,
        )
    return AgentBuilder().provider(provider).tools(
        [tool],
    )


async def _recover_and_finish(scenario: ReconciliationScenario) -> None:
    recovered = await expire_and_reclaim(
        scenario.pool,
        claims=scenario.claims,
        scope=scenario.scope,
        session_id=scenario.session_id,
        owner_id=f"worker_compensation_recovery_{uuid4().hex}",
    )
    provider = _UnexpectedProvider()
    factory = scenario_agent_factory(
        pool=scenario.pool,
        protector=scenario.protector,
        artifacts=scenario.artifacts,
        builder=_builder(scenario, provider),
    )
    agent = await factory.hydrate(claimed=recovered)
    with pytest.raises(
        ToolExecutionError,
        match="compensated side effect failed the run",
    ):
        await agent.run(recovered.execution)
    assert provider.calls == 0


async def _assert_intermediate(
    scenario: ReconciliationScenario,
    status: SideEffectStatus,
) -> SideEffectRecord:
    records = await resolution_records(scenario)
    assert len(records) == 1
    record = records[0]
    assert record.status is status
    run = await PostgresStateStore(scenario.pool).get_run(
        scope=scenario.scope,
        session_id=scenario.session_id,
        run_id=scenario.run_id,
    )
    assert run is not None
    assert run.status is RunStatus.RUNNING
    async with scenario.pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_status,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND payload->>'kind' = 'terminal') AS terminal_outbox_count
            """,
            (
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id,
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id,
            ),
        )
    assert truth == {"input_status": "claimed", "terminal_outbox_count": 0}
    return record
