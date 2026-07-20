from __future__ import annotations

from typing import cast
from uuid import uuid4

from agentos.distributed.errors import CheckpointConflictError, CommandStateError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._command_records import update_run
from agentos.distributed.postgres._database import AsyncConnection, fetchone
from agentos.distributed.postgres._identities import stable_id
from agentos.distributed.postgres._outbox_records import STATUS_TOPIC, insert_outbox
from agentos.distributed.postgres._records import session_checkpoint_from_json
from agentos.distributed.postgres._side_effect_composite import apply_cancel_safe_stop
from agentos.runtime.checkpoint import CHECKPOINT_SCHEMA_VERSION
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from agentos.runtime.run_state import RunState, RunStatus


async def commit_cancel(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    command: DurableRunCommand,
    current: RunState,
    payload_json: str,
) -> DurableCommandReceipt:
    if current.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        raise CommandStateError()
    await apply_cancel_safe_stop(
        connection,
        tenant_id=scope.tenant_id,
        session_id=current.session_id,
        run_id=current.run_id,
    )
    updated = current.transition(RunStatus.CANCELLED)
    pending = await fetchone(
        connection,
        """
        SELECT turn_id FROM agentos_distributed_accepted_inputs
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
          AND status IN ('accepted', 'claimed')
        ORDER BY accepted_at DESC LIMIT 1 FOR UPDATE
        """,
        (scope.tenant_id, current.session_id, current.run_id),
    )
    latest = await fetchone(
        connection,
        """
        SELECT turn_id, snapshot_json FROM agentos_distributed_checkpoints
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        ORDER BY aggregate_version DESC LIMIT 1
        """,
        (scope.tenant_id, current.session_id, current.run_id),
    )
    snapshot_json = None if latest is None else latest["snapshot_json"]
    if snapshot_json is not None:
        if type(snapshot_json) is not str:
            raise CheckpointConflictError()
        session_checkpoint_from_json(snapshot_json)
    turn_id = (
        cast(str, pending["turn_id"])
        if pending is not None
        else cast(str, latest["turn_id"])
        if latest is not None
        else stable_id("turn", scope.tenant_id, command.command_id)
    )
    session = await fetchone(
        connection,
        """
        UPDATE agentos_distributed_sessions
        SET fencing_token = fencing_token + 1,
            active_claim_id = NULL, active_claim_owner_id = NULL,
            active_claim_run_id = NULL, active_claim_expires_at = NULL
        WHERE tenant_id = %s AND session_id = %s
        RETURNING fencing_token
        """,
        (scope.tenant_id, current.session_id),
    )
    if session is None:
        raise CheckpointConflictError()
    new_fence = cast(int, session["fencing_token"])
    checkpoint_id = f"checkpoint_{uuid4().hex}"
    await connection.execute(
        """
        INSERT INTO agentos_distributed_checkpoints
            (tenant_id, checkpoint_id, session_id, run_id, turn_id,
             aggregate_version, snapshot_json, schema_version, fencing_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            scope.tenant_id,
            checkpoint_id,
            current.session_id,
            current.run_id,
            turn_id,
            updated.aggregate_version,
            snapshot_json,
            CHECKPOINT_SCHEMA_VERSION,
            new_fence,
        ),
    )
    await connection.execute(
        """
        UPDATE agentos_distributed_accepted_inputs
        SET status = 'committed', claim_id = NULL, fencing_token = NULL,
            checkpoint_id = %s
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
          AND status IN ('accepted', 'claimed')
        """,
        (checkpoint_id, scope.tenant_id, current.session_id, current.run_id),
    )
    await connection.execute(
        """
        DELETE FROM agentos_distributed_execution_cursors
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
        """,
        (scope.tenant_id, current.session_id, current.run_id),
    )
    await update_run(connection, scope.tenant_id, updated)
    await connection.execute(
        """
        INSERT INTO agentos_distributed_commands
            (tenant_id, command_id, session_id, run_id, kind, payload_json,
             turn_id, aggregate_version, fencing_token)
        VALUES (%s, %s, %s, %s, 'cancel', %s, NULL, %s, %s)
        """,
        (
            scope.tenant_id,
            command.command_id,
            current.session_id,
            current.run_id,
            payload_json,
            updated.aggregate_version,
            new_fence,
        ),
    )
    await insert_outbox(
        connection,
        scope=scope,
        session_id=current.session_id,
        run_id=current.run_id,
        source_kind="cancel",
        source_id=command.command_id,
        topic=STATUS_TOPIC,
        payload={"status": "cancelled"},
    )
    return DurableCommandReceipt(
        current.run_id,
        command.command_id,
        command.kind,
        updated.aggregate_version,
        False,
    )


__all__ = ["commit_cancel"]
