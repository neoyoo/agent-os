from dataclasses import FrozenInstanceError

import pytest

from agentos._waiting import WaitReason
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime
from agentos.runtime.run_state import (
    RunAlreadyExistsError,
    RunNotFoundError,
    RunState,
    RunStateTransitionError,
    RunStatus,
)


def test_run_state_is_an_immutable_created_value() -> None:
    state = RunState(run_id="run_1", session_id="session_1")

    assert state.status is RunStatus.CREATED
    assert state.wait_reason is None
    assert not hasattr(state, "__dict__")
    with pytest.raises(FrozenInstanceError):
        state.status = RunStatus.QUEUED  # type: ignore[misc]


def test_run_runtime_completes_the_standard_lifecycle() -> None:
    runtime = RunRuntime(session_id="session_1", store=InMemoryRunStore())
    created = runtime.create_run(run_id="run_1")

    queued = runtime.queue(created.run_id)
    running = runtime.start(created.run_id)
    completed = runtime.complete(created.run_id)

    assert queued.status is RunStatus.QUEUED
    assert running.status is RunStatus.RUNNING
    assert completed.status is RunStatus.COMPLETED
    assert runtime.get_run(created.run_id) == completed


def test_run_runtime_resumes_waiting_through_queued() -> None:
    runtime = RunRuntime(session_id="session_1", store=InMemoryRunStore())
    runtime.create_run(run_id="run_1")
    runtime.queue("run_1")
    runtime.start("run_1")
    reason = WaitReason(kind="human_input", handle="approval_1")

    waiting = runtime.wait("run_1", reason=reason)
    queued = runtime.queue("run_1")
    running = runtime.start("run_1")

    assert waiting.status is RunStatus.WAITING
    assert waiting.wait_reason == reason
    assert queued.status is RunStatus.QUEUED
    assert queued.wait_reason is None
    assert running.status is RunStatus.RUNNING


@pytest.mark.parametrize(
    ("terminal_method", "terminal_status"),
    [
        ("complete", RunStatus.COMPLETED),
        ("fail", RunStatus.FAILED),
        ("cancel", RunStatus.CANCELLED),
    ],
)
def test_terminal_run_cannot_be_reactivated(
    terminal_method: str,
    terminal_status: RunStatus,
) -> None:
    store = InMemoryRunStore()
    runtime = RunRuntime(session_id="session_1", store=store)
    runtime.create_run(run_id="run_1")
    runtime.queue("run_1")
    runtime.start("run_1")
    terminal = getattr(runtime, terminal_method)("run_1")

    for target in RunStatus:
        with pytest.raises(
            RunStateTransitionError,
            match=f"{terminal_status.value} -> {target.value}",
        ):
            store.transition(
                session_id="session_1",
                run_id="run_1",
                status=target,
            )

    assert runtime.get_run("run_1") == terminal


@pytest.mark.parametrize(
    "source",
    [
        RunStatus.CREATED,
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.WAITING,
    ],
)
def test_all_non_terminal_runs_can_be_cancelled(source: RunStatus) -> None:
    store = InMemoryRunStore()
    runtime = RunRuntime(session_id="session_1", store=store)
    reason = WaitReason(kind="timer", handle="timer_1")
    store.create(
        RunState(
            run_id="run_1",
            session_id="session_1",
            status=source,
            wait_reason=reason if source is RunStatus.WAITING else None,
        ),
    )

    cancelled = runtime.cancel("run_1")

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.wait_reason is None


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunStatus.CREATED, RunStatus.RUNNING),
        (RunStatus.QUEUED, RunStatus.WAITING),
        (RunStatus.RUNNING, RunStatus.QUEUED),
        (RunStatus.WAITING, RunStatus.RUNNING),
    ],
)
def test_illegal_transition_is_fail_closed(
    source: RunStatus,
    target: RunStatus,
) -> None:
    store = InMemoryRunStore()
    runtime = RunRuntime(session_id="session_1", store=store)
    reason = WaitReason(kind="timer", handle="timer_1")
    state = RunState(
        run_id="run_1",
        session_id="session_1",
        status=source,
        wait_reason=reason if source is RunStatus.WAITING else None,
    )
    store.create(state)

    with pytest.raises(
        RunStateTransitionError,
        match=f"{source.value} -> {target.value}",
    ):
        store.transition(
            session_id="session_1",
            run_id="run_1",
            status=target,
        )

    assert runtime.get_run("run_1") == state


def test_waiting_transition_requires_a_reason() -> None:
    store = InMemoryRunStore()
    store.create(
        RunState(
            run_id="run_1",
            session_id="session_1",
            status=RunStatus.RUNNING,
        ),
    )

    with pytest.raises(
        RunStateTransitionError,
        match="waiting transition requires a wait reason",
    ):
        store.transition(
            session_id="session_1",
            run_id="run_1",
            status=RunStatus.WAITING,
        )

    assert store.get(session_id="session_1", run_id="run_1").status is (
        RunStatus.RUNNING
    )


def test_run_runtime_is_scoped_to_one_session() -> None:
    store = InMemoryRunStore()
    first = RunRuntime(session_id="session_1", store=store)
    second = RunRuntime(session_id="session_2", store=store)
    first.create_run(run_id="same_run")
    second.create_run(run_id="same_run")

    assert first.get_run("same_run").session_id == "session_1"
    assert second.get_run("same_run").session_id == "session_2"

    with pytest.raises(RunNotFoundError, match="missing"):
        first.get_run("missing")


def test_duplicate_run_is_rejected_without_overwriting_original() -> None:
    runtime = RunRuntime(session_id="session_1", store=InMemoryRunStore())
    original = runtime.create_run(run_id="run_1")

    with pytest.raises(RunAlreadyExistsError, match="run_1"):
        runtime.create_run(run_id="run_1")

    assert runtime.get_run("run_1") == original


def test_create_run_generates_a_prefixed_identifier() -> None:
    runtime = RunRuntime(
        session_id="session_1",
        store=InMemoryRunStore(),
        id_factory=lambda: "run_fixed",
    )

    assert runtime.create_run().run_id == "run_fixed"
