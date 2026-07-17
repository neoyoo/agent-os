from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from agentos._waiting import WaitReason
from agentos.runtime.durable_commands import (
    AcceptedContinuationInput,
    DurableCommandReceipt,
    DurableRunCommand,
)
from agentos.runtime.run_state import RunState, RunStatus


def test_durable_command_copies_and_freezes_json_payload() -> None:
    payload = {"answer": {"approved": True}, "items": [1, 2]}

    command = DurableRunCommand(
        run_id="run_1",
        command_id="cmd_1",
        kind="hitl_answer",
        payload=payload,
    )
    payload["answer"] = {"approved": False}
    payload["items"].append(3)  # type: ignore[union-attr]

    assert command.payload == {
        "answer": {"approved": True},
        "items": [1, 2],
    }
    assert not hasattr(command, "__dict__")
    with pytest.raises(FrozenInstanceError):
        command.kind = "cancel"  # type: ignore[misc]


@pytest.mark.parametrize("field", ["run_id", "command_id"])
def test_durable_command_requires_nonempty_identifiers(field: str) -> None:
    values = {
        "run_id": "run_1",
        "command_id": "cmd_1",
        "kind": "resume",
        "payload": {},
    }
    values[field] = " "

    with pytest.raises(ValueError, match=field):
        DurableRunCommand(**values)  # type: ignore[arg-type]


def test_accepted_input_and_receipt_are_frozen_domain_values() -> None:
    accepted = AcceptedContinuationInput(
        run_id="run_1",
        command_id="cmd_1",
        kind="resume",
        payload={},
        aggregate_version=4,
    )
    receipt = DurableCommandReceipt(
        run_id="run_1",
        command_id="cmd_1",
        kind="resume",
        aggregate_version=4,
        duplicate=True,
    )

    assert accepted.aggregate_version == 4
    assert receipt.duplicate is True
    assert not hasattr(accepted, "__dict__")
    assert not hasattr(receipt, "__dict__")


def test_timer_and_retry_waits_require_utc_not_before() -> None:
    due = datetime(2026, 7, 17, 12, tzinfo=UTC)

    timer = WaitReason("timer", "timer_1", not_before=due)
    retry = WaitReason("retry_backoff", "retry_1", not_before=due)

    assert timer.not_before == due
    assert retry.not_before == due
    with pytest.raises(ValueError, match="not_before"):
        WaitReason("timer", "timer_1")
    with pytest.raises(ValueError, match="not_before"):
        WaitReason("retry_backoff", "retry_1")


def test_wait_reason_normalizes_aware_datetime_to_utc() -> None:
    plus_eight = timezone(timedelta(hours=8))

    reason = WaitReason(
        "timer",
        "timer_1",
        not_before=datetime(2026, 7, 17, 20, tzinfo=plus_eight),
    )

    assert reason.not_before == datetime(2026, 7, 17, 12, tzinfo=UTC)


def test_non_timer_wait_rejects_not_before() -> None:
    with pytest.raises(ValueError, match="not_before"):
        WaitReason(
            "human_input",
            "approval_1",
            not_before=datetime(2026, 7, 17, 12, tzinfo=UTC),
        )


def test_run_state_version_increments_on_every_transition() -> None:
    created = RunState("run_1", "session_1")
    queued = created.transition(status=RunStatus.QUEUED)
    running = queued.transition(status=RunStatus.RUNNING)

    assert created.aggregate_version == 0
    assert queued.aggregate_version == 1
    assert running.aggregate_version == 2
