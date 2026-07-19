from dataclasses import FrozenInstanceError

import pytest

from agentos.runtime.execution import AcceptedStartInput, AcceptedTurnExecution
from agentos.runtime.run import UserTurnInput
from agentos.runtime.run_runtime import RunWriteGuard


def test_accepted_start_input_preserves_deterministic_turn_identifiers() -> None:
    accepted = AcceptedStartInput(
        run_id="run_1",
        submission_id="submission_1",
        input=UserTurnInput("hello", artifact_handles=("art_1",)),
        turn_id="turn_1",
        user_message_id="message_1",
    )

    assert accepted.input.content == "hello"
    assert accepted.input.artifact_handles == ("art_1",)
    assert accepted.turn_id == "turn_1"
    assert accepted.user_message_id == "message_1"
    with pytest.raises(FrozenInstanceError):
        accepted.turn_id = "turn_2"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field_name",
    ["run_id", "submission_id", "turn_id", "user_message_id"],
)
def test_accepted_start_input_rejects_empty_identifiers(field_name: str) -> None:
    values: dict[str, object] = {
        "run_id": "run_1",
        "submission_id": "submission_1",
        "input": UserTurnInput("hello"),
        "turn_id": "turn_1",
        "user_message_id": "message_1",
    }
    values[field_name] = " "

    with pytest.raises(ValueError, match=field_name):
        AcceptedStartInput(**values)  # type: ignore[arg-type]


def test_accepted_turn_execution_binds_input_to_guard_and_mode() -> None:
    accepted = AcceptedStartInput(
        run_id="run_1",
        submission_id="submission_1",
        input=UserTurnInput("hello"),
        turn_id="turn_1",
        user_message_id="message_1",
    )
    guard = RunWriteGuard(
        expected_version=2,
        claim_id="claim_1",
        fencing_token=4,
    )

    execution = AcceptedTurnExecution(
        input=accepted,
        guard=guard,
        mode="start",
    )

    assert execution.input is accepted
    assert execution.guard is guard
    assert execution.mode == "start"


def test_accepted_turn_execution_rejects_unknown_mode() -> None:
    accepted = AcceptedStartInput(
        run_id="run_1",
        submission_id="submission_1",
        input=UserTurnInput("hello"),
        turn_id="turn_1",
        user_message_id="message_1",
    )

    with pytest.raises(ValueError, match="mode"):
        AcceptedTurnExecution(
            input=accepted,
            guard=RunWriteGuard(expected_version=1),
            mode="resume",  # type: ignore[arg-type]
        )
