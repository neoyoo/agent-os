from agentos.distributed.postgres._database import AsyncConnection
from agentos.runtime.run_state import RunState


async def update_run(
    connection: AsyncConnection,
    tenant_id: str,
    state: RunState,
) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_runs
        SET status = %s, wait_kind = NULL, wait_handle = NULL,
            wait_detail = NULL, wait_not_before = NULL,
            aggregate_version = %s, result_content = NULL,
            updated_at = clock_timestamp()
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        """,
        (
            state.status.value,
            state.aggregate_version,
            tenant_id,
            state.session_id,
            state.run_id,
        ),
    )


__all__ = ["update_run"]
