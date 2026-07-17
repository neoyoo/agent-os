import inspect
from dataclasses import FrozenInstanceError
from typing import get_type_hints

import pytest

from agentos.runtime import WaitReason
from agentos.runtime.waiting import WaitingCommit, WaitingRuntime


def test_waiting_commit_is_a_frozen_slotted_value() -> None:
    reason = WaitReason(
        kind="resource_availability",
        handle="worker_pool",
        detail="capacity exhausted",
    )
    commit = WaitingCommit(run_id="run_1", reason=reason)

    assert commit.run_id == "run_1"
    assert commit.reason == reason
    assert not hasattr(commit, "__dict__")
    with pytest.raises(FrozenInstanceError):
        commit.run_id = "run_2"  # type: ignore[misc]


def test_waiting_runtime_exposes_only_async_commit_waiting() -> None:
    public_methods = {
        name
        for name, value in vars(WaitingRuntime).items()
        if not name.startswith("_") and callable(value)
    }

    assert public_methods == {"commit_waiting"}
    assert inspect.iscoroutinefunction(WaitingRuntime.commit_waiting)

    signature = inspect.signature(WaitingRuntime.commit_waiting)
    assert signature.parameters["run_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["turn_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["reason"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(WaitingRuntime.commit_waiting)["return"] is WaitingCommit
