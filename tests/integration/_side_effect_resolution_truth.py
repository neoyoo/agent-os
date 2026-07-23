from __future__ import annotations

from agentos.distributed.postgres._database import fetchone
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.runtime.run_state import RunStatus
from agentos.runtime.side_effect_types import SideEffectRecord
from tests.integration._distributed_failure_support import side_effect_records
from tests.integration._side_effect_resolution_support import ReconciliationScenario


async def resolution_records(
    scenario: ReconciliationScenario,
) -> tuple[SideEffectRecord, ...]:
    return await side_effect_records(
        scenario.pool,
        tenant_id=scenario.scope.tenant_id,
        session_id=scenario.session_id,
    )


async def assert_resolution_terminal_truth(
    scenario: ReconciliationScenario,
    status: str,
    *,
    source_consumed: bool,
) -> None:
    state = PostgresStateStore(scenario.pool)
    run = await state.get_run(
        scope=scenario.scope,
        session_id=scenario.session_id,
        run_id=scenario.run_id,
    )
    assert run is not None
    assert run.status is RunStatus(status)
    checkpoint = await state.bind(scenario.scope).latest_checkpoint(
        scenario.session_id,
        scenario.run_id,
    )
    assert checkpoint is not None
    assert checkpoint.aggregate_version == run.aggregate_version
    async with scenario.pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_status,
              (SELECT COUNT(*) FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s) AS cursor_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND payload->>'kind' = 'terminal'
                 AND payload->>'status' = %s) AS terminal_outbox_count,
              (SELECT COUNT(*) FROM agentos_distributed_reconciliation_sources
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND resolution_command_id IS NOT NULL) AS resolved_source_count,
              (SELECT COUNT(*) FROM agentos_distributed_reconciliation_sources
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND consumed_checkpoint_id IS NOT NULL) AS consumed_source_count
            """,
            (
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id,
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id,
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id, status,
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id,
                scenario.scope.tenant_id, scenario.session_id, scenario.run_id,
            ),
        )
    assert truth == {
        "input_status": "committed",
        "cursor_count": 0,
        "terminal_outbox_count": 1,
        "resolved_source_count": 1,
        "consumed_source_count": int(source_consumed),
    }


__all__ = ["assert_resolution_terminal_truth", "resolution_records"]
