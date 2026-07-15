import asyncio
from dataclasses import dataclass, field
from typing import get_type_hints

import pytest

from agentos.attachments import AttachmentRuntime
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
from agentos.runtime import WaitReason
from agentos.runtime.run import RunRequest, UserTurnInput
from agentos.runtime.query_loop_support import AttachmentRuntimeBoundary
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
from agentos.runtime.waiting import WaitingCommit
from tests.runtime._query_loop_contract_fixtures import make_query_loop


@dataclass(slots=True)
class RecordingLogger:
    entries: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def log(self, event: str, **fields: object) -> None:
        self.entries.append((event, fields))


@dataclass(slots=True)
class NoticeProvider:
    notices: tuple[str, ...]
    calls: int = 0

    def consume_notices(self) -> tuple[str, ...]:
        self.calls += 1
        return self.notices


class RecordingWaitingRuntime:
    def __init__(self, turn: TurnState, order: list[tuple[str, str]]) -> None:
        self.turn = turn
        self.order = order

    async def commit_waiting(
        self,
        *,
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit:
        assert turn_id == self.turn.id
        self.order.append(("commit", self.turn.status))
        return WaitingCommit("run_1", reason)


class FailingWaitingRuntime:
    async def commit_waiting(
        self,
        *,
        turn_id: str,
        reason: WaitReason,
    ) -> WaitingCommit:
        raise RuntimeError("commit failed")


def make_lifecycle(
    *,
    context: ContextRuntime | None = None,
    messages: MessageRuntime | None = None,
    attachments: AttachmentRuntime | None = None,
    notices: NoticeProvider | None = None,
    waiting_runtime: object | None = None,
    event_bus: EventBus | None = None,
    logger: RecordingLogger | None = None,
) -> TurnLifecycle:
    return TurnLifecycle(
        context_runtime=context or ContextRuntime(),
        message_runtime=messages or MessageRuntime(),
        attachment_runtime=attachments,
        session_state=SessionState("session_1"),
        turn_notice_provider=notices,
        waiting_runtime=waiting_runtime,  # type: ignore[arg-type]
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


def test_prepare_user_turn_requires_attachment_runtime_for_attachments() -> None:
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image",
        filename="diagram.png",
        mime_type="image/png",
    )
    lifecycle = make_lifecycle()

    with pytest.raises(
        RuntimeError,
        match="attachment runtime is required for attachments",
    ):
        lifecycle.prepare_user_turn(UserTurnInput("inspect", (attachment,)))


def test_turn_lifecycle_depends_on_attachment_runtime_boundary() -> None:
    annotations = get_type_hints(TurnLifecycle)

    assert annotations["attachment_runtime"] == AttachmentRuntimeBoundary | None


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
    notices = NoticeProvider(("Task task_1 completed.",))
    lifecycle = make_lifecycle(
        context=context,
        messages=messages,
        notices=notices,
        event_bus=event_bus,
    )

    turn, events = lifecycle.prepare_continuation_turn()

    assert turn == TurnState("turn_1", "")
    assert events == (TurnStreamStarted(""),)
    assert messages.store.all() == []
    assert context.snapshot().runtime_notices == ("Task task_1 completed.",)
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


def test_cleanup_clears_continuation_notices_and_loaded_attachments() -> None:
    context = ContextRuntime()
    context.set_runtime_notices(("Task task_1 completed.",))
    attachments = AttachmentRuntime()
    attachment = attachments.upload_bytes(
        b"image",
        filename="diagram.png",
        mime_type="image/png",
    )
    attachments.load_attachment_handle(f"att:{attachment.handle}")
    lifecycle = make_lifecycle(context=context, attachments=attachments)

    lifecycle.cleanup(is_continuation=True)

    assert context.snapshot().runtime_notices == ()
    assert attachments._project_provider_inputs_compat(()) == ()


def test_commit_waiting_commits_before_state_transition_and_event_return() -> None:
    async def run() -> None:
        turn = TurnState("turn_1", "hello")
        order: list[tuple[str, str]] = []
        lifecycle = make_lifecycle(
            waiting_runtime=RecordingWaitingRuntime(turn, order),
        )

        event = await lifecycle.commit_waiting(
            turn=turn,
            reason=WaitReason("human_input", "approval_1"),
        )
        order.append(("event", turn.status))

        assert order == [("commit", "running"), ("event", "waiting")]
        assert event == TurnStreamWaiting(
            "run_1",
            WaitReason("human_input", "approval_1"),
        )

    asyncio.run(run())


def test_commit_waiting_failure_does_not_change_turn_state() -> None:
    async def run() -> None:
        turn = TurnState("turn_1", "hello")
        lifecycle = make_lifecycle(waiting_runtime=FailingWaitingRuntime())

        with pytest.raises(RuntimeError, match="commit failed"):
            await lifecycle.commit_waiting(
                turn=turn,
                reason=WaitReason("human_input", "approval_1"),
            )

        assert turn.status == "running"

    asyncio.run(run())


def test_commit_waiting_requires_waiting_runtime() -> None:
    async def run() -> None:
        lifecycle = make_lifecycle()

        with pytest.raises(
            WaitingUnsupportedError,
            match="waiting runtime is not configured",
        ):
            await lifecycle.commit_waiting(
                turn=TurnState("turn_1", "hello"),
                reason=WaitReason("human_input", "approval_1"),
            )

    asyncio.run(run())
