import asyncio
from datetime import UTC, datetime

import pytest

from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.runtime.checkpoint import (
    RunCheckpoint,
    RuntimeCheckpointSource,
    SessionCheckpoint,
)
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.run_commit import RunCommitRuntime
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime, RunWriteGuard
from agentos.runtime.session import SessionState
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.waiting import LocalWaitingRuntime


NOW = datetime(2026, 7, 19, 12, tzinfo=UTC)


class RecordingCheckpointStore:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.checkpoints: list[SessionCheckpoint] = []
        self.store_tasks: list[asyncio.Task[object] | None] = []

    async def commit_running(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        self.order.append("store")
        self.checkpoints.append(checkpoint)
        self.store_tasks.append(asyncio.current_task())
        return RunCheckpoint(
            "checkpoint_1",
            checkpoint.session_id,
            run_id,
            turn_id,
            guard.expected_version + 1,
            NOW,
        )


class InvalidCheckpointStore(RecordingCheckpointStore):
    def __init__(self, field: str) -> None:
        super().__init__([])
        self.field = field

    async def commit_running(
        self,
        *,
        checkpoint: SessionCheckpoint,
        run_id: str,
        turn_id: str,
        guard: RunWriteGuard,
    ) -> RunCheckpoint:
        values = {
            "session_id": checkpoint.session_id,
            "run_id": run_id,
            "turn_id": turn_id,
            "aggregate_version": guard.expected_version + 1,
        }
        if self.field == "session_id":
            values["session_id"] = "session_other"
        elif self.field == "run_id":
            values["run_id"] = "run_other"
        elif self.field == "turn_id":
            values["turn_id"] = "turn_other"
        else:
            values["aggregate_version"] = guard.expected_version + 2
        return RunCheckpoint(
            "checkpoint_invalid",
            values["session_id"],  # type: ignore[arg-type]
            values["run_id"],  # type: ignore[arg-type]
            values["turn_id"],  # type: ignore[arg-type]
            values["aggregate_version"],  # type: ignore[arg-type]
            NOW,
        )


class FailingCheckpointSource:
    def capture(self, **_kwargs: object) -> SessionCheckpoint:
        raise RuntimeError("capture failed")


class RecordingCheckpointSource:
    def __init__(
        self,
        source: RuntimeCheckpointSource,
        order: list[str],
    ) -> None:
        self.source = source
        self.order = order
        self.capture_tasks: list[asyncio.Task[object] | None] = []

    def capture(self, **kwargs: object) -> SessionCheckpoint:
        self.order.append("capture")
        self.capture_tasks.append(asyncio.current_task())
        return self.source.capture(**kwargs)  # type: ignore[arg-type]


def _runtime(
    source: object,
    store: RecordingCheckpointStore,
) -> RunCommitRuntime:
    runs = RunRuntime(session_id="session_1", store=InMemoryRunStore())
    side_effects = InMemorySideEffectStore()
    return RunCommitRuntime(
        runs,
        LocalWaitingRuntime(runs, side_effects),
        checkpoint_source=source,  # type: ignore[arg-type]
        checkpoint_store=store,  # type: ignore[arg-type]
    )


def test_running_checkpoint_is_captured_before_store_handoff_in_loop_task() -> None:
    async def scenario() -> None:
        session = SessionState("session_1")
        session.new_turn("hello", turn_id="turn_1")
        messages = MessageRuntime()
        messages.append_user("hello", message_id="message_1")
        order: list[str] = []
        source = RecordingCheckpointSource(
            RuntimeCheckpointSource(session, messages, ContextRuntime()),
            order,
        )
        store = RecordingCheckpointStore(order)
        runtime = _runtime(source, store)
        current_task = asyncio.current_task()

        guard = await runtime.commit_running(
            run_id="run_1",
            turn_id="turn_1",
            cursor=RunExecutionCursor("turn_1", "before_provider", 0),
            guard=RunWriteGuard(3, claim_id="claim_1", fencing_token=7),
        )

        assert order == ["capture", "store"]
        assert source.capture_tasks == [current_task]
        assert store.store_tasks == [current_task]
        assert store.checkpoints[0].execution_cursor == RunExecutionCursor(
            "turn_1",
            "before_provider",
            0,
        )
        assert guard == RunWriteGuard(4, claim_id="claim_1", fencing_token=7)

    asyncio.run(scenario())


def test_capture_failure_does_not_call_checkpoint_store() -> None:
    async def scenario() -> None:
        store = RecordingCheckpointStore([])
        runtime = _runtime(FailingCheckpointSource(), store)

        with pytest.raises(RuntimeError, match="capture failed"):
            await runtime.commit_running(
                run_id="run_1",
                turn_id="turn_1",
                cursor=RunExecutionCursor("turn_1", "before_provider", 0),
                guard=RunWriteGuard(3),
            )

        assert store.checkpoints == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field",
    ["session_id", "run_id", "turn_id", "aggregate_version"],
)
def test_checkpoint_store_cannot_return_unrelated_commit_metadata(field: str) -> None:
    async def scenario() -> None:
        session = SessionState("session_1")
        session.new_turn("hello", turn_id="turn_1")
        messages = MessageRuntime()
        messages.append_user("hello", message_id="message_1")
        runtime = _runtime(
            RuntimeCheckpointSource(session, messages, ContextRuntime()),
            InvalidCheckpointStore(field),
        )

        with pytest.raises(RunProtocolError, match="checkpoint store returned"):
            await runtime.commit_running(
                run_id="run_1",
                turn_id="turn_1",
                cursor=RunExecutionCursor("turn_1", "before_provider", 0),
                guard=RunWriteGuard(3),
            )

    from agentos.runtime.errors import RunProtocolError

    asyncio.run(scenario())
