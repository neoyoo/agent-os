from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from agentos.capabilities import SideEffectPolicy
from agentos.distributed.postgres._database import fetchone
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.durable.serialization import execution_cursor_from_json
from agentos.providers import TextPart
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import (
    SideEffectRecord,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)
from tests.integration._distributed_failure_support import side_effect_records
from tests.integration._side_effect_matrix_support import (
    CrashStage,
    ProcessCrash,
    SideEffectMatrixScenario,
    open_side_effect_matrix_scenario,
    publish_recovery_delivery,
)


pytestmark = pytest.mark.integration
_SAFE_POLICIES = {SideEffectPolicy.PURE, SideEffectPolicy.IDEMPOTENT}


@pytest.mark.parametrize(
    "policy",
    tuple(SideEffectPolicy),
    ids=lambda policy: policy.value,
)
@pytest.mark.parametrize("stage", ("reserved", "started", "completed"))
def test_live_side_effect_policy_recovery_matrix(
    policy: SideEffectPolicy,
    stage: CrashStage,
) -> None:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(_verify_matrix_cell(policy, stage))


async def _verify_matrix_cell(
    policy: SideEffectPolicy,
    stage: CrashStage,
) -> None:
    scenario = await open_side_effect_matrix_scenario(policy, stage)
    try:
        with pytest.raises(ProcessCrash):
            await scenario.initial_runner().run_delivery(scenario.original_delivery)

        crashed_records = await _records(scenario)
        assert len(crashed_records) == 1
        crashed = crashed_records[0]
        assert crashed.policy is policy
        assert crashed.status.value == stage
        assert scenario.initial_provider.calls == 1
        assert scenario.ack_queue.outbox_ids == []
        await _assert_execution_truth(
            scenario,
            run_status="running",
            input_status="claimed",
            cursor_count=1,
            cursor_stage="pending_tools",
        )

        recovery_delivery = await publish_recovery_delivery(scenario)
        recovered_runner = scenario.recovery_runner()
        assert await recovered_runner.run_delivery(recovery_delivery) is True
        assert scenario.ack_queue.outbox_ids == [recovery_delivery.outbox_id]
        assert len(scenario.ack_queue.truth_before_ack) == 1

        if stage == "started" and policy not in _SAFE_POLICIES:
            await _assert_unsafe_started_recovery(scenario, crashed)
        else:
            await _assert_completed_recovery(scenario, crashed, stage, policy)
        assert scenario.probe.compensation_calls == 0
        await _assert_execution_truth(
            scenario,
            run_status=(
                "waiting"
                if stage == "started" and policy not in _SAFE_POLICIES
                else "completed"
            ),
            input_status="committed",
            cursor_count=0,
            cursor_stage=None,
        )

        records_before_duplicate = await _records(scenario)
        provider_calls_before_duplicate = scenario.recovery_provider.calls
        handler_attempts_before_duplicate = tuple(scenario.probe.handler_attempts)
        assert await recovered_runner.run_delivery(scenario.original_delivery) is True
        assert scenario.ack_queue.outbox_ids == [
            recovery_delivery.outbox_id,
            scenario.original_delivery.outbox_id,
        ]
        assert len(scenario.ack_queue.truth_before_ack) == 2
        assert scenario.recovery_provider.calls == provider_calls_before_duplicate
        assert tuple(scenario.probe.handler_attempts) == handler_attempts_before_duplicate
        assert await _records(scenario) == records_before_duplicate
        assert await scenario.worker_queue.reclaim(
            topic=EXECUTION_TOPIC,
            consumer_id=f"final_reclaimer_{scenario.suffix}",
            min_idle=timedelta(milliseconds=1),
            limit=4,
        ) == ()
    finally:
        await scenario.close()


async def _assert_completed_recovery(
    scenario: SideEffectMatrixScenario,
    crashed: SideEffectRecord,
    stage: CrashStage,
    policy: SideEffectPolicy,
) -> None:
    records = await _records(scenario)
    current = records[-1]
    assert current.status is SideEffectStatus.COMPLETED
    assert current.attempt_id.operation_id == crashed.attempt_id.operation_id
    assert current.invocation_id == crashed.invocation_id
    assert scenario.recovery_provider.calls == 1
    assert len(scenario.recovery_provider.requests) == 1
    tool_results = tuple(
        item
        for item in scenario.recovery_provider.requests[0].messages
        if item.kind == "tool_result"
    )
    assert len(tool_results) == 1
    assert tool_results[0].tool_call_id == "call_effect"
    assert tool_results[0].content == (TextPart("effect-result"),)
    run = await PostgresStateStore(scenario.pool).get_run(
        scope=scenario.scope,
        session_id=scenario.session_id,
        run_id=scenario.run_id,
    )
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.content == "recovered after side effect"
    if stage == "started" and policy in _SAFE_POLICIES:
        assert [record.attempt_id.attempt for record in records] == [1, 2]
        assert records[0].status is SideEffectStatus.RESOLVED
        assert records[0].resolution is SideEffectResolutionOutcome.SUPERSEDED
        assert scenario.probe.handler_attempts == [1, 2]
    else:
        assert len(records) == 1
        assert scenario.probe.handler_attempts == [1]


async def _assert_unsafe_started_recovery(
    scenario: SideEffectMatrixScenario,
    crashed: SideEffectRecord,
) -> None:
    records = await _records(scenario)
    assert len(records) == 1
    ambiguous = records[0]
    assert ambiguous.attempt_id == crashed.attempt_id
    assert ambiguous.status is SideEffectStatus.AMBIGUOUS
    assert scenario.probe.handler_attempts == [1]
    assert scenario.recovery_provider.calls == 0
    run = await PostgresStateStore(scenario.pool).get_run(
        scope=scenario.scope,
        session_id=scenario.session_id,
        run_id=scenario.run_id,
    )
    assert run is not None
    assert run.status is RunStatus.WAITING
    assert run.wait_reason is not None
    assert run.wait_reason.kind == "side_effect_reconciliation"
    assert run.wait_reason.handle == ambiguous.attempt_id.operation_id
    assert await _reconciliation_source_count(scenario) == 1


async def _records(
    scenario: SideEffectMatrixScenario,
) -> tuple[SideEffectRecord, ...]:
    return await side_effect_records(
        scenario.pool,
        tenant_id=scenario.scope.tenant_id,
        session_id=scenario.session_id,
    )


async def _assert_execution_truth(
    scenario: SideEffectMatrixScenario,
    *,
    run_status: str,
    input_status: str,
    cursor_count: int,
    cursor_stage: str | None,
) -> None:
    async with scenario.pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_runs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS run_status,
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS input_status,
              (SELECT COUNT(*) FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS cursor_count,
              (SELECT payload_json FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s)
                AS cursor_payload
            """,
            _identity(scenario) * 4,
        )
    cursor_payload = truth.pop("cursor_payload")
    actual_cursor_stage = None
    if cursor_payload is not None:
        assert type(cursor_payload) is str
        actual_cursor_stage = execution_cursor_from_json(cursor_payload).stage
    assert truth == {
        "run_status": run_status,
        "input_status": input_status,
        "cursor_count": cursor_count,
    }
    assert actual_cursor_stage == cursor_stage


async def _reconciliation_source_count(scenario: SideEffectMatrixScenario) -> int:
    async with scenario.pool.connection() as connection:
        row = await fetchone(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM agentos_distributed_reconciliation_sources
            WHERE tenant_id = %s AND session_id = %s AND run_id = %s
            """,
            _identity(scenario),
        )
    assert row is not None
    return int(row["count"])


def _identity(scenario: SideEffectMatrixScenario) -> tuple[str, str, str]:
    return scenario.scope.tenant_id, scenario.session_id, scenario.run_id
