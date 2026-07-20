from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from agentos._json_values import thaw_json_value
from agentos.distributed.errors import (
    CheckpointConflictError,
    CommandConflictError,
    CommandStateError,
    ClaimConflictError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._command_cancellation import commit_cancel
from agentos.distributed.postgres._command_records import update_run
from agentos.distributed.postgres._database import AsyncConnection, PostgresPool, fetchone
from agentos.distributed.postgres._artifact_references import (
    require_active_artifact_result,
)
from agentos.distributed.postgres._guards import lock_run_with_session
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC, insert_outbox
from agentos.distributed.postgres._records import run_state_from_row
from agentos.distributed.postgres._reconciliation_sources import (
    validate_reconciliation_command_source,
)
from agentos.distributed.postgres._side_effect_codec import side_effect_record_from_json
from agentos.durable.serialization import dump_json
from agentos.runtime.durable_commands import DurableCommandReceipt, DurableRunCommand
from agentos.runtime.run_state import RunState, RunStatus
from agentos.runtime.side_effect_resolution import side_effect_resolution_from_payload
from agentos.runtime.side_effect_types import (
    SideEffectStatus,
    SideEffectTransitionError,
)


async def submit_command(
    database: PostgresPool,
    *,
    scope: RequestScope,
    session_id: str,
    command: DurableRunCommand,
) -> DurableCommandReceipt:
    payload_json = dump_json(thaw_json_value(command.payload))
    async with database.transaction() as connection:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"command\x1f{scope.tenant_id}\x1f{command.command_id}",),
        )
        duplicate = await fetchone(
            connection,
            """
            SELECT * FROM agentos_distributed_commands
            WHERE tenant_id = %s AND command_id = %s
            """,
            (scope.tenant_id, command.command_id),
        )
        if duplicate is not None:
            return _duplicate_receipt(duplicate, session_id, command, payload_json)
        row = await lock_run_with_session(
            connection,
            tenant_id=scope.tenant_id,
            session_id=session_id,
            run_id=command.run_id,
        )
        current = run_state_from_row(row)
        if command.kind == "cancel":
            return await commit_cancel(
                connection,
                scope=scope,
                command=command,
                current=current,
                payload_json=payload_json,
            )
        return await _commit_continuation(
            connection,
            scope=scope,
            command=command,
            current=current,
            payload_json=payload_json,
            database_now=cast(datetime, row["database_now"]),
            current_fence=cast(int, row["fencing_token"]),
            active_claim_id=cast(str | None, row["active_claim_id"]),
        )


async def _commit_continuation(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    command: DurableRunCommand,
    current: RunState,
    payload_json: str,
    database_now: datetime,
    current_fence: int,
    active_claim_id: str | None,
) -> DurableCommandReceipt:
    if active_claim_id is not None:
        raise CommandStateError()
    _validate_continuation(command, current, database_now)
    if command.kind == "resolve_side_effect":
        await _validate_resolution(connection, scope, current, command)
    updated = current.transition(RunStatus.QUEUED)
    turn_id = await _allocate_turn(connection, scope.tenant_id, current.session_id)
    await connection.execute(
        """
        INSERT INTO agentos_distributed_commands
            (tenant_id, command_id, session_id, run_id, kind, payload_json,
             turn_id, aggregate_version, fencing_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            scope.tenant_id,
            command.command_id,
            current.session_id,
            current.run_id,
            command.kind,
            payload_json,
            turn_id,
            updated.aggregate_version,
            current_fence,
        ),
    )
    await connection.execute(
        """
        INSERT INTO agentos_distributed_accepted_inputs
            (tenant_id, principal_id, session_id, run_id, turn_id, source_kind, source_id,
             continuation_kind, payload_json, status)
        VALUES (%s, %s, %s, %s, %s, 'command', %s, %s, %s, 'accepted')
        """,
        (
            scope.tenant_id,
            scope.principal_id,
            current.session_id,
            current.run_id,
            turn_id,
            command.command_id,
            command.kind,
            payload_json,
        ),
    )
    await update_run(connection, scope.tenant_id, updated)
    await insert_outbox(
        connection,
        scope=scope,
        session_id=current.session_id,
        run_id=current.run_id,
        source_kind="command",
        source_id=command.command_id,
        topic=EXECUTION_TOPIC,
        payload={"command_kind": command.kind},
    )
    return DurableCommandReceipt(
        current.run_id,
        command.command_id,
        command.kind,
        updated.aggregate_version,
        False,
    )


def _validate_continuation(
    command: DurableRunCommand,
    state: RunState,
    database_now: datetime,
) -> None:
    reason = state.wait_reason
    if state.status is not RunStatus.WAITING or reason is None:
        raise CommandStateError()
    allowed = {
        "hitl_answer": {"human_input"},
        "resume": {"human_input", "remote_result", "resource_availability"},
        "wakeup": {"timer", "remote_result", "resource_availability"},
        "retry": {"retry_backoff"},
        "resolve_side_effect": {"side_effect_reconciliation"},
    }
    if reason.kind not in allowed[command.kind]:
        raise CommandStateError()
    if reason.not_before is not None and database_now < reason.not_before:
        raise CommandStateError()


async def _validate_resolution(
    connection: AsyncConnection,
    scope: RequestScope,
    state: RunState,
    command: DurableRunCommand,
) -> None:
    resolution = side_effect_resolution_from_payload(command.payload)
    if state.wait_reason is None or state.wait_reason.handle != resolution.operation_id:
        raise CommandStateError()
    row = await fetchone(
        connection,
        """
        SELECT * FROM agentos_distributed_side_effects
        WHERE tenant_id = %s AND session_id = %s AND run_id = %s
          AND operation_id = %s
        ORDER BY attempt DESC LIMIT 1 FOR UPDATE
        """,
        (scope.tenant_id, state.session_id, state.run_id, resolution.operation_id),
    )
    if row is None or type(row["payload_json"]) is not str:
        raise CommandStateError()
    try:
        record = side_effect_record_from_json(cast(str, row["payload_json"]))
    except SideEffectTransitionError:
        raise CommandStateError() from None
    if record.status is not SideEffectStatus.AMBIGUOUS:
        raise CommandStateError()
    try:
        await validate_reconciliation_command_source(
            connection,
            scope=scope,
            run=state,
            resolution=resolution,
            record=record,
        )
    except ClaimConflictError:
        raise CommandStateError() from None
    await require_active_artifact_result(
        connection,
        tenant_id=scope.tenant_id,
        session_id=state.session_id,
        reference=resolution.result_ref,
    )


async def _allocate_turn(
    connection: AsyncConnection,
    tenant_id: str,
    session_id: str,
) -> str:
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
    return f"turn_{row['turn_number']}"


def _duplicate_receipt(
    row: Mapping[str, object],
    session_id: str,
    command: DurableRunCommand,
    payload_json: str,
) -> DurableCommandReceipt:
    if (
        row["session_id"] != session_id
        or row["run_id"] != command.run_id
        or row["kind"] != command.kind
        or row["payload_json"] != payload_json
    ):
        raise CommandConflictError()
    return DurableCommandReceipt(
        command.run_id,
        command.command_id,
        command.kind,
        cast(int, row["aggregate_version"]),
        True,
    )


__all__ = ["submit_command"]
