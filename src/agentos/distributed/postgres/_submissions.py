from __future__ import annotations

from agentos.distributed.models import (
    RequestScope,
    RunSubmission,
    RunSubmissionReceipt,
    canonical_submission_digest,
)
from agentos.distributed.postgres._database import PostgresPool, fetchone
from agentos.distributed.postgres._identities import stable_id
from agentos.distributed.postgres._outbox_records import EXECUTION_TOPIC, insert_outbox
from agentos.distributed.postgres._state_records import (
    advisory_lock,
    allocate_turn_number,
    duplicate_submission,
    ensure_session,
    require_artifacts,
    require_no_active_run,
)
from agentos.durable.serialization import dump_json


async def submit(
    database: PostgresPool,
    *,
    scope: RequestScope,
    submission: RunSubmission,
) -> RunSubmissionReceipt:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(submission) is not RunSubmission:
        raise TypeError("submission must be RunSubmission")
    digest = canonical_submission_digest(scope, submission)
    async with database.transaction() as connection:
        await advisory_lock(
            connection,
            "submission",
            scope.tenant_id,
            submission.submission_id,
        )
        duplicate = await fetchone(
            connection,
            """
            SELECT session_id, run_id, input_digest, aggregate_version
            FROM agentos_distributed_submissions
            WHERE tenant_id = %s AND submission_id = %s
            """,
            (scope.tenant_id, submission.submission_id),
        )
        if duplicate is not None:
            return duplicate_submission(duplicate, submission, digest)
        await ensure_session(connection, scope.tenant_id, submission.session_id)
        await require_no_active_run(
            connection,
            scope.tenant_id,
            submission.session_id,
        )
        await require_artifacts(
            connection,
            scope,
            submission.session_id,
            submission.artifact_handles,
        )
        turn_number = await allocate_turn_number(
            connection,
            scope.tenant_id,
            submission.session_id,
        )
        run_id = stable_id("run", scope.tenant_id, submission.submission_id)
        turn_id = f"turn_{turn_number}"
        message_id = stable_id("message", scope.tenant_id, submission.submission_id)
        await connection.execute(
            """
            INSERT INTO agentos_distributed_runs
                (tenant_id, session_id, run_id, status, aggregate_version)
            VALUES (%s, %s, %s, 'queued', 1)
            """,
            (scope.tenant_id, submission.session_id, run_id),
        )
        handles_json = dump_json(list(submission.artifact_handles))
        await connection.execute(
            """
            INSERT INTO agentos_distributed_submissions
                (tenant_id, submission_id, session_id, run_id, input_digest,
                 content, artifact_handles, turn_id, user_message_id,
                 aggregate_version)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, 1)
            """,
            (
                scope.tenant_id,
                submission.submission_id,
                submission.session_id,
                run_id,
                digest,
                submission.content,
                handles_json,
                turn_id,
                message_id,
            ),
        )
        await connection.execute(
            """
            INSERT INTO agentos_distributed_accepted_inputs
                (tenant_id, principal_id, session_id, run_id, turn_id, source_kind,
                 source_id, content, artifact_handles, user_message_id, status)
            VALUES (%s, %s, %s, %s, %s, 'submission', %s, %s, %s::jsonb,
                    %s, 'accepted')
            """,
            (
                scope.tenant_id,
                scope.principal_id,
                submission.session_id,
                run_id,
                turn_id,
                submission.submission_id,
                submission.content,
                handles_json,
                message_id,
            ),
        )
        await insert_outbox(
            connection,
            scope=scope,
            session_id=submission.session_id,
            run_id=run_id,
            source_kind="submission",
            source_id=submission.submission_id,
            topic=EXECUTION_TOPIC,
            payload={},
        )
    return RunSubmissionReceipt(
        submission.session_id,
        run_id,
        submission.submission_id,
        1,
        False,
    )


__all__ = ["submit"]
