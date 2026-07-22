from __future__ import annotations

from hashlib import sha256
from typing import cast

from agentos._json_values import thaw_json_value
from agentos.distributed.errors import (
    ActiveRunConflictError,
    RunSubmissionConflictError,
)
from agentos.distributed.internal_errors import (
    InternalSubmissionBindingRevokedError,
    StaleInternalSubmissionAuthorityError,
)
from agentos.distributed.internal_models import (
    InternalRunSubmission,
    InternalSubmissionAuthority,
)
from agentos.distributed.models import RequestScope, RunSubmissionReceipt
from agentos.distributed.postgres._database import PostgresPool, Row, fetchone
from agentos.distributed.postgres._identities import stable_id
from agentos.distributed.postgres._outbox_records import (
    EXECUTION_TOPIC,
    STATUS_TOPIC,
    insert_outbox,
)
from agentos.distributed.postgres._state_records import (
    advisory_lock,
    allocate_turn_number,
)
from agentos.distributed.postgres._team_access import lock_internal_delivery
from agentos.durable.serialization import dump_json
from agentos.multi.team_identity import team_submission_id
from agentos.runtime.internal_start import canonical_internal_start_payload


async def submit_internal(
    database: PostgresPool,
    *,
    scope: RequestScope,
    submission: InternalRunSubmission,
    authority: InternalSubmissionAuthority,
) -> RunSubmissionReceipt:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(submission) is not InternalRunSubmission:
        raise TypeError("submission must be InternalRunSubmission")
    if type(authority) is not InternalSubmissionAuthority:
        raise TypeError("authority must be InternalSubmissionAuthority")
    expected_id = team_submission_id(
        scope=scope,
        delivery_id=authority.delivery_id,
        target_session_id=submission.session_id,
    )
    if submission.submission_id != expected_id:
        raise RunSubmissionConflictError()
    payload_json = canonical_internal_start_payload(submission.source_payload)
    digest = _submission_digest(scope, submission)
    async with database.transaction() as connection:
        await advisory_lock(
            connection,
            "internal-submission",
            scope.tenant_id,
            submission.submission_id,
        )
        payload = thaw_json_value(submission.source_payload)
        await lock_internal_delivery(
            connection,
            scope=scope,
            session_id=submission.session_id,
            authority=authority,
            source_payload=payload,
        )
        duplicate = await fetchone(
            connection,
            """
            SELECT session_id, run_id, input_digest, aggregate_version,
                   source_payload_json, team_delivery_id
            FROM agentos_distributed_submissions
            WHERE tenant_id = %s AND submission_id = %s
            FOR UPDATE
            """,
            (scope.tenant_id, submission.submission_id),
        )
        if duplicate is not None:
            return _duplicate_receipt(duplicate, submission, authority, digest)
        binding = await fetchone(
            connection,
            """
            SELECT * FROM agentos_team_members
            WHERE tenant_id = %s AND team_id = %s
              AND recipient_agent_id = %s AND target_session_id = %s
            ORDER BY target_session_id FOR UPDATE
            """,
            (
                scope.tenant_id,
                payload["team_id"],
                payload["recipient_agent_id"],
                submission.session_id,
            ),
        )
        if binding is None or binding["status"] != "active":
            raise InternalSubmissionBindingRevokedError()
        session = await fetchone(
            connection,
            """
            SELECT session_id FROM agentos_distributed_sessions
            WHERE tenant_id = %s AND session_id = %s FOR UPDATE
            """,
            (scope.tenant_id, submission.session_id),
        )
        if session is None:
            raise StaleInternalSubmissionAuthorityError()
        active = await fetchone(
            connection,
            """
            SELECT run_id FROM agentos_distributed_runs
            WHERE tenant_id = %s AND session_id = %s
              AND status IN ('created', 'queued', 'running', 'waiting')
            FOR UPDATE
            """,
            (scope.tenant_id, submission.session_id),
        )
        if active is not None:
            raise ActiveRunConflictError()
        turn_number = await allocate_turn_number(
            connection,
            scope.tenant_id,
            submission.session_id,
        )
        run_id = stable_id("run", scope.tenant_id, submission.submission_id)
        turn_id = f"turn_{turn_number}"
        await connection.execute(
            """
            INSERT INTO agentos_distributed_runs
                (tenant_id, session_id, run_id, status, aggregate_version)
            VALUES (%s, %s, %s, 'queued', 1)
            """,
            (scope.tenant_id, submission.session_id, run_id),
        )
        await connection.execute(
            """
            INSERT INTO agentos_distributed_submissions (
                tenant_id, submission_id, session_id, run_id, input_digest,
                turn_id, aggregate_version, source_kind, source_payload_json,
                team_delivery_id
            ) VALUES (%s, %s, %s, %s, %s, %s, 1, 'team_message', %s, %s)
            """,
            (
                scope.tenant_id,
                submission.submission_id,
                submission.session_id,
                run_id,
                digest,
                turn_id,
                payload_json,
                authority.delivery_id,
            ),
        )
        await connection.execute(
            """
            INSERT INTO agentos_distributed_accepted_inputs (
                tenant_id, principal_id, session_id, run_id, turn_id,
                source_kind, source_id, payload_json, team_delivery_id, status
            ) VALUES (
                %s, %s, %s, %s, %s, 'team_message', %s, %s, %s, 'accepted'
            )
            """,
            (
                scope.tenant_id,
                scope.principal_id,
                submission.session_id,
                run_id,
                turn_id,
                submission.submission_id,
                payload_json,
                authority.delivery_id,
            ),
        )
        await insert_outbox(
            connection,
            scope=scope,
            session_id=submission.session_id,
            run_id=run_id,
            source_kind="team_message",
            source_id=submission.submission_id,
            topic=EXECUTION_TOPIC,
            payload={},
        )
        await insert_outbox(
            connection,
            scope=scope,
            session_id=submission.session_id,
            run_id=run_id,
            source_kind="queued",
            source_id=f"{run_id}:1",
            topic=STATUS_TOPIC,
            payload={"status": "queued", "status_sequence": 1},
        )
    return RunSubmissionReceipt(
        submission.session_id,
        run_id,
        submission.submission_id,
        1,
        False,
    )


def _duplicate_receipt(
    row: Row,
    submission: InternalRunSubmission,
    authority: InternalSubmissionAuthority,
    digest: str,
) -> RunSubmissionReceipt:
    if (
        row["session_id"] != submission.session_id
        or row["input_digest"] != digest
        or row["team_delivery_id"] != authority.delivery_id
        or row["source_payload_json"]
        != canonical_internal_start_payload(submission.source_payload)
    ):
        raise RunSubmissionConflictError()
    return RunSubmissionReceipt(
        session_id=submission.session_id,
        run_id=cast(str, row["run_id"]),
        submission_id=submission.submission_id,
        aggregate_version=cast(int, row["aggregate_version"]),
        duplicate=True,
    )


def _submission_digest(
    scope: RequestScope,
    submission: InternalRunSubmission,
) -> str:
    encoded = dump_json(
        {
            "version": 1,
            "tenant_id": scope.tenant_id,
            "session_id": submission.session_id,
            "source_kind": submission.source_kind,
            "source_payload": thaw_json_value(submission.source_payload),
        },
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["submit_internal"]
