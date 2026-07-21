from __future__ import annotations

from agentos.distributed.a2a_models import A2APushConfigRecord, A2ATaskBinding
from agentos.distributed.errors import (
    A2APushConfigNotFoundError,
    A2APushConflictError,
    A2ATaskConflictError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._a2a_push_support import (
    create_push_digest,
    delete_push_digest,
    find_push_binding,
    get_push_operation,
    has_push_delete_tombstone,
    insert_push_operation,
    lock_push_binding,
    push_record_from_operation,
    push_record_from_row,
    require_push_binding,
    secret_digest,
    secret_token,
    validate_push_input,
    validate_push_operation,
)
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchall,
    fetchone,
)
from agentos.distributed.postgres._guards import lock_run_with_session
from agentos.distributed.postgres._state_records import advisory_lock
from agentos.distributed.postgres.a2a_delivery import (
    insert_reconciliation_push_delivery,
)


async def create_push_config(
    database: PostgresPool,
    *,
    scope: RequestScope,
    binding: A2ATaskBinding,
    record: A2APushConfigRecord,
    operation_id: str,
) -> A2APushConfigRecord:
    """按统一聚合锁序创建配置并冻结当前 Run 状态。"""

    validate_push_input(scope, binding, record)
    digest = create_push_digest(record)
    async with database.transaction() as connection:
        await advisory_lock(
            connection,
            "a2a_push_operation",
            scope.tenant_id,
            operation_id,
        )
        duplicate = await get_push_operation(connection, scope, operation_id)
        if duplicate is not None:
            validate_push_operation(
                duplicate,
                kind="create",
                task_id=binding.task_id,
                config_id=record.config_id,
                input_digest=digest,
            )
            return push_record_from_operation(duplicate)
        run = await lock_run_with_session(
            connection,
            tenant_id=scope.tenant_id,
            session_id=binding.session_id,
            run_id=binding.run_id,
        )
        await require_push_binding(connection, binding)
        inserted = await fetchone(
            connection,
            """
            INSERT INTO agentos_distributed_a2a_push_configs
                (tenant_id, task_id, config_id, url,
                 authentication_scheme, secret_token, secret_digest)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, task_id, config_id) DO NOTHING
            RETURNING tenant_id, task_id, config_id, url,
                      authentication_scheme, secret_token, secret_digest
            """,
            (
                record.tenant_id,
                record.task_id,
                record.config_id,
                record.url,
                record.authentication_scheme,
                secret_token(record),
                secret_digest(record),
            ),
        )
        if inserted is None:
            raise A2APushConflictError()
        persisted = push_record_from_row(inserted)
        await insert_push_operation(
            connection,
            scope=scope,
            operation_id=operation_id,
            kind="create",
            task_id=record.task_id,
            config_id=record.config_id,
            input_digest=digest,
            record=persisted,
        )
        status, wait_kind, status_sequence = _run_snapshot(run)
        await insert_reconciliation_push_delivery(
            connection,
            scope=scope,
            binding=binding,
            record=persisted,
            operation_id=operation_id,
            status=status,
            wait_kind=wait_kind,
            status_sequence=status_sequence,
        )
        return persisted


async def delete_push_config(
    database: PostgresPool,
    *,
    scope: RequestScope,
    task_id: str,
    config_id: str,
    operation_id: str,
) -> None:
    """按统一聚合锁序停止发送、删除配置并保存 tombstone。"""

    digest = delete_push_digest(scope, task_id, config_id)
    async with database.transaction() as connection:
        await advisory_lock(
            connection,
            "a2a_push_operation",
            scope.tenant_id,
            operation_id,
        )
        duplicate = await get_push_operation(connection, scope, operation_id)
        if duplicate is not None:
            validate_push_operation(
                duplicate,
                kind="delete",
                task_id=task_id,
                config_id=config_id,
                input_digest=digest,
            )
            return
        discovered = await find_push_binding(connection, scope.tenant_id, task_id)
        if discovered is None:
            raise A2APushConfigNotFoundError()
        await lock_run_with_session(
            connection,
            tenant_id=scope.tenant_id,
            session_id=discovered.session_id,
            run_id=discovered.run_id,
        )
        binding = await lock_push_binding(connection, scope.tenant_id, task_id)
        if binding is None:
            raise A2APushConfigNotFoundError()
        if binding != discovered:
            raise A2ATaskConflictError()
        current = await fetchone(
            connection,
            """
            SELECT tenant_id, task_id, config_id, url,
                   authentication_scheme, secret_token, secret_digest
            FROM agentos_distributed_a2a_push_configs
            WHERE tenant_id = %s AND task_id = %s AND config_id = %s
            FOR UPDATE
            """,
            (scope.tenant_id, task_id, config_id),
        )
        if current is None:
            if not await has_push_delete_tombstone(
                connection,
                scope,
                task_id,
                config_id,
            ):
                raise A2APushConfigNotFoundError()
            await _record_delete_operation(
                connection,
                scope=scope,
                operation_id=operation_id,
                task_id=task_id,
                config_id=config_id,
                input_digest=digest,
            )
            return
        await fetchall(
            connection,
            """
            SELECT delivery_id
            FROM agentos_distributed_a2a_push_deliveries
            WHERE tenant_id = %s AND task_id = %s AND config_id = %s
              AND delivered_at IS NULL AND abandoned_at IS NULL
              AND suppressed_at IS NULL
            ORDER BY status_sequence, created_at, delivery_id
            FOR UPDATE
            """,
            (scope.tenant_id, task_id, config_id),
        )
        await connection.execute(
            """
            UPDATE agentos_distributed_a2a_push_deliveries
            SET suppressed_at = clock_timestamp(), next_attempt_at = NULL,
                attempt_id = NULL, attempt_owner_id = NULL,
                attempt_expires_at = NULL
            WHERE tenant_id = %s AND task_id = %s AND config_id = %s
              AND delivered_at IS NULL AND abandoned_at IS NULL
              AND suppressed_at IS NULL
            """,
            (scope.tenant_id, task_id, config_id),
        )
        await connection.execute(
            """
            DELETE FROM agentos_distributed_a2a_push_configs
            WHERE tenant_id = %s AND task_id = %s AND config_id = %s
            """,
            (scope.tenant_id, task_id, config_id),
        )
        await _record_delete_operation(
            connection,
            scope=scope,
            operation_id=operation_id,
            task_id=task_id,
            config_id=config_id,
            input_digest=digest,
        )


async def _record_delete_operation(
    connection: AsyncConnection,
    *,
    scope: RequestScope,
    operation_id: str,
    task_id: str,
    config_id: str,
    input_digest: str,
) -> None:
    await insert_push_operation(
        connection,
        scope=scope,
        operation_id=operation_id,
        kind="delete",
        task_id=task_id,
        config_id=config_id,
        input_digest=input_digest,
        record=None,
    )


def _run_snapshot(row: Row) -> tuple[str, str | None, int]:
    status = row["status"]
    wait_kind = row["wait_kind"]
    status_sequence = row["aggregate_version"]
    if type(status) is not str:
        raise A2APushConflictError()
    if wait_kind is not None and type(wait_kind) is not str:
        raise A2APushConflictError()
    if type(status_sequence) is not int or status_sequence <= 0:
        raise A2APushConflictError()
    return status, wait_kind, status_sequence


__all__ = ["create_push_config", "delete_push_config"]
