from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from agentos.runtime.errors import (
    AgentBusyError,
    AgentRunError,
    AgentStreamClosedError,
    AgentStreamConsumerError,
    ContinuationUnavailableError,
    RunProtocolError,
    SyncAdapterEventLoopError,
    SyncAdapterReentryError,
    SyncAgentClosedError,
    SyncStreamConsumerError,
    WaitingUnsupportedError,
)
from agentos.runtime.run import (
    AgentResult,
    AgentWaiting,
    LocalContinuationInput,
    RunInput,
    RunRequest,
    UserTurnInput,
)
from agentos.runtime import WaitReason
from agentos.runtime.run import RunOptions


def test_user_turn_input_is_frozen_and_uses_artifact_handle_tuple() -> None:
    handle = "art_12345678-1234-4234-9234-123456789abc"
    user_input = UserTurnInput(content="hello", artifact_handles=(handle,))

    assert user_input.artifact_handles == (handle,)
    assert isinstance(user_input.artifact_handles, tuple)
    assert not hasattr(user_input, "__dict__")
    with pytest.raises(FrozenInstanceError):
        user_input.content = "changed"  # type: ignore[misc]


def test_run_request_is_frozen_and_builds_fresh_default_options() -> None:
    first = RunRequest(input=UserTurnInput(content="first"))
    second = RunRequest(input=LocalContinuationInput())

    assert first.options == RunOptions()
    assert first.options is not second.options
    assert not hasattr(first, "__dict__")
    with pytest.raises(FrozenInstanceError):
        first.options = RunOptions(thinking=True)  # type: ignore[misc]


def test_run_input_contains_all_supported_input_forms() -> None:
    assert set(get_args(RunInput)) == {
        str,
        UserTurnInput,
        LocalContinuationInput,
    }


def test_local_continuation_input_has_fieldless_value_semantics() -> None:
    assert LocalContinuationInput() == LocalContinuationInput()
    assert hash(LocalContinuationInput()) == hash(LocalContinuationInput())
    assert not hasattr(LocalContinuationInput(), "__dict__")


def test_agent_result_and_waiting_are_distinct_outcomes() -> None:
    result = AgentResult(content="done")
    waiting = AgentWaiting(
        run_id="run_1",
        reason=WaitReason(kind="human_input", handle="approval_1"),
    )

    assert type(result) is AgentResult
    assert type(waiting) is AgentWaiting
    assert result != waiting
    assert waiting.reason.detail is None


@pytest.mark.parametrize(
    ("value", "field_name", "replacement"),
    [
        (WaitReason(kind="human_input", handle="approval_1"), "kind", "timer"),
        (AgentResult(content="done"), "content", "changed"),
        (
            AgentWaiting(
                run_id="run_1",
                reason=WaitReason(kind="timer", handle="timer_1"),
            ),
            "run_id",
            "run_2",
        ),
    ],
)
def test_run_outcome_values_are_frozen_and_slotted(
    value: object,
    field_name: str,
    replacement: object,
) -> None:
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        setattr(value, field_name, replacement)


def test_run_errors_share_one_direct_runtime_error_level() -> None:
    assert AgentRunError.__bases__ == (RuntimeError,)

    errors = (
        AgentBusyError,
        AgentStreamConsumerError,
        AgentStreamClosedError,
        ContinuationUnavailableError,
        WaitingUnsupportedError,
        RunProtocolError,
        SyncAdapterEventLoopError,
        SyncAdapterReentryError,
        SyncAgentClosedError,
        SyncStreamConsumerError,
    )
    assert all(error.__bases__ == (AgentRunError,) for error in errors)
