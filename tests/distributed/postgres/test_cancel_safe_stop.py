from __future__ import annotations

import pytest

from agentos.capabilities.tools import SideEffectPolicy
from agentos.distributed.errors import SideEffectInFlightError
from agentos.distributed.postgres._side_effect_codec import (
    side_effect_record_from_json,
    side_effect_record_to_json,
)
from agentos.distributed.postgres._side_effect_composite import (
    apply_cancel_safe_stop,
)
from agentos.runtime.payloads import ProtectedPayloadRef
from agentos.runtime.side_effect_types import (
    SideEffectAttemptId,
    SideEffectRecord,
    SideEffectResolutionOutcome,
    SideEffectStatus,
)
from tests.planning._async import async_test


OPERATION_ID = "operation_0123456789abcdef0123456789abcdef"
INVOCATION_ID = "invocation_0123456789abcdef0123456789abcdef"


class Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def fetchone(self) -> None:
        return None

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


def _side_effect(status: SideEffectStatus) -> SideEffectRecord:
    return SideEffectRecord(
        attempt_id=SideEffectAttemptId(
            "tenant_1",
            "session_1",
            OPERATION_ID,
            1,
        ),
        run_id="run_1",
        turn_id="turn_1",
        invocation_id=INVOCATION_ID,
        tool_name="charge",
        policy=SideEffectPolicy.DEDUPLICATED,
        status=status,
        invocation_digest="sha256:" + "a" * 64,
        invocation_ref=ProtectedPayloadRef("sealed", "digest"),
        claim_id="claim_1",
        fencing_token=7,
    )


def _side_effect_row(record: SideEffectRecord) -> dict[str, object]:
    return {
        "tenant_id": record.attempt_id.tenant_id,
        "session_id": record.attempt_id.session_id,
        "operation_id": record.attempt_id.operation_id,
        "attempt": record.attempt_id.attempt,
        "run_id": record.run_id,
        "status": record.status.value,
        "payload_json": side_effect_record_to_json(record),
    }


class CancelConnection:
    def __init__(self, record: SideEffectRecord) -> None:
        self._row = _side_effect_row(record)
        self.writes: list[tuple[str, tuple[object, ...]]] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...] = (),
    ) -> Cursor:
        normalized = " ".join(query.split())
        if normalized.startswith("SELECT effect.*"):
            return Cursor([self._row])
        self.writes.append((normalized, params))
        return Cursor([])


@async_test
async def test_cancel_resolves_reserved_effect_before_start() -> None:
    connection = CancelConnection(_side_effect(SideEffectStatus.RESERVED))

    await apply_cancel_safe_stop(  # type: ignore[arg-type]
        connection,
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
    )

    assert len(connection.writes) == 1
    _, params = connection.writes[0]
    record = side_effect_record_from_json(params[1])  # type: ignore[arg-type]
    assert record.status is SideEffectStatus.RESOLVED
    assert record.resolution is SideEffectResolutionOutcome.CANCELLED_BEFORE_START


@async_test
async def test_cancel_in_flight_effect_performs_no_writes() -> None:
    connection = CancelConnection(_side_effect(SideEffectStatus.STARTED))

    with pytest.raises(SideEffectInFlightError):
        await apply_cancel_safe_stop(  # type: ignore[arg-type]
            connection,
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
        )

    assert connection.writes == []
