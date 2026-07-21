from __future__ import annotations

from typing import cast

from agentos.distributed.a2a_models import (
    A2APushConfigRecord,
    A2APushConfigRecordPage,
    A2ATaskBinding,
)
from agentos.distributed.errors import (
    A2ATaskConflictError,
    A2ATaskNotFoundError,
)
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._a2a_push_support import (
    decode_push_page_token,
    encode_push_page_token,
    push_record_from_row,
    require_push_scope,
)
from agentos.distributed.postgres._a2a_push_mutations import (
    create_push_config,
    delete_push_config,
)
from agentos.distributed.postgres._database import (
    PostgresPool,
    Row,
    fetchall,
    fetchone,
)


class PostgresA2ATaskStore:
    """PostgreSQL-backed A2A task/run binding truth。"""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def bind(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
    ) -> A2ATaskBinding:
        """幂等创建 binding；同 task 的不同 binding 拒绝。"""

        _validate_scope_binding(scope, binding)
        async with self._database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO agentos_distributed_a2a_tasks
                    (tenant_id, task_id, session_id, run_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, task_id) DO NOTHING
                """,
                (
                    binding.tenant_id,
                    binding.task_id,
                    binding.session_id,
                    binding.run_id,
                ),
            )
            row = await fetchone(
                connection,
                """
                SELECT tenant_id, task_id, session_id, run_id
                FROM agentos_distributed_a2a_tasks
                WHERE tenant_id = %s AND task_id = %s
                FOR UPDATE
                """,
                (scope.tenant_id, binding.task_id),
            )
        if row is None:
            raise RuntimeError("a2a task binding was not persisted")
        current = _binding_from_row(row)
        if current != binding:
            raise A2ATaskConflictError()
        return current

    async def resolve(
        self,
        *,
        scope: RequestScope,
        task_id: str,
    ) -> A2ATaskBinding | None:
        """按 tenant/task 读取 binding。"""

        if type(scope) is not RequestScope:
            raise TypeError("scope must be RequestScope")
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT tenant_id, task_id, session_id, run_id
                FROM agentos_distributed_a2a_tasks
                WHERE tenant_id = %s AND task_id = %s
                """,
                (scope.tenant_id, task_id),
            )
        return None if row is None else _binding_from_row(row)


class PostgresA2APushStore:
    """PostgreSQL push config、idempotency 与 Outbox truth。"""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def create(
        self,
        *,
        scope: RequestScope,
        binding: A2ATaskBinding,
        record: A2APushConfigRecord,
        operation_id: str,
    ) -> A2APushConfigRecord:
        return await create_push_config(
            self._database,
            scope=scope,
            binding=binding,
            record=record,
            operation_id=operation_id,
        )

    async def get(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
    ) -> A2APushConfigRecord | None:
        require_push_scope(scope)
        async with self._database.connection() as connection:
            row = await fetchone(
                connection,
                """
                SELECT tenant_id, task_id, config_id, url,
                       authentication_scheme, secret_token, secret_digest
                FROM agentos_distributed_a2a_push_configs
                WHERE tenant_id = %s AND task_id = %s AND config_id = %s
                """,
                (scope.tenant_id, task_id, config_id),
            )
        return None if row is None else push_record_from_row(row)

    async def list(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        page_size: int,
        page_token: str | None,
    ) -> A2APushConfigRecordPage:
        require_push_scope(scope)
        after_id = (
            None
            if page_token is None
            else decode_push_page_token(page_token, scope, task_id, page_size)
        )
        async with self._database.connection() as connection:
            rows = await fetchall(
                connection,
                """
                WITH task AS (
                    SELECT tenant_id, task_id
                    FROM agentos_distributed_a2a_tasks
                    WHERE tenant_id = %s AND task_id = %s
                ), page AS (
                    SELECT config.tenant_id, config.task_id, config.config_id,
                           config.url, config.authentication_scheme,
                           config.secret_token, config.secret_digest
                    FROM agentos_distributed_a2a_push_configs AS config
                    JOIN task USING (tenant_id, task_id)
                    WHERE (%s IS NULL OR config.config_id > %s)
                    ORDER BY config.config_id
                    LIMIT %s
                )
                SELECT EXISTS(SELECT 1 FROM task) AS task_exists,
                       page.tenant_id, page.task_id, page.config_id, page.url,
                       page.authentication_scheme, page.secret_token,
                       page.secret_digest
                FROM (SELECT 1) AS singleton
                LEFT JOIN page ON TRUE
                ORDER BY page.config_id
                """,
                (scope.tenant_id, task_id, after_id, after_id, page_size + 1),
            )
        if not rows or rows[0]["task_exists"] is not True:
            raise A2ATaskNotFoundError()
        records = tuple(
            push_record_from_row(row)
            for row in rows
            if row["config_id"] is not None
        )
        has_more = len(records) > page_size
        selected = records[:page_size]
        next_page_token = (
            encode_push_page_token(
                scope,
                task_id,
                page_size,
                selected[-1].config_id,
            )
            if has_more
            else ""
        )
        return A2APushConfigRecordPage(selected, next_page_token)

    async def delete(
        self,
        *,
        scope: RequestScope,
        task_id: str,
        config_id: str,
        operation_id: str,
    ) -> None:
        require_push_scope(scope)
        await delete_push_config(
            self._database,
            scope=scope,
            task_id=task_id,
            config_id=config_id,
            operation_id=operation_id,
        )


def _validate_scope_binding(
    scope: object,
    binding: object,
) -> None:
    if type(scope) is not RequestScope:
        raise TypeError("scope must be RequestScope")
    if type(binding) is not A2ATaskBinding:
        raise TypeError("binding must be A2ATaskBinding")
    if binding.tenant_id != scope.tenant_id:
        raise ValueError("binding tenant must match request scope")


def _binding_from_row(row: Row) -> A2ATaskBinding:
    return A2ATaskBinding(
        tenant_id=cast(str, row["tenant_id"]),
        task_id=cast(str, row["task_id"]),
        session_id=cast(str, row["session_id"]),
        run_id=cast(str, row["run_id"]),
    )


__all__ = ["PostgresA2APushStore", "PostgresA2ATaskStore"]
