from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentos.distributed.models import (
    AcceptedStartInput,
    AcceptedTurnExecution,
    ClaimedExecution,
    ExecutionClaim,
    OutboxClaim,
    OutboxRecord,
    QueueDelivery,
    RequestScope,
    RunDeliveryTarget,
    SessionLease,
)
from agentos.runtime.execution import AcceptedTurnExecution as RuntimeExecution
from agentos.runtime.run import UserTurnInput
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.run_state import RunState, RunStatus


NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _claim() -> ExecutionClaim:
    return ExecutionClaim(
        tenant_id="tenant_1",
        session_id="session_1",
        run_id="run_1",
        owner_id="worker_1",
        claim_id="claim_1",
        fencing_token=2,
        expires_at=NOW + timedelta(minutes=1),
    )


def _accepted() -> AcceptedStartInput:
    return AcceptedStartInput(
        run_id="run_1",
        submission_id="submission_1",
        input=UserTurnInput("inspect"),
        turn_id="turn_1",
        user_message_id="message_1",
    )


def test_delivery_dtos_are_frozen_slotted_and_validate_counts() -> None:
    values = (
        QueueDelivery("1-0", "outbox_1", 1),
        OutboxRecord(
            scope=RequestScope("tenant_1", "user_1"),
            outbox_id="outbox_1",
            topic="run_wakeup",
            payload={"run_id": "run_1"},
            created_at=NOW,
        ),
    )
    for value in values:
        assert not hasattr(value, "__dict__")
        field_name = fields(value)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(value, field_name, getattr(value, field_name))

    with pytest.raises(ValueError, match="delivery_count"):
        QueueDelivery("1-0", "outbox_1", True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="delivery_count"):
        QueueDelivery("1-0", "outbox_1", 0)


def test_accepted_execution_is_the_canonical_runtime_type() -> None:
    assert AcceptedTurnExecution is RuntimeExecution


def test_claimed_execution_binds_target_claim_guard_and_mode() -> None:
    scope = RequestScope("tenant_1", "user_1")
    target = RunDeliveryTarget(
        scope,
        "outbox_1",
        "session_1",
        RunState("run_1", "session_1", status=RunStatus.QUEUED),
    )
    execution = AcceptedTurnExecution(
        input=_accepted(),
        guard=RunWriteGuard(0, "claim_1", 2),
        mode="start",
    )

    assert ClaimedExecution(target, _claim(), execution).execution is execution
    assert not hasattr(QueueDelivery("1-0", "outbox_1", 1), "tenant_id")

    with pytest.raises(ValueError, match="session"):
        RunDeliveryTarget(scope, "outbox_1", "other_session", target.run)
    with pytest.raises(ValueError, match="accepted execution"):
        ClaimedExecution(
            target,
            _claim(),
            AcceptedTurnExecution(
                input=AcceptedStartInput(
                    run_id="run_2",
                    submission_id="submission_2",
                    input=UserTurnInput("inspect"),
                    turn_id="turn_2",
                    user_message_id="message_2",
                ),
                guard=RunWriteGuard(0, "claim_2", 2),
                mode="start",
            ),
        )
    with pytest.raises(ValueError, match="guard"):
        ClaimedExecution(
            target,
            _claim(),
            AcceptedTurnExecution(
                input=_accepted(),
                guard=RunWriteGuard(0, "other_claim", 9),
                mode="start",
            ),
        )
    with pytest.raises(ValueError, match="version"):
        ClaimedExecution(
            target,
            _claim(),
            AcceptedTurnExecution(
                input=_accepted(),
                guard=RunWriteGuard(1, "claim_1", 2),
                mode="start",
            ),
        )
    with pytest.raises(ValueError, match="mode"):
        ClaimedExecution(
            RunDeliveryTarget(
                scope,
                "outbox_1",
                "session_1",
                RunState("run_1", "session_1", status=RunStatus.CREATED),
            ),
            _claim(),
            execution,
        )

    recovered = ClaimedExecution(
        RunDeliveryTarget(
            scope,
            "outbox_1",
            "session_1",
            RunState("run_1", "session_1", status=RunStatus.RUNNING),
        ),
        _claim(),
        AcceptedTurnExecution(
            input=_accepted(),
            guard=RunWriteGuard(0, "claim_1", 2),
            mode="recover",
        ),
    )
    assert recovered.execution.mode == "recover"


def test_outbox_datetimes_are_normalized_and_payload_is_frozen() -> None:
    local_time = datetime(
        2026,
        7,
        20,
        20,
        tzinfo=timezone(timedelta(hours=8)),
    )
    payload = {"items": ["a"]}
    record = OutboxRecord(
        scope=RequestScope("tenant_1", "user_1"),
        outbox_id="outbox_1",
        topic="run_wakeup",
        payload=payload,
        created_at=local_time,
    )
    payload["items"].append("b")

    assert record.created_at == NOW
    assert record.payload == {"items": ["a"]}
    with pytest.raises(TypeError):
        record.payload["new"] = 1  # type: ignore[index]
    with pytest.raises(ValueError, match="timezone-aware"):
        OutboxRecord(
            scope=record.scope,
            outbox_id="outbox_2",
            topic="run_wakeup",
            payload={},
            created_at=datetime(2026, 7, 20, 12),
        )


def test_outbox_requires_consistent_publish_attempt_metadata() -> None:
    values = {
        "scope": RequestScope("tenant_1", "user_1"),
        "outbox_id": "outbox_1",
        "topic": "run_wakeup",
        "payload": {},
        "created_at": NOW,
    }
    with pytest.raises(ValueError, match="created_at"):
        OutboxRecord(
            **values,
            last_publish_attempt_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="zero publish attempts"):
        OutboxRecord(**values, published_at=NOW)
    with pytest.raises(ValueError, match="last_publish_attempt_at"):
        OutboxRecord(**values, publish_attempts=1)
    with pytest.raises(ValueError, match="published_at"):
        OutboxRecord(
            **values,
            publish_attempts=1,
            last_publish_attempt_at=NOW + timedelta(seconds=2),
            published_at=NOW + timedelta(seconds=1),
        )


def test_lease_and_outbox_claim_normalize_and_validate_expiry() -> None:
    scope = RequestScope("tenant_1", "user_1")
    local_expiry = datetime(
        2026,
        7,
        20,
        21,
        tzinfo=timezone(timedelta(hours=8)),
    )
    lease = SessionLease(scope, "session_1", "worker_1", "lease_1", local_expiry)
    record = OutboxRecord(
        scope=scope,
        outbox_id="outbox_1",
        topic="run_wakeup",
        payload={},
        created_at=NOW,
    )

    assert lease.expires_at == datetime(2026, 7, 20, 13, tzinfo=UTC)
    with pytest.raises(ValueError, match="expires_at"):
        OutboxClaim(
            record=record,
            owner_id="relay_1",
            claim_id="claim_1",
            expires_at=NOW - timedelta(seconds=1),
        )
