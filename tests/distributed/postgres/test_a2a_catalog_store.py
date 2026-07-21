from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from agentos.distributed.a2a_models import A2ATaskListQuery
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres.a2a_catalog import PostgresA2ATaskCatalogStore
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
NOW = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, object]]:
        return list(self._rows)


class CatalogConnection:
    def __init__(self, pages: list[list[dict[str, object]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, Sequence[object]]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> Cursor:
        self.calls.append((" ".join(query.split()).lower(), params))
        return Cursor(self.pages.pop(0))


class Database:
    def __init__(self, pages: list[list[dict[str, object]]]) -> None:
        self.connection_value = CatalogConnection(pages)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[CatalogConnection]:
        yield self.connection_value


def _row(task_id: str, updated_at: datetime, *, total_size: int = 3) -> dict[str, object]:
    return {
        "total_size": total_size,
        "tenant_id": "tenant_1",
        "task_id": task_id,
        "session_id": f"session_{task_id}",
        "run_id": task_id,
        "status": "running",
        "wait_kind": None,
        "wait_handle": None,
        "wait_detail": None,
        "wait_not_before": None,
        "aggregate_version": 2,
        "result_content": None,
        "status_updated_at": updated_at,
    }


@async_test
async def test_catalog_store_uses_scoped_keyset_page_and_bound_token() -> None:
    database = Database(
        [
            [
                _row("run_3", NOW),
                _row("run_2", NOW - timedelta(minutes=1)),
                _row("run_1", NOW - timedelta(minutes=2)),
            ],
            [_row("run_1", NOW - timedelta(minutes=2))],
        ],
    )
    store = PostgresA2ATaskCatalogStore(database)  # type: ignore[arg-type]
    query = A2ATaskListQuery(page_size=2)

    first = await store.list(scope=SCOPE, query=query)
    assert [item.binding.task_id for item in first.items] == ["run_3", "run_2"]
    assert first.total_size == 3
    assert first.next_page_token

    second = await store.list(
        scope=SCOPE,
        query=A2ATaskListQuery(page_size=2, page_token=first.next_page_token),
    )
    assert [item.binding.task_id for item in second.items] == ["run_1"]
    assert second.next_page_token == ""

    sql, params = database.connection_value.calls[0]
    assert "join agentos_distributed_runs" in sql
    assert "order by status_updated_at desc, task_id desc" in sql
    assert params[0] == "tenant_1"

    with pytest.raises(ValueError, match="page token"):
        await store.list(
            scope=RequestScope("tenant_2", "principal_1"),
            query=A2ATaskListQuery(page_size=2, page_token=first.next_page_token),
        )
    assert len(database.connection_value.calls) == 2


@async_test
async def test_catalog_token_is_bound_to_filters() -> None:
    database = Database(
        [[_row("run_2", NOW), _row("run_1", NOW - timedelta(minutes=1))]],
    )
    store = PostgresA2ATaskCatalogStore(database)  # type: ignore[arg-type]
    first = await store.list(
        scope=SCOPE,
        query=A2ATaskListQuery(page_size=1, context_id="session_run_2"),
    )

    with pytest.raises(ValueError, match="page token"):
        await store.list(
            scope=SCOPE,
            query=A2ATaskListQuery(
                page_size=1,
                context_id="session_other",
                page_token=first.next_page_token,
            ),
        )
