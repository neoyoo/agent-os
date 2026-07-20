from __future__ import annotations

import aiosqlite

from agentos.durable.side_effect_serialization import (
    side_effect_record_from_json,
    side_effect_record_to_json,
)
from agentos.runtime.errors import CheckpointCorruptedError
from agentos.runtime.side_effect_types import SideEffectAttemptId, SideEffectRecord


async def load_side_effect(
    connection: aiosqlite.Connection,
    *,
    session_id: str,
    operation_id: str,
    attempt: int | None,
) -> SideEffectRecord | None:
    suffix = "ORDER BY attempt DESC LIMIT 1" if attempt is None else "AND attempt = ?"
    parameters: tuple[object, ...] = (session_id, operation_id)
    if attempt is not None:
        parameters += (attempt,)
    async with connection.execute(
        "SELECT session_id, operation_id, attempt, run_id, status, payload_json "
        "FROM durable_side_effects WHERE session_id = ? AND operation_id = ? "
        + suffix,
        parameters,
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    try:
        record = side_effect_record_from_json(row["payload_json"])
        if (
            record.invocation_ref is None
            or side_effect_record_to_json(record) != row["payload_json"]
            or record.attempt_id.session_id != row["session_id"]
            or record.attempt_id.operation_id != row["operation_id"]
            or record.attempt_id.attempt != row["attempt"]
            or record.run_id != row["run_id"]
            or record.status.value != row["status"]
        ):
            raise ValueError
        return record
    except (KeyError, TypeError, ValueError):
        raise CheckpointCorruptedError(
            "durable side effect record is corrupted",
        ) from None


async def insert_side_effect(
    connection: aiosqlite.Connection,
    record: SideEffectRecord,
) -> None:
    await connection.execute(
        "INSERT INTO durable_side_effects "
        "(session_id, operation_id, attempt, run_id, status, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        _record_parameters(record),
    )


async def update_side_effect(
    connection: aiosqlite.Connection,
    record: SideEffectRecord,
) -> None:
    cursor = await connection.execute(
        "UPDATE durable_side_effects SET run_id = ?, status = ?, payload_json = ? "
        "WHERE session_id = ? AND operation_id = ? AND attempt = ?",
        (
            record.run_id,
            record.status.value,
            side_effect_record_to_json(record),
            record.attempt_id.session_id,
            record.attempt_id.operation_id,
            record.attempt_id.attempt,
        ),
    )
    if cursor.rowcount != 1:
        raise CheckpointCorruptedError("durable side effect record is missing")


def _record_parameters(record: SideEffectRecord) -> tuple[object, ...]:
    attempt_id: SideEffectAttemptId = record.attempt_id
    return (
        attempt_id.session_id,
        attempt_id.operation_id,
        attempt_id.attempt,
        record.run_id,
        record.status.value,
        side_effect_record_to_json(record),
    )


__all__ = ["insert_side_effect", "load_side_effect", "update_side_effect"]
