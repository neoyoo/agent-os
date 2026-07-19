import asyncio
import inspect
from dataclasses import FrozenInstanceError

import pytest

from agentos.runtime.run_runtime import (
    InMemoryRunStore,
    RunRuntime,
    RunStore,
    RunWriteGuard,
)
from agentos.runtime.run_state import RunState, RunStatus


class SuspendingRunStore:
    def __init__(self) -> None:
        self.create_started = asyncio.Event()
        self.release_create = asyncio.Event()
        self.state: RunState | None = None

    async def create(self, state: RunState) -> RunState:
        self.create_started.set()
        await self.release_create.wait()
        self.state = state
        return state

    async def get(self, *, session_id: str, run_id: str) -> RunState | None:
        return self.state

    async def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason: object | None,
        guard: RunWriteGuard,
        turn_id: str | None = None,
    ) -> RunState:
        assert self.state is not None
        self.state = self.state.transition(status)
        return self.state


def test_run_store_and_in_memory_adapter_are_native_async() -> None:
    for owner in (RunStore, InMemoryRunStore):
        assert inspect.iscoroutinefunction(owner.create)
        assert inspect.iscoroutinefunction(owner.get)
        assert inspect.iscoroutinefunction(owner.transition)


def test_run_runtime_awaits_store_without_blocking_the_event_loop() -> None:
    async def run() -> None:
        store = SuspendingRunStore()
        runtime = RunRuntime(session_id="session_1", store=store)
        task = asyncio.create_task(runtime.create_run(run_id="run_1"))

        await store.create_started.wait()
        assert not task.done()
        await asyncio.sleep(0)

        store.release_create.set()
        assert await task == RunState("run_1", "session_1")

    asyncio.run(run())


def test_run_write_guard_is_immutable_and_requires_a_version() -> None:
    guard = RunWriteGuard(expected_version=3)

    assert guard.expected_version == 3
    assert guard.claim_id is None
    assert guard.fencing_token is None
    with pytest.raises(FrozenInstanceError):
        guard.expected_version = 4  # type: ignore[misc]


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_run_write_guard_rejects_invalid_versions(value: object) -> None:
    with pytest.raises(ValueError, match="expected_version"):
        RunWriteGuard(expected_version=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("claim_id", "fencing_token"),
    [("claim_1", None), (None, 1), (" ", 1), ("claim_1", 0)],
)
def test_run_write_guard_requires_a_complete_valid_fence(
    claim_id: str | None,
    fencing_token: int | None,
) -> None:
    with pytest.raises(ValueError, match="claim_id and fencing_token"):
        RunWriteGuard(
            expected_version=3,
            claim_id=claim_id,
            fencing_token=fencing_token,
        )


def test_run_write_guard_accepts_a_distributed_fence() -> None:
    guard = RunWriteGuard(
        expected_version=3,
        claim_id="claim_1",
        fencing_token=7,
    )

    assert guard.claim_id == "claim_1"
    assert guard.fencing_token == 7
