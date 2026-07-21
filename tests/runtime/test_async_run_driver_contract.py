import asyncio
import inspect
from dataclasses import replace

import pytest

from agentos import Agent
from agentos._waiting import WaitRequest, WaitReason
from agentos.artifacts import ArtifactRef
from agentos.context import ContextRuntime
from agentos.events import EventBus, TurnStartedEvent, UserMessageAppendedEvent
from agentos.messages import MessageRuntime, StoredMessage
from agentos.providers import ProviderResponse
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.errors import RunProtocolError
from agentos.runtime.continuation import ContinuationRuntime
from agentos.runtime.execution import (
    AcceptedStartInput,
    AcceptedTurnExecution,
    ApplyAcceptedInput,
    RestoreAcceptedTurn,
)
from agentos.runtime.run import RunRequest
from agentos.runtime.run import UserTurnInput
from agentos.runtime.run_commit import RunCommitRuntime
from agentos.runtime.run_driver import RunDriver
from agentos.runtime._execution_control import RunningCheckpointRequest
from agentos.runtime.execution import RunExecutionCursor
from agentos.runtime.query_loop_support import _FinalContent
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime, RunWriteGuard
from agentos.runtime.run_state import (
    RunAlreadyExistsError,
    RunState,
    RunStateTransitionError,
    RunStatus,
)
from agentos.runtime.session import SessionState
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.stream_events import (
    FinalResult,
    TurnStreamCompleted,
    TurnStreamFailed,
)
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle
from agentos.runtime.waiting import LocalWaitingRuntime
from tests.runtime.test_async_run_store_contract import SuspendingRunStore
from tests.runtime._query_loop_contract_fixtures import make_query_loop


def _driver(runs: RunRuntime) -> RunDriver:
    side_effects = InMemorySideEffectStore()
    return RunDriver(
        runs=runs,
        turns=object(),  # type: ignore[arg-type]
        commits=RunCommitRuntime(
            runs,
            LocalWaitingRuntime(runs, side_effects),
        ),
    )


class _RecordingSessionState(SessionState):
    def __init__(self) -> None:
        super().__init__("session_1")
        self.turns: list[TurnState] = []

    def new_turn(
        self,
        user_input: str,
        *,
        turn_id: str | None = None,
    ) -> TurnState:
        turn = super().new_turn(user_input, turn_id=turn_id)
        self.turns.append(turn)
        return turn


def _recording_driver(
    runs: RunRuntime,
) -> tuple[RunDriver, _RecordingSessionState, MessageRuntime]:
    session = _RecordingSessionState()
    messages = MessageRuntime()
    turns = TurnLifecycle(
        context_runtime=ContextRuntime(),
        message_runtime=messages,
        session_state=session,
        continuation_runtime=ContinuationRuntime(),
    )
    side_effects = InMemorySideEffectStore()
    return (
        RunDriver(
            runs=runs,
            turns=turns,
            commits=RunCommitRuntime(
                runs,
                LocalWaitingRuntime(runs, side_effects),
            ),
        ),
        session,
        messages,
    )


class _PostCommitSuspendingStore:
    def __init__(self, blocked_status: RunStatus | None) -> None:
        self.blocked_status = blocked_status
        self.committed = asyncio.Event()
        self.release = asyncio.Event()
        self.state: RunState | None = None
        self.transitions: list[tuple[RunStatus, RunWriteGuard]] = []

    async def create(self, state: RunState) -> RunState:
        self.state = state
        await self._suspend_if_blocked(state.status)
        return state

    async def get(self, *, session_id: str, run_id: str) -> RunState | None:
        return self.state

    async def transition(
        self,
        *,
        session_id: str,
        run_id: str,
        status: RunStatus,
        wait_reason=None,  # type: ignore[no-untyped-def]
        guard: RunWriteGuard,
        turn_id: str | None = None,
    ) -> RunState:
        assert self.state is not None
        if self.state.aggregate_version != guard.expected_version:
            raise RunStateTransitionError("run aggregate version conflict")
        self.state = self.state.transition(status, wait_reason=wait_reason)
        self.transitions.append((status, guard))
        await self._suspend_if_blocked(status)
        return self.state

    async def _suspend_if_blocked(self, status: RunStatus) -> None:
        if status is not self.blocked_status:
            return
        self.committed.set()
        await self.release.wait()


def test_run_driver_state_entrypoints_are_native_async() -> None:
    assert inspect.iscoroutinefunction(RunDriver.prepare)
    assert inspect.iscoroutinefunction(RunDriver.cancel_open)


def test_run_driver_prepare_awaits_the_run_store() -> None:
    async def run() -> None:
        store = SuspendingRunStore()
        driver = _driver(
            RunRuntime(
                session_id="session_1", store=store, id_factory=lambda: "run_1"
            ),
        )
        task = asyncio.create_task(driver.prepare(UserTurnInput("hello")))

        await store.create_started.wait()
        assert not task.done()
        store.release_create.set()

        assert await task == "run_1"

    asyncio.run(run())


def test_prepare_id_collision_does_not_cancel_the_existing_run() -> None:
    async def run() -> None:
        runs = RunRuntime(
            session_id="session_1",
            store=InMemoryRunStore(),
            id_factory=lambda: "run_1",
        )
        existing = await runs.create_run()
        driver = _driver(runs)

        with pytest.raises(RunAlreadyExistsError, match="already exists"):
            await driver.prepare(UserTurnInput("hello"))

        assert await runs.get_run("run_1") == existing

    asyncio.run(run())


@pytest.mark.parametrize("blocked_status", [RunStatus.CREATED, RunStatus.QUEUED])
def test_prepare_cancellation_closes_a_run_committed_before_await_returns(
    blocked_status: RunStatus,
) -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(blocked_status)
        driver = _driver(
            RunRuntime(
                session_id="session_1", store=store, id_factory=lambda: "run_1"
            ),
        )
        task = asyncio.create_task(driver.prepare(UserTurnInput("hello")))

        await store.committed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert store.state is not None
        assert store.state.status is RunStatus.CANCELLED

    asyncio.run(run())


def test_start_cancellation_refreshes_guard_and_closes_committed_run() -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(RunStatus.RUNNING)
        loop = make_query_loop(
            run_runtime=RunRuntime(
                session_id="session_1",
                store=store,
                id_factory=lambda: "run_1",
            ),
        )
        stream = await loop.execute(RunRequest(UserTurnInput("hello")))
        consumer = asyncio.create_task(anext(stream))

        await store.committed.wait()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        assert store.state is not None
        assert store.state.status is RunStatus.CANCELLED
        assert stream.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    ("initial_status", "expected_transitions"),
    [
        (RunStatus.QUEUED, [RunStatus.RUNNING, RunStatus.COMPLETED]),
        (RunStatus.RUNNING, [RunStatus.COMPLETED]),
    ],
)
def test_accepted_execution_preserves_fence_across_run_entry_states(
    initial_status: RunStatus,
    expected_transitions: list[RunStatus],
) -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=initial_status,
            aggregate_version=4,
        )
        loop = make_query_loop(
            run_runtime=RunRuntime(session_id="session_1", store=store),
        )
        accepted = AcceptedContinuationInput(
            "run_1",
            "command_1",
            "resume",
            {},
            "turn_stable",
        )
        execution = AcceptedTurnExecution(
            input=accepted,
            guard=RunWriteGuard(4, claim_id="claim_1", fencing_token=7),
            preparation=(
                ApplyAcceptedInput()
                if initial_status is RunStatus.QUEUED
                else RestoreAcceptedTurn(
                    RunExecutionCursor("turn_stable", "before_provider", 0),
                )
            ),
        )

        stream = await loop.execute(RunRequest(execution))  # type: ignore[arg-type]
        async with stream:
            _ = [event async for event in stream]

        assert [status for status, _guard in store.transitions] == (
            expected_transitions
        )
        assert all(
            guard.claim_id == "claim_1" and guard.fencing_token == 7
            for _status, guard in store.transitions
        )

    asyncio.run(run())


def test_agent_executes_accepted_start_with_the_claim_guard() -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=RunStatus.QUEUED,
            aggregate_version=3,
        )
        loop = make_query_loop(
            run_runtime=RunRuntime(session_id="session_1", store=store),
        )
        execution = AcceptedTurnExecution(
            input=AcceptedStartInput(
                run_id="run_1",
                submission_id="submission_1",
                input=UserTurnInput("hello"),
                turn_id="turn_stable",
                user_message_id="message_stable",
            ),
            guard=RunWriteGuard(3, claim_id="claim_1", fencing_token=11),
            preparation=ApplyAcceptedInput(),
        )

        result = await Agent(loop).run(execution)

        assert result.content == "answer"
        assert loop.message_runtime.store.get("message_stable").content == "hello"
        assert loop.event_bus is not None
        started = next(
            event
            for event in loop.event_bus.events
            if isinstance(event, TurnStartedEvent)
        )
        assert started.turn_id == "turn_stable"
        assert [status for status, _guard in store.transitions] == [
            RunStatus.RUNNING,
            RunStatus.COMPLETED,
        ]
        assert all(
            guard.claim_id == "claim_1" and guard.fencing_token == 11
            for _status, guard in store.transitions
        )

    asyncio.run(run())


def test_consumer_protocol_failure_reenters_authoritative_run_driver() -> None:
    async def run() -> None:
        store = InMemoryRunStore()
        runs = RunRuntime(
            session_id="session_1",
            store=store,
            id_factory=lambda: "run_1",
        )
        loop = make_query_loop(run_runtime=runs)
        stream = await loop.execute(RunRequest(UserTurnInput("hello")))
        await anext(stream)
        error = RunProtocolError("run event exceeds protocol size limit")

        failed = await stream._fail_active(error)

        assert type(failed) is TurnStreamFailed
        assert failed.error is error
        assert (await runs.get_run("run_1")).status is RunStatus.FAILED
        assert stream.closed

    asyncio.run(run())


def test_consumer_failure_cannot_override_an_external_terminal_commit() -> None:
    async def run() -> None:
        store = InMemoryRunStore()
        runs = RunRuntime(
            session_id="session_1",
            store=store,
            id_factory=lambda: "run_1",
        )
        loop = make_query_loop(run_runtime=runs)
        stream = await loop.execute(RunRequest(UserTurnInput("hello")))
        await anext(stream)
        current = await runs.get_run("run_1")
        await runs.cancel(
            "run_1",
            guard=RunWriteGuard(current.aggregate_version),
            turn_id=None,
        )
        error = RunProtocolError("run event exceeds protocol size limit")

        with pytest.raises(RunProtocolError) as caught:
            await stream._fail_active(error)

        assert caught.value is error
        assert (await runs.get_run("run_1")).status is RunStatus.CANCELLED
        assert stream.closed

    asyncio.run(run())


def test_accepted_start_recovery_is_idempotent_after_checkpoint_hydration() -> None:
    async def run() -> None:
        artifact_id = "art_12345678-1234-4234-9234-123456789abc"
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=RunStatus.RUNNING,
            aggregate_version=4,
        )
        session = SessionState.from_snapshot("session_1", "running", 2)
        messages = MessageRuntime()
        artifact_ref = ArtifactRef(artifact_id, "drawing.png", "image/png")
        user = StoredMessage(
            "message_stable",
            "user",
            "hello",
            artifact_refs=(artifact_ref,),
        )
        messages.hydrate_messages([user])
        event_bus = EventBus()

        class ArtifactSpy:
            prepare_calls = 0
            restore_calls = 0

            async def prepare_user_uploads(self, handles):  # type: ignore[no-untyped-def]
                self.prepare_calls += 1
                return ()

            async def restore_user_uploads(self, refs):  # type: ignore[no-untyped-def]
                self.restore_calls += 1
                assert refs == (artifact_ref,)

            def clear_mounts(self) -> None:
                return None

        artifacts = ArtifactSpy()
        runs = RunRuntime(session_id="session_1", store=store)
        turns = TurnLifecycle(
            context_runtime=ContextRuntime(),
            message_runtime=messages,
            artifact_runtime=artifacts,  # type: ignore[arg-type]
            session_state=session,
            continuation_runtime=ContinuationRuntime(),
            event_bus=event_bus,
        )
        side_effects = InMemorySideEffectStore()
        driver = RunDriver(
            runs=runs,
            turns=turns,
            commits=RunCommitRuntime(
                runs,
                LocalWaitingRuntime(runs, side_effects),
            ),
        )
        cursor = RunExecutionCursor("turn_1", "before_provider", 0)
        execution = AcceptedTurnExecution(
            input=AcceptedStartInput(
                run_id="run_1",
                submission_id="submission_1",
                input=UserTurnInput("hello", artifact_handles=(artifact_id,)),
                turn_id="turn_1",
                user_message_id=user.id,
            ),
            guard=RunWriteGuard(4, claim_id="claim_1", fencing_token=7),
            preparation=RestoreAcceptedTurn(cursor),
        )
        request = RunRequest(execution)
        run_id = await driver.prepare(request.input)

        async def provider_events(  # type: ignore[no-untyped-def]
            _run_id,
            turn,
            _options,
            preparation,
            _execution_guard,
        ):
            assert turn is not None
            assert turn.id == "turn_1"
            assert preparation == RestoreAcceptedTurn(cursor)
            yield FinalResult("done")
            yield _FinalContent("done")

        events = [
            event
            async for event in driver.events(
                request,
                run_id,
                provider_events,
            )
        ]

        assert any(isinstance(event, TurnStreamCompleted) for event in events)
        assert messages.store.all() == [user]
        assert messages.active_window.refs == ()
        assert session.next_turn_number() == 2
        assert artifacts.prepare_calls == 0
        assert artifacts.restore_calls == 1
        assert not any(
            isinstance(event, (TurnStartedEvent, UserMessageAppendedEvent))
            for event in event_bus.events
        )

    asyncio.run(run())


def test_side_effect_resume_starts_new_turn_with_old_source_cursor() -> None:
    from tests.runtime.test_side_effect_resolution_contract import (
        _accepted_resolution,
        _fenced_resume,
    )

    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=RunStatus.QUEUED,
            aggregate_version=4,
        )
        session = SessionState.from_snapshot("session_1", "running", 2)
        event_bus = EventBus()
        continuation = ContinuationRuntime()
        runs = RunRuntime(session_id="session_1", store=store)
        side_effects = InMemorySideEffectStore()
        driver = RunDriver(
            runs=runs,
            turns=TurnLifecycle(
                context_runtime=ContextRuntime(),
                message_runtime=MessageRuntime(),
                session_state=session,
                continuation_runtime=continuation,
                event_bus=event_bus,
            ),
            commits=RunCommitRuntime(
                runs,
                LocalWaitingRuntime(runs, side_effects),
            ),
        )
        resume = _fenced_resume()
        execution = AcceptedTurnExecution(
            _accepted_resolution(),
            RunWriteGuard(4, "claim_1", 7),
            resume,
        )
        request = RunRequest(execution)
        run_id = await driver.prepare(request.input)

        async def provider_events(  # type: ignore[no-untyped-def]
            _run_id,
            turn,
            _options,
            preparation,
            _execution_guard,
        ):
            assert turn == TurnState(resume.continuation_turn_id, "")
            assert preparation is resume
            assert preparation.source_cursor.turn_id != turn.id
            yield FinalResult("done")
            yield _FinalContent("done")

        _ = [
            event
            async for event in driver.events(request, run_id, provider_events)
        ]

        assert continuation.inputs() == ()
        assert event_bus.events[0] == TurnStartedEvent(
            session_id="session_1",
            turn_id=resume.continuation_turn_id,
            user_input="",
            is_continuation=True,
        )

    asyncio.run(run())


def test_query_loop_rejects_side_effect_resume_without_validator_before_writes() -> None:
    from tests.runtime.test_side_effect_resolution_contract import (
        _accepted_resolution,
        _fenced_resume,
    )

    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=RunStatus.QUEUED,
            aggregate_version=4,
        )
        loop = make_query_loop(
            run_runtime=RunRuntime(session_id="session_1", store=store),
        )
        execution = AcceptedTurnExecution(
            _accepted_resolution(),
            RunWriteGuard(4, "claim_1", 7),
            _fenced_resume(),
        )

        with pytest.raises(RunProtocolError, match="resume validator"):
            await loop.execute(RunRequest(execution))

        assert store.transitions == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "status",
    [RunStatus.QUEUED, RunStatus.RUNNING],
)
def test_closing_unconsumed_fenced_stream_leaves_claimed_run_unchanged(
    status: RunStatus,
) -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=status,
            aggregate_version=4,
        )
        loop = make_query_loop(
            run_runtime=RunRuntime(session_id="session_1", store=store),
        )
        execution = AcceptedTurnExecution(
            input=AcceptedContinuationInput(
                "run_1",
                "command_1",
                "resume",
                {},
                "turn_stable",
            ),
            guard=RunWriteGuard(4, claim_id="claim_1", fencing_token=7),
            preparation=(
                ApplyAcceptedInput()
                if status is RunStatus.QUEUED
                else RestoreAcceptedTurn(
                    RunExecutionCursor("turn_stable", "before_provider", 0),
                )
            ),
        )

        stream = await loop.execute(RunRequest(execution))
        await stream.aclose()

        assert store.state is not None
        assert store.state.status is status
        assert store.transitions == []

    asyncio.run(run())


def test_closing_active_fenced_stream_has_idempotent_cleanup(monkeypatch) -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        store.state = RunState(
            "run_1",
            "session_1",
            status=RunStatus.QUEUED,
            aggregate_version=4,
        )
        loop = make_query_loop(
            run_runtime=RunRuntime(session_id="session_1", store=store),
        )
        provider_entered = asyncio.Event()
        release_provider = asyncio.Event()

        class BlockingProvider:
            async def async_complete(self, request):  # type: ignore[no-untyped-def]
                provider_entered.set()
                await release_provider.wait()
                return ProviderResponse("answer")

        loop.provider = BlockingProvider()
        cleanup_errors: list[BaseException] = []
        original_cleanup = RunDriver.cleanup_open

        async def record_cleanup(self: RunDriver, run_id: str) -> None:
            try:
                await original_cleanup(self, run_id)
            except BaseException as error:
                cleanup_errors.append(error)
                raise

        monkeypatch.setattr(RunDriver, "cleanup_open", record_cleanup)
        execution = AcceptedTurnExecution(
            input=AcceptedStartInput(
                run_id="run_1",
                submission_id="submission_1",
                input=UserTurnInput("hello"),
                turn_id="turn_stable",
                user_message_id="message_stable",
            ),
            guard=RunWriteGuard(4, claim_id="claim_1", fencing_token=7),
            preparation=ApplyAcceptedInput(),
        )
        stream = await loop.execute(RunRequest(execution))

        async def consume() -> None:
            _ = [event async for event in stream]

        consumer = asyncio.create_task(consume())
        try:
            await provider_entered.wait()
            await stream.aclose()
            with pytest.raises(asyncio.CancelledError):
                await consumer
        finally:
            release_provider.set()
            if not consumer.done():
                consumer.cancel()

        assert store.state is not None
        assert store.state.status is RunStatus.RUNNING
        assert all(
            status is not RunStatus.CANCELLED for status, _guard in store.transitions
        )
        assert cleanup_errors == []

    asyncio.run(run())


@pytest.mark.parametrize(
    ("blocked_status", "expected_turn_status"),
    [
        (RunStatus.WAITING, "waiting"),
        (RunStatus.COMPLETED, "completed"),
    ],
)
def test_cancellation_after_authoritative_commit_preserves_committed_outcome(
    blocked_status: RunStatus,
    expected_turn_status: str,
) -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(blocked_status)
        driver, session, _messages = _recording_driver(
            RunRuntime(
                session_id="session_1",
                store=store,
                id_factory=lambda: "run_1",
            ),
        )
        request = RunRequest(UserTurnInput("hello"))
        run_id = await driver.prepare(request.input)

        async def provider_events(  # type: ignore[no-untyped-def]
            run_id,
            turn,
            options,
            recovery_cursor,
            _execution_guard,
        ):
            if blocked_status is RunStatus.WAITING:
                yield WaitRequest(WaitReason("human_input", "approval_1"))
            return
            yield  # pragma: no cover

        async def consume() -> list[object]:
            return [
                event
                async for event in driver.events(
                    request,
                    run_id,
                    provider_events,
                )
            ]

        consumer = asyncio.create_task(consume())
        await store.committed.wait()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        assert store.state is not None
        assert store.state.status is blocked_status
        assert session.turns[0].status == expected_turn_status
        assert sum(
            status is blocked_status for status, _guard in store.transitions
        ) == 1

    asyncio.run(run())


def test_provider_failure_uses_original_guard_after_external_version_advance() -> None:
    async def run() -> None:
        store = _PostCommitSuspendingStore(None)
        driver, _session, _messages = _recording_driver(
            RunRuntime(
                session_id="session_1",
                store=store,
                id_factory=lambda: "run_1",
            ),
        )
        request = RunRequest(UserTurnInput("hello"))
        run_id = await driver.prepare(request.input)

        async def provider_events(  # type: ignore[no-untyped-def]
            run_id,
            turn,
            options,
            recovery_cursor,
            _execution_guard,
        ):
            assert store.state is not None
            store.state = replace(
                store.state,
                aggregate_version=store.state.aggregate_version + 1,
            )
            raise RuntimeError("provider failed")
            yield  # pragma: no cover

        with pytest.raises(RunStateTransitionError, match="version conflict"):
            _ = [
                event
                async for event in driver.events(
                    request,
                    run_id,
                    provider_events,
                )
            ]

        assert store.state is not None
        assert store.state.status is RunStatus.RUNNING
        assert all(
            status is not RunStatus.FAILED for status, _guard in store.transitions
        )

    asyncio.run(run())


@pytest.mark.parametrize("failure_boundary", ["checkpoint", "terminal"])
def test_commit_failure_does_not_publish_failure_or_terminal_events(
    failure_boundary: str,
    monkeypatch,
) -> None:
    async def run() -> None:
        runs = RunRuntime(
            session_id="session_1",
            store=InMemoryRunStore(),
            id_factory=lambda: "run_1",
        )
        driver, session, _messages = _recording_driver(runs)
        request = RunRequest(UserTurnInput("hello"))
        run_id = await driver.prepare(request.input)
        original_commit_running = RunCommitRuntime.commit_running
        running_commits = 0

        async def fail_checkpoint(self, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal running_commits
            running_commits += 1
            if running_commits == 2:
                raise RuntimeError("checkpoint commit failed")
            return await original_commit_running(self, **kwargs)

        async def fail_terminal(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("terminal commit failed")

        if failure_boundary == "checkpoint":
            monkeypatch.setattr(RunCommitRuntime, "commit_running", fail_checkpoint)
        else:
            monkeypatch.setattr(RunCommitRuntime, "commit_terminal", fail_terminal)

        async def provider_events(  # type: ignore[no-untyped-def]
            _run_id,
            turn,
            _options,
            _recovery_cursor,
            _execution_guard,
        ):
            if failure_boundary == "checkpoint":
                assert turn is not None
                yield RunningCheckpointRequest(
                    RunExecutionCursor(
                        turn.id,
                        "after_tools",
                        0,
                        "assistant_1",
                    ),
                )
            else:
                yield FinalResult("done")
                yield _FinalContent("done")

        published = []
        with pytest.raises(RuntimeError, match="commit failed"):
            async for event in driver.events(
                request,
                run_id,
                provider_events,
            ):
                published.append(event)

        assert not any(
            isinstance(event, (FinalResult, TurnStreamCompleted, TurnStreamFailed))
            for event in published
        )
        assert (await runs.get_run(run_id)).status is RunStatus.RUNNING
        assert session.turns[0].status == "running"

    asyncio.run(run())
