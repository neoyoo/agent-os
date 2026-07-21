from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from agentos.distributed.a2a_models import A2ATaskBinding
from agentos.distributed.errors import A2ATaskConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres.a2a import PostgresA2ATaskStore
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")


class Cursor:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row


class Connection:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, object]] = {}

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> Cursor:
        normalized = " ".join(query.split()).lower()
        if normalized.startswith("insert into agentos_distributed_a2a_tasks"):
            tenant_id, task_id, session_id, run_id = params
            key = (str(tenant_id), str(task_id))
            self.rows.setdefault(
                key,
                {
                    "tenant_id": tenant_id,
                    "task_id": task_id,
                    "session_id": session_id,
                    "run_id": run_id,
                },
            )
            return Cursor()
        if "from agentos_distributed_a2a_tasks" in normalized:
            tenant_id, task_id = params
            return Cursor(self.rows.get((str(tenant_id), str(task_id))))
        raise AssertionError(f"unexpected query: {normalized}")


class Database:
    def __init__(self) -> None:
        self.connection_value = Connection()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Connection]:
        yield self.connection_value

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Connection]:
        yield self.connection_value


@async_test
async def test_postgres_a2a_task_store_is_idempotent_and_tenant_scoped() -> None:
    database = Database()
    store = PostgresA2ATaskStore(database)  # type: ignore[arg-type]
    binding = A2ATaskBinding("tenant_1", "run_1", "session_1", "run_1")

    assert await store.bind(scope=SCOPE, binding=binding) == binding
    assert await store.bind(scope=SCOPE, binding=binding) == binding
    assert await store.resolve(scope=SCOPE, task_id="run_1") == binding
    assert (
        await store.resolve(
            scope=RequestScope("tenant_2", "principal_1"),
            task_id="run_1",
        )
        is None
    )


@async_test
async def test_postgres_a2a_task_store_rejects_conflicting_binding() -> None:
    store = PostgresA2ATaskStore(Database())  # type: ignore[arg-type]

    await store.bind(
        scope=SCOPE,
        binding=A2ATaskBinding("tenant_1", "run_1", "session_1", "run_1"),
    )
    with pytest.raises(
        A2ATaskConflictError,
        match="^a2a task conflicts with an existing binding$",
    ):
        await store.bind(
            scope=SCOPE,
            binding=A2ATaskBinding(
                "tenant_1",
                "run_1",
                "other_session",
                "run_1",
            ),
        )


def test_runtime_schema_contains_tenant_scoped_a2a_task_binding() -> None:
    from agentos.distributed.postgres.schema import SCHEMA_STATEMENTS

    schema = " ".join("\n".join(SCHEMA_STATEMENTS).lower().split())

    assert "agentos_distributed_a2a_tasks" in schema
    assert "primary key (tenant_id, task_id)" in schema
    assert "unique (tenant_id, run_id)" in schema
