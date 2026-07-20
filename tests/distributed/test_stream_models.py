from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from agentos.distributed.models import (
    LiveContentDelta,
    LiveToolStatus,
    LiveTurnFailed,
    ReplayBatch,
    ReplayItem,
    RunEventEnvelope,
    StreamGap,
    WorkerState,
    project_live_event,
)
from agentos.runtime.stream_events import (
    AssistantContentDelta,
    AssistantThinkingDelta,
    ToolStreamCompleted,
    TurnStreamFailed,
    TurnStreamStarted,
)


NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _event(*, tenant_id: str = "tenant_1", sequence: int = 0) -> RunEventEnvelope:
    return RunEventEnvelope(
        tenant_id=tenant_id,
        session_id="session_1",
        run_id="run_1",
        turn_id="turn_1",
        execution_attempt=2,
        event_sequence=sequence,
        event=LiveContentDelta(index=0, text="quoted result"),
        occurred_at=NOW,
    )


def test_live_event_projection_is_allowlisted_and_redacts_internal_payloads() -> None:
    started = project_live_event(TurnStreamStarted("secret prompt"))
    content = project_live_event(AssistantContentDelta(0, "visible answer"))
    tool = project_live_event(
        ToolStreamCompleted("load_attachment", "call_1", "secret tool result"),
    )
    failed = project_live_event(TurnStreamFailed(RuntimeError("secret error")))

    assert started is not None and not hasattr(started, "user_message")
    assert content == LiveContentDelta(0, "visible answer")
    assert tool == LiveToolStatus("load_attachment", "call_1", "completed")
    assert failed == LiveTurnFailed()
    assert project_live_event(AssistantThinkingDelta(0, "private reasoning")) is None
    assert "secret" not in repr((started, content, tool, failed))


def test_event_kind_is_derived_from_the_typed_projection() -> None:
    envelope = _event()

    assert envelope.event_kind == "content_delta"
    assert "event_kind" not in {field.name for field in fields(envelope)}


def test_stream_dtos_are_frozen_and_validate_ranges() -> None:
    envelope = _event()
    with pytest.raises(FrozenInstanceError):
        envelope.run_id = "other"  # type: ignore[misc]

    values = {
        "tenant_id": "tenant_1",
        "session_id": "session_1",
        "run_id": "run_1",
        "turn_id": "turn_1",
        "execution_attempt": 1,
        "event_sequence": 0,
        "event": LiveContentDelta(0, "answer"),
        "occurred_at": NOW,
    }
    with pytest.raises(ValueError, match="execution_attempt"):
        RunEventEnvelope(**{**values, "execution_attempt": 0})
    with pytest.raises(ValueError, match="event_sequence"):
        RunEventEnvelope(**{**values, "event_sequence": True})


def test_replay_batch_identity_excludes_origin_principal() -> None:
    first = ReplayItem("1-0", _event(sequence=0))
    second = ReplayItem("2-0", _event(sequence=1))

    assert not hasattr(first.event, "scope")
    assert ReplayBatch((first, second), "2-0").items == (first, second)
    with pytest.raises(ValueError, match="next_cursor"):
        ReplayBatch((first, second), "1-0")
    with pytest.raises(ValueError, match="cursor"):
        ReplayBatch((first, first), "1-0")
    with pytest.raises(ValueError, match="run scope"):
        ReplayBatch(
            (first, ReplayItem("3-0", _event(tenant_id="tenant_2", sequence=2))),
            "3-0",
        )


def test_stream_gap_and_worker_state_enforce_invariants() -> None:
    with pytest.raises(ValueError, match="oldest_available_cursor"):
        StreamGap(
            tenant_id="tenant_1",
            session_id="session_1",
            run_id="run_1",
            requested_cursor="1-0",
            oldest_available_cursor=None,
            reason="trimmed",
        )
    with pytest.raises(ValueError, match="accepting_claims"):
        WorkerState(
            worker_id="worker_1",
            status="draining",
            accepting_claims=True,
            active_claim_count=1,
            last_heartbeat_at=NOW,
            drain_started_at=NOW,
        )
