from __future__ import annotations

import pytest

from agentos.distributed.models import RequestScope, RunReadModel
from agentos.distributed.services import RunQueryService
from agentos.runtime.run_state import RunStatus
from tests.planning._async import async_test


SCOPE = RequestScope("tenant_1", "team_delivery_service")


class ActiveRunPort:
    def __init__(self, result: RunReadModel | None) -> None:
        self.result = result
        self.calls: list[tuple[RequestScope, str]] = []

    async def get_run(self, **_: object) -> RunReadModel | None:
        raise AssertionError("active lookup must not scan by run ID")

    async def get_active_run(
        self,
        *,
        scope: RequestScope,
        session_id: str,
    ) -> RunReadModel | None:
        self.calls.append((scope, session_id))
        return self.result


@async_test
async def test_run_query_service_reads_active_run_by_session() -> None:
    expected = RunReadModel(
        "tenant_1",
        "session_1",
        "run_1",
        RunStatus.RUNNING,
        None,
        3,
        None,
    )
    port = ActiveRunPort(expected)

    result = await RunQueryService(port).get_active(SCOPE, "session_1")

    assert result is expected
    assert port.calls == [(SCOPE, "session_1")]


@async_test
async def test_run_query_service_allows_no_active_run() -> None:
    assert await RunQueryService(ActiveRunPort(None)).get_active(SCOPE, "session_1") is None


@async_test
async def test_run_query_service_rejects_terminal_or_cross_scope_active_result() -> None:
    terminal = RunReadModel(
        "tenant_1",
        "session_1",
        "run_1",
        RunStatus.FAILED,
        None,
        3,
        None,
    )
    with pytest.raises(RuntimeError, match="active run"):
        await RunQueryService(ActiveRunPort(terminal)).get_active(SCOPE, "session_1")

    other_session = RunReadModel(
        "tenant_1",
        "session_2",
        "run_1",
        RunStatus.RUNNING,
        None,
        3,
        None,
    )
    with pytest.raises(RuntimeError, match="active run"):
        await RunQueryService(ActiveRunPort(other_session)).get_active(SCOPE, "session_1")
