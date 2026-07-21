from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from agentos.distributed.a2a_models import (
    A2ATaskBinding,
    A2ATaskListItem,
    A2ATaskListPage,
    A2ATaskListQuery,
)
from agentos.distributed.a2a_services import A2ATaskCatalogService
from agentos.distributed.models import RequestScope, RunReadModel
from agentos.runtime.run_state import RunStatus
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "principal_1")
UPDATED_AT = datetime(2026, 7, 21, 8, 30, tzinfo=UTC)


def _item(*, tenant_id: str = "tenant_1") -> A2ATaskListItem:
    return A2ATaskListItem(
        binding=A2ATaskBinding(tenant_id, "run_1", "session_1", "run_1"),
        run=RunReadModel(
            tenant_id=tenant_id,
            session_id="session_1",
            run_id="run_1",
            status=RunStatus.RUNNING,
            wait_reason=None,
            aggregate_version=2,
            result=None,
        ),
        status_updated_at=UPDATED_AT,
    )


@dataclass
class RecordingCatalogPort:
    page: A2ATaskListPage
    calls: list[tuple[RequestScope, A2ATaskListQuery]] = field(default_factory=list)

    async def list(
        self,
        *,
        scope: RequestScope,
        query: A2ATaskListQuery,
    ) -> A2ATaskListPage:
        self.calls.append((scope, query))
        return self.page


@async_test
async def test_a2a_task_catalog_service_returns_validated_page() -> None:
    query = A2ATaskListQuery(page_size=25, include_artifacts=True)
    page = A2ATaskListPage(
        items=(_item(),),
        next_page_token="next_page",
        page_size=25,
        total_size=1,
    )
    port = RecordingCatalogPort(page)
    service = A2ATaskCatalogService(port)

    assert await service.list(SCOPE, query) == page
    assert port.calls == [(SCOPE, query)]


@async_test
async def test_a2a_task_catalog_service_rejects_cross_scope_port_result() -> None:
    page = A2ATaskListPage(
        items=(_item(tenant_id="tenant_2"),),
        next_page_token="",
        page_size=50,
        total_size=1,
    )
    service = A2ATaskCatalogService(RecordingCatalogPort(page))

    with pytest.raises(RuntimeError, match="another scope"):
        await service.list(SCOPE, A2ATaskListQuery())


def test_a2a_task_list_query_rejects_invalid_pagination() -> None:
    with pytest.raises(ValueError, match="page_size"):
        A2ATaskListQuery(page_size=0)
    with pytest.raises(ValueError, match="page_token"):
        A2ATaskListQuery(page_token="")
