from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from agentos.distributed.errors import ClaimConflictError, ClaimExpiredError
from agentos.distributed.models import OutboxClaim, OutboxRecord, RequestScope
from agentos.distributed.postgres._database import PostgresPool, Row, fetchall, fetchone


class PostgresOutboxStore:
    """Multi-relay PostgreSQL Outbox claim adapter."""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def claim_batch(
        self,
        *,
        owner_id: str,
        limit: int,
        ttl: timedelta,
    ) -> tuple[OutboxClaim, ...]:
        if type(owner_id) is not str or not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("outbox claim limit is invalid")
        seconds = _ttl_seconds(ttl)
        claims: list[OutboxClaim] = []
        async with self._database.transaction() as connection:
            rows = await fetchall(
                connection,
                """
                SELECT outbox.* FROM agentos_distributed_outbox AS outbox
                WHERE (
                    outbox.published_at IS NULL
                    OR (
                        outbox.topic = 'agentos.run.execution'
                        AND outbox.published_at <=
                            clock_timestamp() - (%s * interval '1 second')
                        AND EXISTS (
                            SELECT 1
                            FROM agentos_distributed_runs AS run
                            JOIN agentos_distributed_sessions AS session
                              ON session.tenant_id = run.tenant_id
                             AND session.session_id = run.session_id
                            JOIN agentos_distributed_accepted_inputs AS input
                              ON input.tenant_id = run.tenant_id
                             AND input.session_id = run.session_id
                             AND input.run_id = run.run_id
                             AND input.status = 'accepted'
                            WHERE run.tenant_id = outbox.tenant_id
                              AND run.session_id = outbox.session_id
                              AND run.run_id = outbox.run_id
                              AND run.status IN ('queued', 'running')
                              AND session.active_claim_id IS NULL
                              AND (
                                  (
                                      outbox.payload ->> 'kind' = input.source_kind
                                      AND outbox.payload ->> 'source_id' = input.source_id
                                  )
                                  OR (
                                      outbox.payload ->> 'kind' = 'recover'
                                      AND outbox.payload ->> 'turn_id' = input.turn_id
                                      AND outbox.payload ->> 'fencing_token' =
                                          session.fencing_token::text
                                  )
                              )
                        )
                    )
                )
                  AND (
                      outbox.claim_id IS NULL
                      OR outbox.claim_expires_at <= clock_timestamp()
                  )
                ORDER BY created_at, outbox_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (seconds, limit),
            )
            for row in rows:
                claim_id = f"outbox_claim_{uuid4().hex}"
                updated = await fetchone(
                    connection,
                    """
                    UPDATE agentos_distributed_outbox
                    SET claim_owner_id = %s, claim_id = %s,
                        claim_expires_at =
                            clock_timestamp() + (%s * interval '1 second'),
                        publish_attempts = publish_attempts + 1,
                        last_publish_attempt_at = clock_timestamp(),
                        published_at = NULL, queue_entry_id = NULL
                    WHERE outbox_id = %s
                    RETURNING *
                    """,
                    (owner_id, claim_id, seconds, row["outbox_id"]),
                )
                if updated is None:
                    raise ClaimConflictError()
                claims.append(
                    OutboxClaim(
                        _record_from_row(updated),
                        owner_id,
                        claim_id,
                        cast(datetime, updated["claim_expires_at"]),
                    ),
                )
        return tuple(claims)

    async def mark_published(
        self,
        *,
        claim: OutboxClaim,
        queue_entry_id: str,
    ) -> None:
        if type(claim) is not OutboxClaim:
            raise TypeError("claim must be OutboxClaim")
        if type(queue_entry_id) is not str or not queue_entry_id.strip():
            raise ValueError("queue_entry_id must not be empty")
        async with self._database.transaction() as connection:
            updated = await fetchone(
                connection,
                """
                UPDATE agentos_distributed_outbox
                SET published_at = clock_timestamp(), queue_entry_id = %s,
                    claim_owner_id = NULL, claim_id = NULL, claim_expires_at = NULL
                WHERE outbox_id = %s AND published_at IS NULL
                  AND claim_owner_id = %s AND claim_id = %s
                  AND claim_expires_at > clock_timestamp()
                RETURNING outbox_id
                """,
                (
                    queue_entry_id,
                    claim.record.outbox_id,
                    claim.owner_id,
                    claim.claim_id,
                ),
            )
            if updated is None:
                await _raise_claim_failure(connection, claim)

    async def release_claim(self, *, claim: OutboxClaim) -> None:
        if type(claim) is not OutboxClaim:
            raise TypeError("claim must be OutboxClaim")
        async with self._database.transaction() as connection:
            updated = await fetchone(
                connection,
                """
                UPDATE agentos_distributed_outbox
                SET claim_owner_id = NULL, claim_id = NULL, claim_expires_at = NULL
                WHERE outbox_id = %s AND published_at IS NULL
                  AND claim_owner_id = %s AND claim_id = %s
                RETURNING outbox_id
                """,
                (claim.record.outbox_id, claim.owner_id, claim.claim_id),
            )
            if updated is None:
                raise ClaimConflictError()


def _record_from_row(row: Row) -> OutboxRecord:
    payload = row["payload"]
    if not isinstance(payload, Mapping):
        raise ClaimConflictError()
    return OutboxRecord(
        scope=RequestScope(
            cast(str, row["tenant_id"]),
            cast(str, row["principal_id"]),
        ),
        outbox_id=cast(str, row["outbox_id"]),
        topic=cast(str, row["topic"]),
        payload=payload,
        created_at=cast(datetime, row["created_at"]),
        publish_attempts=cast(int, row["publish_attempts"]),
        last_publish_attempt_at=cast(
            datetime | None,
            row["last_publish_attempt_at"],
        ),
        published_at=cast(datetime | None, row["published_at"]),
    )


async def _raise_claim_failure(connection, claim: OutboxClaim) -> None:
    row = await fetchone(
        connection,
        """
        SELECT claim_owner_id, claim_id, claim_expires_at,
               clock_timestamp() AS database_now
        FROM agentos_distributed_outbox WHERE outbox_id = %s FOR UPDATE
        """,
        (claim.record.outbox_id,),
    )
    if (
        row is not None
        and row["claim_owner_id"] == claim.owner_id
        and row["claim_id"] == claim.claim_id
        and row["claim_expires_at"] <= row["database_now"]  # type: ignore[operator]
    ):
        raise ClaimExpiredError()
    raise ClaimConflictError()


def _ttl_seconds(ttl: timedelta) -> float:
    if type(ttl) is not timedelta or ttl <= timedelta(0):
        raise ValueError("outbox claim ttl must be positive")
    return ttl.total_seconds()


__all__ = ["PostgresOutboxStore"]
