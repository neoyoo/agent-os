import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from agentos._waiting import WaitReason
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime, RunWriteGuard
from agentos.runtime.run_state import (
    RunAlreadyExistsError,
    RunNotFoundError,
    RunState,
    RunStateTransitionError,
    RunStatus,
)


_TIMER_DUE = datetime(2026, 7, 17, 12, tzinfo=UTC)


def test_run_state_is_an_immutable_created_value() -> None:
    state = RunState(run_id="run_1", session_id="session_1")

    assert state.status is RunStatus.CREATED
    assert state.wait_reason is None
    assert not hasattr(state, "__dict__")
    with pytest.raises(FrozenInstanceError):
        state.status = RunStatus.QUEUED  # type: ignore[misc]


def test_run_runtime_completes_the_standard_lifecycle() -> None:
    async def run() -> None:
        runtime = RunRuntime(session_id="session_1", store=InMemoryRunStore())
        created = await runtime.create_run(run_id="run_1")

        queued = await runtime.queue(created.run_id, guard=_guard(created))
        running = await runtime.start(created.run_id, guard=_guard(queued))
        completed = await runtime.complete(created.run_id, guard=_guard(running))

        assert queued.status is RunStatus.QUEUED
        assert running.status is RunStatus.RUNNING
        assert completed.status is RunStatus.COMPLETED
        assert await runtime.get_run(created.run_id) == completed

    asyncio.run(run())


def test_run_runtime_resumes_waiting_through_queued() -> None:
    async def run() -> None:
        runtime = RunRuntime(session_id="session_1", store=InMemoryRunStore())
        created = await runtime.create_run(run_id="run_1")
        queued = await runtime.queue("run_1", guard=_guard(created))
        running = await runtime.start("run_1", guard=_guard(queued))
        reason = WaitReason(kind="human_input", handle="approval_1")

        waiting = await runtime.wait("run_1", reason=reason, guard=_guard(running))
        resumed = await runtime.queue("run_1", guard=_guard(waiting))
        restarted = await runtime.start("run_1", guard=_guard(resumed))

        assert waiting.status is RunStatus.WAITING
        assert waiting.wait_reason == reason
        assert resumed.status is RunStatus.QUEUED
        assert resumed.wait_reason is None
        assert restarted.status is RunStatus.RUNNING

    asyncio.run(run())


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
    async def run() -> None:
        store = InMemoryRunStore()
        runtime = RunRuntime(session_id="session_1", store=store)
        created = await runtime.create_run(run_id="run_1")
        queued = await runtime.queue("run_1", guard=_guard(created))
        running = await runtime.start("run_1", guard=_guard(queued))
        terminal = await getattr(runtime, terminal_method)(
            "run_1",
            guard=_guard(running),
        )

        for target in RunStatus:
            with pytest.raises(
                RunStateTransitionError,
                match=f"{terminal_status.value} -> {target.value}",
            ):
                await store.transition(
                    session_id="session_1",
                    run_id="run_1",
                    status=target,
                    guard=_guard(terminal),
                )

        assert await runtime.get_run("run_1") == terminal

    asyncio.run(run())


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
    async def run() -> None:
        store = InMemoryRunStore()
        runtime = RunRuntime(session_id="session_1", store=store)
        reason = WaitReason(kind="timer", handle="timer_1", not_before=_TIMER_DUE)
        state = await store.create(RunState(
            run_id="run_1",
            session_id="session_1",
            status=source,
            wait_reason=reason if source is RunStatus.WAITING else None,
        ))

        cancelled = await runtime.cancel("run_1", guard=_guard(state))

        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.wait_reason is None

    asyncio.run(run())


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
    async def run() -> None:
        store = InMemoryRunStore()
        runtime = RunRuntime(session_id="session_1", store=store)
        reason = WaitReason(kind="timer", handle="timer_1", not_before=_TIMER_DUE)
        state = RunState(
            run_id="run_1",
            session_id="session_1",
            status=source,
            wait_reason=reason if source is RunStatus.WAITING else None,
        )
        await store.create(state)

        with pytest.raises(
            RunStateTransitionError,
            match=f"{source.value} -> {target.value}",
        ):
            await store.transition(
                session_id="session_1",
                run_id="run_1",
                status=target,
                guard=_guard(state),
            )

        assert await runtime.get_run("run_1") == state

    asyncio.run(run())


def test_waiting_transition_requires_a_reason() -> None:
    async def run() -> None:
        store = InMemoryRunStore()
        state = await store.create(RunState(
            run_id="run_1",
            session_id="session_1",
            status=RunStatus.RUNNING,
        ))

        with pytest.raises(
            RunStateTransitionError,
            match="waiting transition requires a wait reason",
        ):
            await store.transition(
                session_id="session_1",
                run_id="run_1",
                status=RunStatus.WAITING,
                guard=_guard(state),
            )

        stored = await store.get(session_id="session_1", run_id="run_1")
        assert stored is not None
        assert stored.status is RunStatus.RUNNING

    asyncio.run(run())


def test_run_runtime_is_scoped_to_one_session() -> None:
    async def run() -> None:
        store = InMemoryRunStore()
        first = RunRuntime(session_id="session_1", store=store)
        second = RunRuntime(session_id="session_2", store=store)
        await first.create_run(run_id="same_run")
        await second.create_run(run_id="same_run")

        assert (await first.get_run("same_run")).session_id == "session_1"
        assert (await second.get_run("same_run")).session_id == "session_2"

        with pytest.raises(RunNotFoundError, match="missing"):
            await first.get_run("missing")

    asyncio.run(run())


def test_duplicate_run_is_rejected_without_overwriting_original() -> None:
    async def run() -> None:
        runtime = RunRuntime(session_id="session_1", store=InMemoryRunStore())
        original = await runtime.create_run(run_id="run_1")

        with pytest.raises(RunAlreadyExistsError, match="run_1"):
            await runtime.create_run(run_id="run_1")

        assert await runtime.get_run("run_1") == original

    asyncio.run(run())


def test_create_run_generates_a_prefixed_identifier() -> None:
    async def run() -> None:
        runtime = RunRuntime(
            session_id="session_1",
            store=InMemoryRunStore(),
            id_factory=lambda: "run_fixed",
        )

        assert (await runtime.create_run()).run_id == "run_fixed"

    asyncio.run(run())


def _guard(state: RunState) -> RunWriteGuard:
    return RunWriteGuard(expected_version=state.aggregate_version)
