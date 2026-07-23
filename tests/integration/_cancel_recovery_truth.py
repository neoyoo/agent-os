from __future__ import annotations

from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres.state import PostgresStateStore
from agentos.runtime.run_state import RunStatus
from tests.integration._cancel_recovery_support import ClaimedRun


async def assert_cancelled_truth(
    pool: PostgresPool,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    command_id: str,
    aggregate_version: int,
    cleared_pending_assistant_id: str | None = None,
) -> None:
    state = PostgresStateStore(pool)
    run = await state.get_run(
        scope=scope,
        session_id=session_id,
        run_id=run_id,
    )
    assert run is not None
    assert run.status is RunStatus.CANCELLED
    assert run.aggregate_version == aggregate_version
    bound = state.bind(scope)
    checkpoint = await bound.latest_checkpoint(session_id, run_id)
    assert checkpoint is not None
    assert checkpoint.aggregate_version == aggregate_version
    loaded = await bound.load_checkpoint(session_id)
    if loaded is not None:
        assert loaded.execution_cursor is None
        if cleared_pending_assistant_id is not None:
            assert cleared_pending_assistant_id not in loaded.active_refs
    async with pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_status,
              (SELECT claim_id FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_claim_id,
              (SELECT fencing_token FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_fence,
              (SELECT checkpoint_id FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_checkpoint_id,
              (SELECT COUNT(*) FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s) AS cursor_count,
              (SELECT COUNT(*) FROM agentos_distributed_commands
               WHERE tenant_id = %s AND command_id = %s) AS command_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND payload->>'kind' = 'cancel') AS cancel_outbox_count,
              (SELECT active_claim_id FROM agentos_distributed_sessions
               WHERE tenant_id = %s AND session_id = %s) AS active_claim_id
            """,
            (
                scope.tenant_id, session_id, run_id,
                scope.tenant_id, session_id, run_id,
                scope.tenant_id, session_id, run_id,
                scope.tenant_id, session_id, run_id,
                scope.tenant_id, session_id, run_id,
                scope.tenant_id, command_id,
                scope.tenant_id, session_id, run_id,
                scope.tenant_id, session_id,
            ),
        )
    assert truth is not None
    assert truth["input_status"] == "committed"
    assert truth["input_claim_id"] is None
    assert truth["input_fence"] is None
    assert type(truth["input_checkpoint_id"]) is str
    assert truth["cursor_count"] == 0
    assert truth["command_count"] == 1
    assert truth["cancel_outbox_count"] == 1
    assert truth["active_claim_id"] is None


async def cancel_truth_snapshot(
    run: ClaimedRun,
    command_id: str,
) -> dict[str, object]:
    async with run.pool.connection() as connection:
        truth = await fetchone(
            connection,
            """
            SELECT
              (SELECT status FROM agentos_distributed_runs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s) AS run_status,
              (SELECT aggregate_version FROM agentos_distributed_runs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s) AS run_version,
              (SELECT fencing_token FROM agentos_distributed_sessions
               WHERE tenant_id = %s AND session_id = %s) AS session_fence,
              (SELECT active_claim_id FROM agentos_distributed_sessions
               WHERE tenant_id = %s AND session_id = %s) AS active_claim_id,
              (SELECT status FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_status,
              (SELECT claim_id FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_claim_id,
              (SELECT fencing_token FROM agentos_distributed_accepted_inputs
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
               ORDER BY accepted_at DESC LIMIT 1) AS input_fence,
              (SELECT payload_json FROM agentos_distributed_execution_cursors
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s) AS cursor_payload,
              (SELECT COUNT(*) FROM agentos_distributed_checkpoints
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s) AS checkpoint_count,
              (SELECT COUNT(*) FROM agentos_distributed_commands
               WHERE tenant_id = %s AND command_id = %s) AS command_count,
              (SELECT COUNT(*) FROM agentos_distributed_outbox
               WHERE tenant_id = %s AND session_id = %s AND run_id = %s
                 AND payload->>'kind' = 'cancel') AS cancel_outbox_count
            """,
            (
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, run.session_id,
                run.scope.tenant_id, run.session_id,
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, run.session_id, run.run_id,
                run.scope.tenant_id, command_id,
                run.scope.tenant_id, run.session_id, run.run_id,
            ),
        )
    assert truth is not None
    return dict(truth)


async def pending_assistant_message_id(run: ClaimedRun) -> str:
    checkpoint = await PostgresStateStore(run.pool).bind(run.scope).load_checkpoint(
        run.session_id,
    )
    assert checkpoint is not None
    cursor = checkpoint.execution_cursor
    assert cursor is not None
    assert cursor.stage == "pending_tools"
    assert cursor.assistant_message_id is not None
    return cursor.assistant_message_id


__all__ = [
    "assert_cancelled_truth",
    "cancel_truth_snapshot",
    "pending_assistant_message_id",
]
