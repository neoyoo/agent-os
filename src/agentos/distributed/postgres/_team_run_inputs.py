from __future__ import annotations

from typing import cast

from agentos.distributed.errors import CheckpointConflictError
from agentos.distributed.internal_models import (
    InternalRunInputReceipt,
    InternalSubmissionAuthority,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._database import PostgresPool, Row, fetchone
from agentos.distributed.postgres._team_access import lock_delivery_authority


async def get_applied_input(
    database: PostgresPool,
    *,
    scope: RequestScope,
    authority: InternalSubmissionAuthority,
) -> InternalRunInputReceipt | None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(authority) is not InternalSubmissionAuthority:
        raise TypeError("authority must be InternalSubmissionAuthority")
    async with database.transaction() as connection:
        delivery = await lock_delivery_authority(
            connection,
            scope=scope,
            authority=authority,
        )
        row = await fetchone(
            connection,
            """
            SELECT accepted.session_id, accepted.run_id,
                   accepted.source_kind, accepted.continuation_kind,
                   submission.aggregate_version AS submission_version,
                   command.aggregate_version AS command_version
            FROM agentos_distributed_accepted_inputs AS accepted
            LEFT JOIN agentos_distributed_submissions AS submission
              ON accepted.tenant_id = submission.tenant_id
             AND accepted.source_kind = 'team_message'
             AND accepted.source_id = submission.submission_id
            LEFT JOIN agentos_distributed_commands AS command
              ON accepted.tenant_id = command.tenant_id
             AND accepted.source_kind = 'command'
             AND accepted.source_id = command.command_id
            WHERE accepted.tenant_id = %s
              AND accepted.team_delivery_id = %s
            FOR UPDATE OF accepted
            """,
            (scope.tenant_id, authority.delivery_id),
        )
        if row is None:
            return None
        return _receipt(row, delivery, authority)


def _receipt(
    row: Row,
    delivery: Row,
    authority: InternalSubmissionAuthority,
) -> InternalRunInputReceipt:
    source_kind = row.get("source_kind")
    continuation_kind = row.get("continuation_kind")
    submission_version = row.get("submission_version")
    command_version = row.get("command_version")
    if (
        row.get("session_id") != delivery.get("target_session_id")
        or type(row.get("run_id")) is not str
    ):
        raise CheckpointConflictError()
    if (
        source_kind == "team_message"
        and continuation_kind is None
        and type(submission_version) is int
        and command_version is None
    ):
        input_kind = "internal_start"
        aggregate_version = submission_version
    elif (
        source_kind == "command"
        and continuation_kind == "wakeup"
        and submission_version is None
        and type(command_version) is int
    ):
        input_kind = "wakeup"
        aggregate_version = command_version
    else:
        raise CheckpointConflictError()
    try:
        return InternalRunInputReceipt(
            authority.delivery_id,
            cast(str, row["session_id"]),
            input_kind,
            cast(str, row["run_id"]),
            aggregate_version,
        )
    except (KeyError, TypeError, ValueError):
        raise CheckpointConflictError() from None


__all__ = ["get_applied_input"]
