from __future__ import annotations

import pytest

from agentos.distributed.errors import ClaimConflictError
from agentos.distributed.models import RequestScope
from agentos.distributed.postgres._claim_preparation import (
    classify_accepted_turn_preparation,
)
from agentos.durable.serialization import execution_cursor_to_json
from agentos.runtime.execution import (
    AcceptedStartInput,
    ApplyAcceptedInput,
    RestoreAcceptedTurn,
    RunExecutionCursor,
)
from agentos.runtime.run import UserTurnInput
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState, RunStatus
from tests.planning._async import async_test


class _Cursor:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, object] | None:
        return self._row


class _Connection:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    async def execute(self, query: str, params=()):  # type: ignore[no-untyped-def]
        del query, params
        return _Cursor(self._row)


def _accepted() -> AcceptedStartInput:
    return AcceptedStartInput(
        "run_1",
        "submission_1",
        UserTurnInput("inspect"),
        "turn_1",
        "message_1",
    )


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
@async_test
async def test_missing_current_turn_cursor_requires_first_application(
    status: RunStatus,
) -> None:
    preparation = await classify_accepted_turn_preparation(
        _Connection(None),  # type: ignore[arg-type]
        scope=RequestScope("tenant_1", "principal_1"),
        run=RunState("run_1", "session_1", status=status, aggregate_version=4),
        accepted=_accepted(),
        guard=RunWriteGuard(4, "claim_1", 7),
    )

    assert preparation == ApplyAcceptedInput()


@async_test
async def test_current_turn_cursor_requires_ordinary_restore() -> None:
    cursor = RunExecutionCursor("turn_1", "before_provider", 0)
    preparation = await classify_accepted_turn_preparation(
        _Connection(
            {
                "payload_json": execution_cursor_to_json(cursor),
                "turn_id": cursor.turn_id,
                "aggregate_version": 4,
            },
        ),  # type: ignore[arg-type]
        scope=RequestScope("tenant_1", "principal_1"),
        run=RunState(
            "run_1",
            "session_1",
            status=RunStatus.RUNNING,
            aggregate_version=4,
        ),
        accepted=_accepted(),
        guard=RunWriteGuard(4, "claim_1", 7),
    )

    assert preparation == RestoreAcceptedTurn(cursor)


@async_test
async def test_cursor_from_another_turn_fails_closed() -> None:
    cursor = RunExecutionCursor("turn_other", "before_provider", 0)

    with pytest.raises(ClaimConflictError):
        await classify_accepted_turn_preparation(
            _Connection(
                {
                    "payload_json": execution_cursor_to_json(cursor),
                    "turn_id": cursor.turn_id,
                    "aggregate_version": 4,
                },
            ),  # type: ignore[arg-type]
            scope=RequestScope("tenant_1", "principal_1"),
            run=RunState(
                "run_1",
                "session_1",
                status=RunStatus.RUNNING,
                aggregate_version=4,
            ),
            accepted=_accepted(),
            guard=RunWriteGuard(4, "claim_1", 7),
        )
