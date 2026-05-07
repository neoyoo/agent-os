from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from agentos.compression import CompressionRuntime
from agentos.context import ContextState
from agentos.messages import MessageRuntime, ToolCall
from agentos.providers import (
    Provider,
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCancelled,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamFailed,
    ProviderStreamOptions,
    ProviderThinkingDelta,
    complete_response_to_stream_events,
)
from agentos.runtime.event_bus import (
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderRequestBuiltEvent,
    ProviderResponseReceivedEvent,
    AgentEvent,
    ToolCallRequestedEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultAppendedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
    UserMessageAppendedEvent,
)
from agentos.runtime.provider_request_builder import ProviderRequestBuilder
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    RunOptions,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamCompleted,
    TurnStreamEvent,
    TurnStreamFailed,
    TurnStreamStarted,
)
from agentos.runtime.turn import TurnState


class ContextRuntimeBoundary(Protocol):
    """QueryLoop 依赖的 context runtime 边界。"""

    def snapshot(self) -> ContextState:
        """返回可渲染的 context snapshot。"""

    def set_runtime_notices(self, notices: tuple[str, ...]) -> None:
        """设置本轮 provider request 可见的一次性 runtime notice。"""

    def clear_runtime_notices(self) -> None:
        """清空一次性 runtime notice。"""


class TurnNoticeProvider(Protocol):
    """QueryLoop 依赖的一次性 turn notice 边界。"""

    def consume_notices(self) -> tuple[str, ...]:
        """返回并消费本轮 runtime notices。"""


class ToolCallRouterBoundary(Protocol):
    """QueryLoop 依赖的 tool call router 边界。"""

    def execute_tool_call(self, tool_call: object) -> object:
        """执行 provider tool call。"""


@dataclass(slots=True)
class QueryLoop:
    """最小 agent turn 调度器。"""

    context_runtime: ContextRuntimeBoundary
    message_runtime: MessageRuntime
    request_builder: ProviderRequestBuilder
    provider: Provider
    compression_runtime: CompressionRuntime | None = None
    tool_call_router: ToolCallRouterBoundary | None = None
    event_bus: EventBus | None = None
    session_state: SessionState | None = None
    turn_notice_provider: TurnNoticeProvider | None = None
    max_tool_iterations: int = 8
    _provider_stream_counter: int = field(default=0, init=False, repr=False)
    _interrupted: bool = field(default=False, init=False, repr=False)

    @property
    def interrupted(self) -> bool:
        """判断当前 loop 是否已收到中断请求。"""

        return self._interrupted

    def request_interrupt(self) -> None:
        """请求在下一个安全点中断运行。"""

        self._interrupted = True

    def clear_interrupt(self) -> None:
        """清除中断请求。"""

        self._interrupted = False

    def build_request(self) -> ProviderRequest:
        """构建下一次 provider request，并在请求前执行窗口压缩。"""

        if self.compression_runtime is not None:
            self.compression_runtime.maybe_compress()
        try:
            return self.request_builder.build(self.context_runtime)
        finally:
            self._clear_runtime_notices()

    def run_turn(self, user_message: str) -> str:
        """运行一轮 user -> provider -> assistant。"""

        final_content = ""
        for event in self.run_turn_stream(user_message):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return final_content

    def run_turn_stream(
        self,
        user_message: str,
        options: RunOptions | None = None,
    ) -> Iterator[TurnStreamEvent]:
        """运行一轮 user -> provider -> assistant，并产出 typed stream events。"""

        run_options = options or RunOptions()
        self._raise_if_interrupted()
        turn = self._start_turn(user_message)
        user = self.message_runtime.append_user(user_message)
        self._emit(
            UserMessageAppendedEvent(
                message_id=user.id,
                **self._event_context(turn),
            ),
        )
        yield TurnStreamStarted(user_message=user_message)

        try:
            response_content = yield from self._run_provider_loop_stream(
                turn,
                run_options,
            )
        except Exception as error:
            if turn is not None:
                turn.fail(str(error))
            self._emit(
                TurnFailedEvent(
                    error=str(error),
                    **self._event_context(turn),
                ),
            )
            yield TurnStreamFailed(error=error)
            raise

        if turn is not None:
            turn.complete()
        self._emit(TurnCompletedEvent(**self._event_context(turn)))
        yield TurnStreamCompleted(content=response_content)

    def run_continuation_stream(
        self,
        options: RunOptions | None = None,
    ) -> Iterator[TurnStreamEvent]:
        """运行 runtime continuation turn，不追加 user 消息。"""

        run_options = options or RunOptions()
        self._raise_if_interrupted()
        notices = self._consume_turn_notices()
        if not notices:
            return
        turn = self._start_turn("", is_continuation=True)
        self._set_runtime_notices(notices)
        try:
            yield TurnStreamStarted(user_message="")

            try:
                response_content = yield from self._run_provider_loop_stream(
                    turn,
                    run_options,
                )
            except Exception as error:
                if turn is not None:
                    turn.fail(str(error))
                self._emit(
                    TurnFailedEvent(
                        error=str(error),
                        **self._event_context(turn),
                    ),
                )
                yield TurnStreamFailed(error=error)
                raise

            if turn is not None:
                turn.complete()
            self._emit(TurnCompletedEvent(**self._event_context(turn)))
            yield TurnStreamCompleted(content=response_content)
        finally:
            self._clear_runtime_notices()

    def _clear_runtime_notices(self) -> None:
        """清空 context runtime 中可能残留的一次性 runtime notice。"""

        clear_runtime_notices = getattr(
            self.context_runtime,
            "clear_runtime_notices",
            None,
        )
        if callable(clear_runtime_notices):
            clear_runtime_notices()

    def _run_provider_loop_stream(
        self,
        turn: TurnState | None,
        options: RunOptions,
    ) -> Iterator[TurnStreamEvent]:
        """执行 provider streaming loop，直到返回 final assistant response。"""

        iterations = 0
        while True:
            self._raise_if_interrupted()
            request = self.build_request()
            self._emit(ProviderRequestBuiltEvent(**self._event_context(turn)))
            response = yield from self._consume_provider_stream(request, options)
            self._emit(ProviderResponseReceivedEvent(**self._event_context(turn)))
            self._ensure_provider_response_usable(response)
            tool_calls = [
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=dict(tool_call.arguments),
                )
                for tool_call in response.tool_calls
            ]
            assistant = self.message_runtime.append_assistant(
                response.content,
                tool_calls=tool_calls,
            )
            self._emit(
                AssistantMessageAppendedEvent(
                    message_id=assistant.id,
                    **self._event_context(turn),
                ),
            )
            yield AssistantCompleted(response=response)

            if not response.tool_calls:
                return response.content
            if self.tool_call_router is None:
                raise RuntimeError("tool call router is required for tool calls")
            iterations += 1
            if iterations > self.max_tool_iterations:
                raise RuntimeError("provider tool-call loop exceeded max iterations")
            if turn is not None:
                turn.increment_tool_iteration()

            appended_message_ids: list[str] = [assistant.id]
            for tool_call in response.tool_calls:
                self._raise_if_interrupted()
                yield ToolStreamStarted(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
                self._emit(
                    ToolCallRequestedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self._event_context(turn),
                    ),
                )
                self._emit(
                    ToolExecutionStartedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self._event_context(turn),
                    ),
                )
                try:
                    result = self.tool_call_router.execute_tool_call(tool_call)
                except Exception as error:
                    self.message_runtime.active_window.remove_refs(
                        appended_message_ids,
                        self.message_runtime.store,
                    )
                    yield ToolStreamFailed(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        error=error,
                    )
                    raise
                self._emit(
                    ToolExecutionCompletedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self._event_context(turn),
                    ),
                )
                tool_result = self.message_runtime.append_tool_result(
                    tool_call_id=result.tool_call_id,
                    content=result.content,
                )
                appended_message_ids.append(tool_result.id)
                self._emit(
                    ToolResultAppendedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        message_id=tool_result.id,
                        **self._event_context(turn),
                    ),
                )
                yield ToolStreamCompleted(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    content=result.content,
                )

    def _consume_provider_stream(
        self,
        request: ProviderRequest,
        options: RunOptions,
    ) -> Iterator[TurnStreamEvent]:
        """消费 provider stream 并返回最终 ProviderResponse。"""

        provider_options = ProviderStreamOptions(
            thinking=options.thinking,
            show_thinking=options.show_thinking,
        )
        response: ProviderResponse | None = None

        for event in self._provider_stream_events(request, provider_options):
            if isinstance(event, ProviderContentDelta):
                yield AssistantContentDelta(index=event.index, text=event.text)
            elif isinstance(event, ProviderThinkingDelta):
                if options.show_thinking:
                    yield AssistantThinkingDelta(index=event.index, text=event.text)
            elif isinstance(event, ProviderStreamCompleted):
                response = event.response
            elif isinstance(event, ProviderStreamFailed):
                raise event.error
            elif isinstance(event, ProviderStreamCancelled):
                raise RuntimeError(event.reason or "provider stream was cancelled")

        if response is None:
            raise RuntimeError("provider stream ended without completion event")
        return response

    def _raise_if_interrupted(self) -> None:
        """在安全点响应 interrupt 请求。"""

        if self._interrupted:
            raise RuntimeError("agent run interrupted")

    def _consume_turn_notices(self) -> tuple[str, ...]:
        """从可选 notice provider 读取本轮 runtime notices。"""

        if self.turn_notice_provider is None:
            return ()
        return self.turn_notice_provider.consume_notices()

    def _set_runtime_notices(self, notices: tuple[str, ...]) -> None:
        """把 runtime notices 写入支持该 projection 的 context runtime。"""

        if not notices:
            return
        set_runtime_notices = getattr(
            self.context_runtime,
            "set_runtime_notices",
            None,
        )
        if not callable(set_runtime_notices):
            raise RuntimeError("context runtime does not support runtime notices")
        set_runtime_notices(notices)

    def _provider_stream_events(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> Iterator[ProviderStreamEvent]:
        """返回 provider stream events，必要时使用 complete fallback。"""

        stream = getattr(self.provider, "stream", None)
        if callable(stream):
            yield from stream(request, options)
            return

        response = self.provider.complete(request)
        yield from complete_response_to_stream_events(
            request_id=self._next_provider_stream_request_id(),
            response=response,
            options=options,
        )

    def _next_provider_stream_request_id(self) -> str:
        """生成 QueryLoop fallback provider stream request id。"""

        self._provider_stream_counter += 1
        return f"provider_{self._provider_stream_counter}"

    def _ensure_provider_response_usable(self, response: ProviderResponse) -> None:
        """拒绝被 provider 截断或拦截的响应，避免伪装成最终答案。"""

        stop_reason = response.stop_reason
        if stop_reason in {"length", "max_tokens"}:
            raise RuntimeError(
                f"provider response was truncated before final answer: {stop_reason}",
            )
        if stop_reason == "content_filter":
            raise RuntimeError("provider response was blocked by content filter")

    def _start_turn(
        self,
        user_message: str,
        *,
        is_continuation: bool = False,
    ) -> TurnState | None:
        """创建 turn state 并发出 turn_started 事件。"""

        turn = None
        if self.session_state is not None:
            turn = self.session_state.new_turn(user_message)
        self._emit(
            TurnStartedEvent(
                user_input=user_message,
                is_continuation=is_continuation,
                **self._event_context(turn),
            ),
        )
        return turn

    def _emit(self, event: AgentEvent) -> None:
        """向 EventBus 写入内部 runtime event。"""

        if self.event_bus is None:
            return
        self.event_bus.emit(event)

    def _event_context(self, turn: TurnState | None) -> dict[str, str | None]:
        """返回 typed event 使用的 session/turn id。"""

        return {
            "session_id": self.session_state.id
            if self.session_state is not None
            else None,
            "turn_id": turn.id if turn is not None else None,
        }
