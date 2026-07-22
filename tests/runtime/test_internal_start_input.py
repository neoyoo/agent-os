from dataclasses import FrozenInstanceError

import pytest

from agentos._json_values import FrozenJsonObject, freeze_json_mapping
from agentos.runtime.execution import (
    AcceptedInternalStartInput,
    AcceptedTurnExecution,
    ApplyAcceptedInput,
)
from agentos.runtime.internal_start import canonical_internal_start_payload
from agentos.runtime.run_runtime import RunWriteGuard


def _payload() -> dict[str, object]:
    return {
        "team_id": "team_1",
        "message_id": "team_msg_1",
        "recipient_agent_id": "agent_2",
        "action": "team_read_messages",
    }


def test_accepted_internal_start_input_freezes_canonical_payload() -> None:
    payload = _payload()

    accepted = AcceptedInternalStartInput(
        run_id="run_1",
        submission_id="team_submission_1",
        source_kind="team_message",
        source_payload=payload,
        turn_id="turn_1",
    )
    payload["team_id"] = "team_changed"

    assert type(accepted.source_payload) is FrozenJsonObject
    assert accepted.source_payload["team_id"] == "team_1"
    with pytest.raises(FrozenInstanceError):
        accepted.turn_id = "turn_2"  # type: ignore[misc]


def test_accepted_turn_execution_accepts_internal_start() -> None:
    accepted = AcceptedInternalStartInput(
        "run_1",
        "team_submission_1",
        "team_message",
        _payload(),
        "turn_1",
    )

    execution = AcceptedTurnExecution(
        input=accepted,
        guard=RunWriteGuard(expected_version=1, claim_id="claim_1", fencing_token=1),
        preparation=ApplyAcceptedInput(),
    )

    assert execution.input is accepted


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("source_kind", "user_message"),
        ("source_payload", {"team_id": "team_1"}),
        (
            "source_payload",
            {
                "team_id": "team_1",
                "message_id": "team_msg_1",
                "recipient_agent_id": "agent_2",
                "action": "not_allowed",
            },
        ),
    ],
)
def test_accepted_internal_start_input_rejects_noncanonical_source(
    field_name: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "run_id": "run_1",
        "submission_id": "team_submission_1",
        "source_kind": "team_message",
        "source_payload": _payload(),
        "turn_id": "turn_1",
    }
    values[field_name] = value

    with pytest.raises((TypeError, ValueError)):
        AcceptedInternalStartInput(**values)  # type: ignore[arg-type]


def test_accepted_internal_start_input_rejects_payload_over_four_kib() -> None:
    payload = _payload()
    payload["message_id"] = "m" * 4096

    with pytest.raises(ValueError, match="4096 UTF-8 bytes"):
        canonical_internal_start_payload(freeze_json_mapping(payload))
