from __future__ import annotations

from dataclasses import dataclass

from agentos._waiting import WaitReason
from agentos.artifacts import ArtifactRef
from agentos.messages import MessageRuntime
from agentos.runtime.errors import (
    ContinuationUnavailableError,
    RunProtocolError,
    WaitingUnsupportedError,
)
from agentos.runtime.continuation import ContinuationNotice, ContinuationRuntime
from agentos.runtime.event_bus import (
    AgentEvent,
    EventBus,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
    UserMessageAppendedEvent,
)
from agentos.runtime.query_loop_support import (
    ArtifactRuntimeBoundary,
    ContextRuntimeBoundary,
    StructuredLoggerBoundary,
    TurnNoticeProvider,
)
from agentos.runtime.durable_commands import AcceptedContinuationInput
from agentos.runtime.run import LocalContinuationInput, UserTurnInput
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    PlanUpdated,
    StatusUpdate,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamStarted,
    TurnStreamWaiting,
)
from agentos.runtime.turn import TurnState
from agentos.runtime.waiting import WaitingRuntime


@dataclass(slots=True)
class TurnLifecycle:
    """管理单个 Turn 的创建、状态迁移与临时资源清理。"""

    context_runtime: ContextRuntimeBoundary
    message_runtime: MessageRuntime
    artifact_runtime: ArtifactRuntimeBoundary | None = None
    session_state: SessionState | None = None
    turn_notice_provider: TurnNoticeProvider | None = None
    waiting_runtime: WaitingRuntime | None = None
    continuation_runtime: ContinuationRuntime | None = None
    event_bus: EventBus | None = None
    structured_logger: StructuredLoggerBoundary | None = None

    def prepare_user_turn(
        self,
        input: UserTurnInput,
    ) -> tuple[TurnState | None, tuple[TurnStreamEvent, ...]]:
        """创建用户 Turn、追加消息并返回起始流事件。"""

        turn = self._start_turn(input.content)
        self._log("turn_start", user_message_length=len(input.content))
        artifact_refs = self._prepare_user_artifacts(input)
        user = self.message_runtime.append_user(
            input.content,
            artifact_refs=artifact_refs,
        )
        self._emit(
            UserMessageAppendedEvent(
                message_id=user.id,
                **self.event_context(turn),
            ),
        )
        return turn, (
            TurnStreamStarted(input.content),
            StatusUpdate("received", "我已收到请求，先整理上下文再开始执行。"),
            PlanUpdated(
                "先装载上下文和可用能力，再由模型决定是否调用工具或 skill，最后整合结果。",
                "created",
            ),
        )

    def prepare_continuation_turn(
        self,
        input: LocalContinuationInput | AcceptedContinuationInput = LocalContinuationInput(),
    ) -> tuple[TurnState | None, tuple[TurnStreamEvent, ...]]:
        """消费本地 notice 并创建不追加用户消息的 continuation Turn。"""

        notices = ()
        if type(input) is LocalContinuationInput:
            notices = self._consume_turn_notices()
            if not notices:
                raise ContinuationUnavailableError(
                    "local continuation requires a pending runtime notice",
                )
        turn = self._start_turn("", is_continuation=True)
        if self.continuation_runtime is None:
            raise RuntimeError("continuation runtime is not configured")
        if type(input) is AcceptedContinuationInput:
            self.continuation_runtime.set_durable(input)
        else:
            self.continuation_runtime.set_notices(notices)
        return turn, (TurnStreamStarted(""),)

    def complete(
        self,
        turn: TurnState | None,
        content: str,
    ) -> TurnStreamCompleted:
        """完成 Turn 并生成完成事件。"""

        if turn is not None:
            turn.complete()
        self._emit(TurnCompletedEvent(**self.event_context(turn)))
        self._log("turn_end")
        return TurnStreamCompleted(content)

    def fail(
        self,
        turn: TurnState | None,
        error: BaseException,
        *,
        mark_turn: bool = True,
    ) -> TurnStreamFailed:
        """失败仍在运行的 Turn 并保留原始异常对象。"""

        if mark_turn and turn is not None and turn.status == "running":
            turn.fail(str(error))
        self._emit(
            TurnFailedEvent(error=str(error), **self.event_context(turn)),
        )
        return TurnStreamFailed(error)

    def cancel(self, turn: TurnState | None) -> None:
        """取消仍在运行的 Turn。"""

        if turn is not None and turn.status == "running":
            turn.cancel()

    async def commit_waiting(
        self,
        *,
        run_id: str,
        turn: TurnState | None,
        reason: WaitReason,
        expected_version: int,
    ) -> TurnStreamWaiting:
        """先提交权威等待状态，再迁移 Turn 并返回等待事件。"""

        if turn is None:
            raise WaitingUnsupportedError("waiting requires session turn state")
        if self.waiting_runtime is None:
            raise WaitingUnsupportedError("waiting runtime is not configured")
        commit = await self.waiting_runtime.commit_waiting(
            run_id=run_id,
            turn_id=turn.id,
            reason=reason,
            expected_version=expected_version,
        )
        if commit.run_id != run_id:
            raise RunProtocolError("waiting runtime returned another run id")
        turn.mark_waiting()
        return TurnStreamWaiting(commit.run_id, commit.reason)

    def cleanup(self, *, is_continuation: bool) -> None:
        """清理 Turn 级 runtime notice 和附件挂载。"""

        if self.continuation_runtime is not None:
            self.continuation_runtime.clear()
        if self.artifact_runtime is not None:
            self.artifact_runtime.clear_mounts()

    def event_context(self, turn: TurnState | None) -> dict[str, str | None]:
        """返回 lifecycle event 使用的 session 与 turn 标识。"""

        return {
            "session_id": self.session_state.id if self.session_state else None,
            "turn_id": turn.id if turn else None,
        }

    def _prepare_user_artifacts(
        self,
        input: UserTurnInput,
    ) -> tuple[ArtifactRef, ...]:
        if not input.artifact_handles:
            return ()
        if self.artifact_runtime is None:
            raise RuntimeError("artifact runtime is required for artifact handles")
        return self.artifact_runtime.prepare_user_uploads(input.artifact_handles)

    def _consume_turn_notices(self) -> tuple[ContinuationNotice, ...]:
        if self.turn_notice_provider is None:
            return ()
        return self.turn_notice_provider.consume_notices()

    def _start_turn(
        self,
        content: str,
        *,
        is_continuation: bool = False,
    ) -> TurnState | None:
        turn = (
            self.session_state.new_turn(content)
            if self.session_state is not None
            else None
        )
        self._emit(
            TurnStartedEvent(
                user_input=content,
                is_continuation=is_continuation,
                **self.event_context(turn),
            ),
        )
        return turn

    def _emit(self, event: AgentEvent) -> None:
        if self.event_bus is not None:
            self.event_bus.emit(event)

    def _log(self, event: str, **fields: object) -> None:
        if self.structured_logger is not None:
            if self.session_state is not None:
                fields.setdefault("session_id", self.session_state.id)
            self.structured_logger.log(event, **fields)
