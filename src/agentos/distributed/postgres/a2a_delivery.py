from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import json

from agentos.distributed.a2a_models import (
    A2APushConfigRecord,
    A2ATaskBinding,
    A2ATaskState,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._a2a_delivery_attempt import (
    PostgresA2APushDeliveryAttempt,
    open_push_attempt,
)
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchall,
)
from agentos.distributed.postgres._identities import outbox_id, stable_id


PUSH_TOPIC = "agentos.a2a.push"
_PROTOCOL_VERSION = "1.0"


async def fanout_status_push_deliveries(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    session_id: str,
    run_id: str,
    event_id: str,
    payload: Mapping[str, object],
) -> None:
    """在 status transaction 内为当前 active configs 冻结 Push deliveries。"""

    task_state = _task_state(payload)
    status_sequence = _status_sequence(payload)
    rows = await fetchall(
        connection,
        """
        SELECT task.task_id, task.session_id, config.config_id, config.url,
               config.authentication_scheme, config.secret_token,
               config.secret_digest
        FROM agentos_distributed_a2a_tasks AS task
        JOIN agentos_distributed_a2a_push_configs AS config
          ON config.tenant_id = task.tenant_id
         AND config.task_id = task.task_id
        WHERE task.tenant_id = %s
          AND task.session_id = %s
          AND task.run_id = %s
        ORDER BY config.config_id
        FOR KEY SHARE OF task, config
        """,
        (scope.tenant_id, session_id, run_id),
    )
    for row in rows:
        await _insert_delivery(
            connection,
            scope=scope,
            event_id=event_id,
            status_sequence=status_sequence,
            task_state=task_state,
            row=row,
        )


async def insert_reconciliation_push_delivery(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    binding: A2ATaskBinding,
    record: A2APushConfigRecord,
    operation_id: str,
    status: str,
    wait_kind: str | None,
    status_sequence: int,
) -> None:
    """为新配置冻结当前 Run 状态，避免 Create 与状态迁移竞态漏通知。"""

    _status_sequence({"status_sequence": status_sequence})
    event_id = stable_id(
        "a2a_push_reconciliation",
        scope.tenant_id,
        operation_id,
        str(status_sequence),
    )
    await _insert_delivery(
        connection,
        scope=scope,
        event_id=event_id,
        status_sequence=status_sequence,
        task_state=_task_state({"status": status, "wait_kind": wait_kind}),
        row={
            "task_id": binding.task_id,
            "session_id": binding.session_id,
            "config_id": record.config_id,
            "url": record.url,
            "authentication_scheme": record.authentication_scheme,
            "secret_token": (
                None if record.secret_ref is None else record.secret_ref.token
            ),
            "secret_digest": (
                None if record.secret_ref is None else record.secret_ref.digest
            ),
        },
    )


class PostgresA2APushDeliveryStore:
    """PostgreSQL Push delivery attempt、顺序与重试真值。"""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def open_attempt(
        self,
        *,
        outbox_id: str,
        worker_id: str,
        ttl: timedelta,
    ) -> PostgresA2APushDeliveryAttempt | None:
        return await open_push_attempt(
            self._database,
            outbox_id=outbox_id,
            worker_id=worker_id,
            ttl=ttl,
        )


async def _insert_delivery(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    event_id: str,
    status_sequence: int,
    task_state: A2ATaskState,
    row: Row,
) -> None:
    task_id = _text(row, "task_id")
    context_id = _text(row, "session_id")
    config_id = _text(row, "config_id")
    delivery_id = stable_id(
        "a2a_push_delivery",
        scope.tenant_id,
        task_id,
        event_id,
        config_id,
    )
    delivery_outbox_id = outbox_id(
        scope.tenant_id,
        "a2a_push_delivery",
        delivery_id,
    )
    await connection.execute(
        """
        INSERT INTO agentos_distributed_outbox
            (outbox_id, tenant_id, principal_id, session_id, run_id, topic, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (outbox_id) DO NOTHING
        """,
        (
            delivery_outbox_id,
            scope.tenant_id,
            scope.principal_id,
            context_id,
            task_id,
            PUSH_TOPIC,
            _canonical_json({"delivery_id": delivery_id}),
        ),
    )
    await connection.execute(
        """
        INSERT INTO agentos_distributed_a2a_push_deliveries
            (tenant_id, principal_id, delivery_id, outbox_id,
             task_id, context_id, config_id, url,
             authentication_scheme, secret_token, secret_digest, event_id,
             protocol_version, status_sequence, task_state)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, delivery_id) DO NOTHING
        """,
        (
            scope.tenant_id,
            scope.principal_id,
            delivery_id,
            delivery_outbox_id,
            task_id,
            context_id,
            config_id,
            _text(row, "url"),
            _optional_text(row, "authentication_scheme"),
            _optional_text(row, "secret_token"),
            _optional_text(row, "secret_digest"),
            event_id,
            _PROTOCOL_VERSION,
            status_sequence,
            task_state.value,
        ),
    )


def _task_state(payload: Mapping[str, object]) -> A2ATaskState:
    status = payload.get("status")
    if status == "queued":
        return A2ATaskState.SUBMITTED
    if status == "running":
        return A2ATaskState.WORKING
    if status == "completed":
        return A2ATaskState.COMPLETED
    if status == "failed":
        return A2ATaskState.FAILED
    if status == "cancelled":
        return A2ATaskState.CANCELED
    if status == "waiting":
        return (
            A2ATaskState.INPUT_REQUIRED
            if payload.get("wait_kind") == "human_input"
            else A2ATaskState.WORKING
        )
    raise ValueError("status outbox is not push-deliverable")


def _status_sequence(payload: Mapping[str, object]) -> int:
    value = payload.get("status_sequence")
    if type(value) is not int or value <= 0:
        raise ValueError("status outbox requires a positive status_sequence")
    return value


def _text(row: Row, field_name: str) -> str:
    value = row[field_name]
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str")
    return value


def _optional_text(row: Row, field_name: str) -> str | None:
    value = row[field_name]
    if value is not None and type(value) is not str:
        raise TypeError(f"{field_name} must be str or None")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


__all__ = [
    "PUSH_TOPIC",
    "PostgresA2APushDeliveryStore",
    "fanout_status_push_deliveries",
    "insert_reconciliation_push_delivery",
]
