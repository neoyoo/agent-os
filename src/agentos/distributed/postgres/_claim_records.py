from __future__ import annotations

from typing import cast

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope, RunDeliveryTarget
from agentos.distributed.postgres._database import Row
from agentos.distributed.postgres._records import run_state_from_row
from agentos.durable.command_serialization import (
    command_payload_from_json,
)
from agentos.durable.serialization import load_json_object
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.errors import CheckpointCorruptedError, DurableUnsafeDataError
from agentos.runtime.execution import AcceptedInternalStartInput, AcceptedStartInput
from agentos.runtime.run import UserTurnInput


def target_from_row(row: Row) -> RunDeliveryTarget:
    return RunDeliveryTarget(
        scope=RequestScope(
            cast(str, row["tenant_id"]),
            cast(str, row["principal_id"]),
        ),
        outbox_id=cast(str, row["outbox_id"]),
        session_id=cast(str, row["session_id"]),
        run=run_state_from_row(row),
    )


def accepted_input(row: Row):  # type: ignore[no-untyped-def]
    if row["source_kind"] == "submission":
        handles = row["artifact_handles"]
        if type(handles) is not list or any(type(item) is not str for item in handles):
            raise ClaimConflictError()
        return AcceptedStartInput(
            run_id=cast(str, row["run_id"]),
            submission_id=cast(str, row["source_id"]),
            input=UserTurnInput(cast(str, row["content"]), tuple(handles)),
            turn_id=cast(str, row["turn_id"]),
            user_message_id=cast(str, row["user_message_id"]),
        )
    payload = row["payload_json"]
    if type(payload) is not str:
        raise ClaimConflictError()
    try:
        if row["source_kind"] == "team_message":
            return AcceptedInternalStartInput(
                run_id=cast(str, row["run_id"]),
                submission_id=cast(str, row["source_id"]),
                source_kind="team_message",
                source_payload=load_json_object(payload),
                turn_id=cast(str, row["turn_id"]),
            )
        continuation_kind = row["continuation_kind"]
        if type(continuation_kind) is not str:
            raise ClaimConflictError()
        return AcceptedContinuationInput(
            run_id=cast(str, row["run_id"]),
            command_id=cast(str, row["source_id"]),
            kind=cast(object, continuation_kind),  # type: ignore[arg-type]
            payload=command_payload_from_json(continuation_kind, payload),
            turn_id=cast(str, row["turn_id"]),
            team_delivery_id=cast(str | None, row.get("team_delivery_id")),
        )
    except (CheckpointCorruptedError, DurableUnsafeDataError):
        raise ClaimConflictError() from None


__all__ = ["accepted_input", "target_from_row"]
