from datetime import UTC, datetime

import pytest

from agentos.distributed.errors import RunEventTooLargeError
from agentos.distributed.models import LiveContentDelta, RunEventEnvelope
from agentos.distributed.run_event_limits import (
    MAX_RUN_EVENT_JSON_BYTES,
    canonical_run_event_json,
    require_run_event_size,
)


def _envelope(text: str) -> RunEventEnvelope:
    return RunEventEnvelope(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        execution_attempt=1,
        event_sequence=0,
        event=LiveContentDelta(0, text),
        occurred_at=datetime(2026, 7, 21, tzinfo=UTC),
    )


def test_canonical_run_event_json_is_deterministic_and_bounded() -> None:
    encoded = canonical_run_event_json(_envelope("answer"))

    assert encoded.startswith(b'{"event":{"index":0,"text":"answer"}')
    assert len(encoded) < MAX_RUN_EVENT_JSON_BYTES
    require_run_event_size(_envelope("answer"))


def test_run_event_size_rejects_before_transport_append() -> None:
    oversized = _envelope("x" * MAX_RUN_EVENT_JSON_BYTES)

    with pytest.raises(
        RunEventTooLargeError,
        match="^run event exceeds protocol size limit$",
    ):
        require_run_event_size(oversized)
