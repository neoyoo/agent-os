import asyncio
from dataclasses import dataclass, field
from typing import get_type_hints

import pytest

from agentos.artifacts import ArtifactRuntime, InMemoryArtifactStore
from agentos.context import ContextRuntime
from agentos.events import (
    EventBus,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
    UserMessageAppendedEvent,
)
from agentos.messages import MessageRuntime
from agentos.runtime.errors import (
    ContinuationUnavailableError,
    WaitingUnsupportedError,
)
from agentos.runtime.continuation import ContinuationNotice, ContinuationRuntime
from agentos.runtime import WaitReason
from agentos.runtime.run import RunRequest, UserTurnInput
from agentos.runtime.query_loop_support import ArtifactRuntimeBoundary
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    PlanUpdated,
    StatusUpdate,
    TurnStreamCompleted,
    TurnStreamFailed,
    TurnStreamStarted,
    TurnStreamWaiting,
)
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle
from tests.runtime._query_loop_contract_fixtures import make_query_loop


@dataclass(slots=True)
class RecordingLogger:
    entries: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def log(self, event: str, **fields: object) -> None:
        self.entries.append((event, fields))


@dataclass(slots=True)
class NoticeProvider:
    notices: tuple[ContinuationNotice, ...]
    calls: int = 0

    def consume_notices(self) -> tuple[ContinuationNotice, ...]:
        self.calls += 1
        return self.notices


def make_lifecycle(
    *,
    context: ContextRuntime | None = None,
    messages: MessageRuntime | None = None,
    artifacts: ArtifactRuntime | None = None,
    notices: NoticeProvider | None = None,
    continuation: ContinuationRuntime | None = None,
    event_bus: EventBus | None = None,
    logger: RecordingLogger | None = None,
) -> TurnLifecycle:
    return TurnLifecycle(
        context_runtime=context or ContextRuntime(),
        message_runtime=messages or MessageRuntime(),
        artifact_runtime=artifacts,
        session_state=SessionState("session_1"),
        turn_notice_provider=notices,
        continuation_runtime=continuation or ContinuationRuntime(),
        event_bus=event_bus,
        structured_logger=logger,
    )


def test_prepare_user_turn_appends_message_and_emits_start_events() -> None:
    messages = MessageRuntime()
    event_bus = EventBus()
    logger = RecordingLogger()
    lifecycle = make_lifecycle(
        messages=messages,
        event_bus=event_bus,
        logger=logger,
    )

    turn, events = lifecycle.prepare_user_turn(UserTurnInput("hello"))

    assert turn == TurnState("turn_1", "hello")
    assert [(message.role, message.content) for message in messages.store.all()] == [
        ("user", "hello"),
    ]
    assert events == (
        TurnStreamStarted("hello"),
        StatusUpdate("received", "我已收到请求，先整理上下文再开始执行。"),
        PlanUpdated(
            "先装载上下文和可用能力，再由模型决定是否调用工具或 skill，最后整合结果。",
            "created",
        ),
    )
    assert event_bus.events == [
        TurnStartedEvent(
            session_id="session_1",
            turn_id="turn_1",
            user_input="hello",
        ),
        UserMessageAppendedEvent(
            session_id="session_1",
            turn_id="turn_1",
            message_id="msg_1",
        ),
    ]
    assert logger.entries == [
        ("turn_start", {"user_message_length": 5, "session_id": "session_1"}),
    ]


def test_query_loop_uses_session_state_assigned_before_execute() -> None:
    async def run() -> None:
        loop = make_query_loop()
        loop.session_state = SessionState("session_replaced")

        stream = await loop.execute(RunRequest(UserTurnInput("hello")))
        async with stream:
            _ = [event async for event in stream]

        assert loop.event_bus is not None
        started = next(
            event
            for event in loop.event_bus.events
            if isinstance(event, TurnStartedEvent)
        )
        assert started.session_id == "session_replaced"
        assert started.turn_id == "turn_1"

    asyncio.run(run())


def test_prepare_user_turn_requires_artifact_runtime_for_handles() -> None:
    lifecycle = make_lifecycle()

    with pytest.raises(
        RuntimeError,
        match="artifact runtime is required for artifact handles",
    ):
        lifecycle.prepare_user_turn(
            UserTurnInput(
                "inspect",
                ("art_12345678-1234-4234-9234-123456789abc",),
            )
        )


def test_turn_lifecycle_depends_on_artifact_runtime_boundary() -> None:
    annotations = get_type_hints(TurnLifecycle)

    assert annotations["artifact_runtime"] == ArtifactRuntimeBoundary | None


def test_prepare_continuation_requires_pending_notice() -> None:
    event_bus = EventBus()
    notices = NoticeProvider(())
    lifecycle = make_lifecycle(notices=notices, event_bus=event_bus)

    with pytest.raises(
        ContinuationUnavailableError,
        match="local continuation requires a pending runtime notice",
    ):
        lifecycle.prepare_continuation_turn()

    assert notices.calls == 1
    assert event_bus.events == []


def test_prepare_continuation_sets_notice_without_user_message() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    event_bus = EventBus()
    notice = ContinuationNotice("task_completed", "task_1", "check_agent_tasks")
    notices = NoticeProvider((notice,))
    continuation = ContinuationRuntime()
    lifecycle = make_lifecycle(
        context=context,
        messages=messages,
        notices=notices,
        continuation=continuation,
        event_bus=event_bus,
    )

    turn, events = lifecycle.prepare_continuation_turn()

    assert turn == TurnState("turn_1", "")
    assert events == (TurnStreamStarted(""),)
    assert messages.store.all() == []
    assert continuation.inputs()[0].kind == "continuation_data"
    assert "task_1" in continuation.inputs()[0].content[0].text  # type: ignore[union-attr]
    assert event_bus.events == [
        TurnStartedEvent(
            session_id="session_1",
            turn_id="turn_1",
            user_input="",
            is_continuation=True,
        ),
    ]


def test_complete_fail_and_cancel_apply_terminal_transitions() -> None:
    event_bus = EventBus()
    logger = RecordingLogger()
    lifecycle = make_lifecycle(event_bus=event_bus, logger=logger)
    completed = TurnState("turn_completed", "hello")
    failed = TurnState("turn_failed", "hello")
    cancelled = TurnState("turn_cancelled", "hello")
    error = RuntimeError("provider failed")

    completed_event = lifecycle.complete(completed, "answer")
    failed_event = lifecycle.fail(failed, error)
    lifecycle.cancel(cancelled)

    assert completed.status == "completed"
    assert completed_event == TurnStreamCompleted("answer")
    assert failed.status == "failed"
    assert failed.error == "provider failed"
    assert failed_event == TurnStreamFailed(error)
    assert failed_event.error is error
    assert cancelled.status == "cancelled"
    assert event_bus.events == [
        TurnCompletedEvent(session_id="session_1", turn_id="turn_completed"),
        TurnFailedEvent(
            session_id="session_1",
            turn_id="turn_failed",
            error="provider failed",
        ),
    ]
    assert logger.entries == [("turn_end", {"session_id": "session_1"})]


def test_structured_logs_add_current_session_without_overwriting_caller() -> None:
    lifecycle_logger = RecordingLogger()
    lifecycle = make_lifecycle(logger=lifecycle_logger)
    lifecycle._log("turn_start")
    lifecycle._log("turn_end", session_id="caller_session")

    loop_logger = RecordingLogger()
    loop = make_query_loop()
    loop.structured_logger = loop_logger
    loop._log("provider_call")
    loop._log("tool_exec", session_id="caller_session")

    assert lifecycle_logger.entries == [
        ("turn_start", {"session_id": "session_1"}),
        ("turn_end", {"session_id": "caller_session"}),
    ]
    assert loop_logger.entries == [
        ("provider_call", {"session_id": "session_1"}),
        ("tool_exec", {"session_id": "caller_session"}),
    ]


def test_cleanup_clears_continuation_data_and_artifact_mounts() -> None:
    continuation = ContinuationRuntime()
    continuation.set_notices(
        (ContinuationNotice("task_completed", "task_1", "check_agent_tasks"),)
    )
    artifacts = ArtifactRuntime(
        session_id="session_1",
        store=InMemoryArtifactStore(),
    )
    artifact = artifacts.upload(
        data=b"image",
        filename="diagram.png",
        media_type="image/png",
    )
    artifacts.load_attachment(artifact.id)
    lifecycle = make_lifecycle(
        artifacts=artifacts,
        continuation=continuation,
    )

    lifecycle.cleanup(is_continuation=True)

    assert continuation.inputs() == ()
    assert artifacts.active_mounts() == ()


def test_mark_waiting_transitions_turn_after_authoritative_commit() -> None:
    turn = TurnState("turn_1", "hello")
    lifecycle = make_lifecycle()

    event = lifecycle.mark_waiting(
        run_id="run_1",
        turn=turn,
        reason=WaitReason("human_input", "approval_1"),
    )

    assert turn.status == "waiting"
    assert event == TurnStreamWaiting(
        "run_1",
        WaitReason("human_input", "approval_1"),
    )


def test_mark_waiting_requires_turn_state() -> None:
    lifecycle = make_lifecycle()

    with pytest.raises(
        WaitingUnsupportedError,
        match="waiting requires session turn state",
    ):
        lifecycle.mark_waiting(
            run_id="run_1",
            turn=None,
            reason=WaitReason("human_input", "approval_1"),
        )
