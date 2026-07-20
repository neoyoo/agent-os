from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from agentos.artifacts import ArtifactNotFoundError
from agentos.distributed.errors import (
    ActiveRunConflictError,
    CheckpointConflictError,
    RunSubmissionConflictError,
)
from agentos.distributed.models import (
    RequestScope,
    RunReadModel,
    RunSubmission,
    RunSubmissionReceipt,
)
from agentos.distributed.postgres._database import (
    AsyncConnection,
    Row,
    fetchall,
    fetchone,
)
from agentos.distributed.postgres._records import run_state_from_row
from agentos.runtime.run import AgentResult
from agentos.runtime.run_state import RunState


async def ensure_session(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
) -> None:
    await connection.execute(
        """
        INSERT INTO agentos_distributed_sessions
            (tenant_id, session_id, status, next_turn_number)
        VALUES (%s, %s, 'new', 1)
        ON CONFLICT (tenant_id, session_id) DO NOTHING
        """,
        (tenant_id, session_id),
    )
    await connection.execute(
        """
        SELECT session_id FROM agentos_distributed_sessions
        WHERE tenant_id = %s AND session_id = %s FOR UPDATE
        """,
        (tenant_id, session_id),
    )


async def active_run(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
) -> Row | None:
    return await fetchone(
        connection,
        """
        SELECT run_id FROM agentos_distributed_runs
        WHERE tenant_id = %s AND session_id = %s
          AND status IN ('created', 'queued', 'running', 'waiting')
        FOR UPDATE
        """,
        (tenant_id, session_id),
    )


async def require_no_active_run(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
) -> None:
    if await active_run(connection, tenant_id, session_id) is not None:
        raise ActiveRunConflictError()


async def allocate_turn_number(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
) -> int:
    row = await fetchone(
        connection,
        """
        UPDATE agentos_distributed_sessions
        SET next_turn_number = next_turn_number + 1
        WHERE tenant_id = %s AND session_id = %s
        RETURNING next_turn_number - 1 AS turn_number
        """,
        (tenant_id, session_id),
    )
    if row is None or type(row["turn_number"]) is not int:
        raise CheckpointConflictError()
    return cast(int, row["turn_number"])


async def require_artifacts(
    connection: AsyncConnection,
    scope: RequestScope,
    session_id: str,
    handles: tuple[str, ...],
) -> None:
    if not handles:
        return
    rows = await fetchall(
        connection,
        """
        SELECT artifact_id FROM agentos_distributed_artifacts
        WHERE tenant_id = %s AND session_id = %s AND lifecycle = 'active'
          AND artifact_id = ANY(%s)
        """,
        (scope.tenant_id, session_id, list(handles)),
    )
    if {row["artifact_id"] for row in rows} != set(handles):
        raise ArtifactNotFoundError()


async def advisory_lock(connection: AsyncConnection, *parts: str) -> None:
    await connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("\x1f".join(parts),),
    )


async def update_run(
    connection: AsyncConnection,
    tenant_id: str,
    state: RunState,
) -> None:
    reason = state.wait_reason
    await connection.execute(
        """
        UPDATE agentos_distributed_runs
        SET status = %s, wait_kind = %s, wait_handle = %s, wait_detail = %s,
            wait_not_before = %s, aggregate_version = %s,
            updated_at = clock_timestamp()
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        """,
        (
            state.status.value,
            None if reason is None else reason.kind,
            None if reason is None else reason.handle,
            None if reason is None else reason.detail,
            None if reason is None else reason.not_before,
            state.aggregate_version,
            tenant_id,
            state.session_id,
            state.run_id,
        ),
    )


def duplicate_submission(
    row: Mapping[str, object],
    submission: RunSubmission,
    digest: str,
) -> RunSubmissionReceipt:
    if row["session_id"] != submission.session_id or row["input_digest"] != digest:
        raise RunSubmissionConflictError()
    return RunSubmissionReceipt(
        session_id=cast(str, row["session_id"]),
        run_id=cast(str, row["run_id"]),
        submission_id=submission.submission_id,
        aggregate_version=cast(int, row["aggregate_version"]),
        duplicate=True,
    )


def run_read_model(row: Mapping[str, object]) -> RunReadModel:
    state = run_state_from_row(row)
    result_content = row["result_content"]
    result = None if result_content is None else AgentResult(cast(str, result_content))
    return RunReadModel(
        tenant_id=cast(str, row["tenant_id"]),
        session_id=state.session_id,
        run_id=state.run_id,
        status=state.status,
        wait_reason=state.wait_reason,
        aggregate_version=state.aggregate_version,
        result=result,
    )


__all__ = [
    "active_run",
    "advisory_lock",
    "allocate_turn_number",
    "duplicate_submission",
    "ensure_session",
    "require_artifacts",
    "require_no_active_run",
    "run_read_model",
    "update_run",
]
