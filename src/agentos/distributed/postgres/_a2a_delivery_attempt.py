from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from agentos.distributed._model_validation import require_identifier
from agentos.distributed.a2a_models import (
    A2APushAttemptResolution,
    A2APushDeliveryTarget,
    A2APushFailureCategory,
)
from agentos.distributed.errors import (
    A2APushAttemptFencedError,
    A2APushDeliveryDeferredError,
    DeliveryUnavailableError,
)
from agentos.distributed.postgres._a2a_delivery_rows import (
    datetime_value,
    integer,
    is_current_attempt,
    is_terminal_delivery,
    optional_datetime,
    push_delivery_target_from_row,
)
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchone,
)


_MAX_FAILURES = 8


async def open_push_attempt(
    database: PostgresPool,
    *,
    outbox_id: str,
    worker_id: str,
    ttl: timedelta,
) -> PostgresA2APushDeliveryAttempt | None:
    require_identifier(outbox_id, "outbox_id")
    require_identifier(worker_id, "worker_id")
    seconds = _ttl_seconds(ttl)
    async with database.transaction() as connection:
        identity = await fetchone(
            connection,
            """
            SELECT tenant_id, task_id, config_id
            FROM agentos_distributed_a2a_push_deliveries
            WHERE outbox_id = %s
            """,
            (outbox_id,),
        )
        if identity is None:
            return None
        config = await fetchone(
            connection,
            """
            SELECT 1 FROM agentos_distributed_a2a_push_configs
            WHERE tenant_id = %s AND task_id = %s AND config_id = %s
            FOR KEY SHARE
            """,
            (
                identity["tenant_id"],
                identity["task_id"],
                identity["config_id"],
            ),
        )
        row = await _lock_delivery(connection, outbox_id)
        if row is None:
            return None
        if is_terminal_delivery(row):
            return None
        if config is None:
            await _suppress_missing_config(connection, outbox_id)
            return None
        now = datetime_value(row, "database_now")
        next_attempt_at = optional_datetime(row, "next_attempt_at")
        attempt_expires_at = optional_datetime(row, "attempt_expires_at")
        if (
            next_attempt_at is not None
            and next_attempt_at > now
            or attempt_expires_at is not None
            and attempt_expires_at > now
            or await _has_predecessor(connection, row)
        ):
            raise A2APushDeliveryDeferredError()
        attempt_id = f"a2a_attempt_{uuid4().hex}"
        claimed = await fetchone(
            connection,
            """
            UPDATE agentos_distributed_a2a_push_deliveries
            SET attempt_id = %s, attempt_owner_id = %s,
                attempt_expires_at = clock_timestamp() + (%s * interval '1 second')
            WHERE outbox_id = %s
            RETURNING *, clock_timestamp() AS database_now
            """,
            (attempt_id, worker_id, seconds, outbox_id),
        )
        if claimed is None:
            raise DeliveryUnavailableError()
    target = push_delivery_target_from_row(claimed)
    return PostgresA2APushDeliveryAttempt(
        database=database,
        target=target,
        attempt_id=attempt_id,
        expires_at=datetime_value(claimed, "attempt_expires_at"),
    )


@dataclass(frozen=True, slots=True)
class PostgresA2APushDeliveryAttempt:
    database: PostgresPool
    target: A2APushDeliveryTarget
    attempt_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        require_identifier(self.attempt_id, "attempt_id")
        if self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")

    @asynccontextmanager
    async def authorize_send(self) -> AsyncIterator[None]:
        async with self.database.transaction() as connection:
            config = await fetchone(
                connection,
                """
                SELECT 1 FROM agentos_distributed_a2a_push_configs
                WHERE tenant_id = %s AND task_id = %s AND config_id = %s
                FOR KEY SHARE
                """,
                (
                    self.target.scope.tenant_id,
                    self.target.task_id,
                    self.target.config_id,
                ),
            )
            row = await _lock_delivery(connection, self.target.outbox_id)
            if config is None or not is_current_attempt(row, self.attempt_id):
                raise A2APushAttemptFencedError()
            yield

    async def mark_delivered(self) -> None:
        async with self.database.transaction() as connection:
            row = await _lock_delivery(connection, self.target.outbox_id)
            if row is None:
                raise DeliveryUnavailableError()
            if is_terminal_delivery(row):
                return
            if not is_current_attempt(row, self.attempt_id):
                raise A2APushAttemptFencedError()
            await connection.execute(
                """
                UPDATE agentos_distributed_a2a_push_deliveries
                SET delivered_at = clock_timestamp(), next_attempt_at = NULL,
                    attempt_id = NULL, attempt_owner_id = NULL,
                    attempt_expires_at = NULL
                WHERE outbox_id = %s
                """,
                (self.target.outbox_id,),
            )

    async def mark_failed(
        self,
        *,
        category: A2APushFailureCategory,
    ) -> A2APushAttemptResolution:
        if type(category) is not A2APushFailureCategory:
            raise TypeError("category must be A2APushFailureCategory")
        async with self.database.transaction() as connection:
            row = await _lock_delivery(connection, self.target.outbox_id)
            if row is None:
                raise DeliveryUnavailableError()
            if is_terminal_delivery(row):
                return A2APushAttemptResolution.ACK_SAFE
            if not is_current_attempt(row, self.attempt_id):
                raise A2APushAttemptFencedError()
            failure_count = integer(row, "failure_count") + 1
            if failure_count >= _MAX_FAILURES:
                await _mark_abandoned(
                    connection,
                    self.target.outbox_id,
                    failure_count,
                    category,
                )
                return A2APushAttemptResolution.ACK_SAFE
            await _schedule_retry(
                connection,
                self.target.outbox_id,
                failure_count,
                category,
            )
            return A2APushAttemptResolution.RETRY_PENDING


async def _lock_delivery(
    connection: AsyncConnection,
    outbox_id: str,
) -> Row | None:
    return await fetchone(
        connection,
        """
        SELECT *, clock_timestamp() AS database_now
        FROM agentos_distributed_a2a_push_deliveries
        WHERE outbox_id = %s
        FOR UPDATE
        """,
        (outbox_id,),
    )


async def _has_predecessor(connection: AsyncConnection, row: Row) -> bool:
    predecessor = await fetchone(
        connection,
        """
        SELECT 1 FROM agentos_distributed_a2a_push_deliveries
        WHERE tenant_id = %s AND task_id = %s AND config_id = %s
          AND delivered_at IS NULL AND suppressed_at IS NULL
          AND abandoned_at IS NULL
          AND (status_sequence, created_at, delivery_id) < (%s, %s, %s)
        LIMIT 1
        """,
        (
            row["tenant_id"],
            row["task_id"],
            row["config_id"],
            row["status_sequence"],
            row["created_at"],
            row["delivery_id"],
        ),
    )
    return predecessor is not None


async def _suppress_missing_config(
    connection: AsyncConnection,
    outbox_id: str,
) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_a2a_push_deliveries
        SET suppressed_at = clock_timestamp(), next_attempt_at = NULL,
            attempt_id = NULL, attempt_owner_id = NULL,
            attempt_expires_at = NULL
        WHERE outbox_id = %s AND delivered_at IS NULL
          AND suppressed_at IS NULL AND abandoned_at IS NULL
        """,
        (outbox_id,),
    )


async def _schedule_retry(
    connection: AsyncConnection,
    outbox_id: str,
    failure_count: int,
    category: A2APushFailureCategory,
) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_a2a_push_deliveries
        SET failure_count = %s, last_failure_category = %s,
            next_attempt_at = clock_timestamp() + (%s * interval '1 second'),
            attempt_id = NULL, attempt_owner_id = NULL,
            attempt_expires_at = NULL
        WHERE outbox_id = %s
        """,
        (failure_count, category.value, min(2**failure_count, 300), outbox_id),
    )


async def _mark_abandoned(
    connection: AsyncConnection,
    outbox_id: str,
    failure_count: int,
    category: A2APushFailureCategory,
) -> None:
    await connection.execute(
        """
        UPDATE agentos_distributed_a2a_push_deliveries
        SET failure_count = %s, last_failure_category = %s,
            abandoned_at = clock_timestamp(), next_attempt_at = NULL,
            attempt_id = NULL, attempt_owner_id = NULL,
            attempt_expires_at = NULL
        WHERE outbox_id = %s
        """,
        (failure_count, category.value, outbox_id),
    )


def _ttl_seconds(value: object) -> float:
    if type(value) is not timedelta or value <= timedelta(0):
        raise ValueError("attempt ttl must be positive")
    seconds = value.total_seconds()
    if seconds > 300:
        raise ValueError("attempt ttl must not exceed 300 seconds")
    return seconds


__all__ = ["PostgresA2APushDeliveryAttempt", "open_push_attempt"]
