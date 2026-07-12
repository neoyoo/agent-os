import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from agentos.capabilities.executor import ToolExecutionResult
from agentos.compression import CompressionRuntime
from agentos.hooks import HookManager, HookResult
from agentos.messages import MessageRuntime, ToolCall
from agentos.policies import ToolResultBudget
from agentos.policies.tool_result_budget import cap_tool_result_content
from agentos.providers import (
    Provider,
    ProviderContentDelta,
    ProviderToolCall,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderThinkingDelta,
)
from agentos.runtime.event_bus import (
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderRequestBuiltEvent,
    ProviderResponseReceivedEvent,
    ProviderRetryEvent,
    AgentEvent,
    ToolCallRequestedEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultAppendedEvent,
    ToolResultCappedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
    UserMessageAppendedEvent,
)
from agentos.runtime.provider_attempt import (
    ProviderAttemptRunner,
    ensure_provider_response_usable,
    provider_stream_events,
)
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestBuilder,
)
from agentos.runtime.query_loop_support import (
    ContextRuntimeBoundary as _ContextRuntimeBoundary,
    StructuredLoggerBoundary as _StructuredLoggerBoundary,
    ToolCallRouterBoundary as _ToolCallRouterBoundary,
    TurnNoticeProvider as _TurnNoticeProvider,
    duplicate_tool_call_result,
    skill_loaded_event,
    tool_call_signature,
)
from agentos.runtime.retry import RetryPolicy
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    PlanUpdated,
    StatusUpdate,
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
from agentos.tokens import HeuristicTokenCounter, TokenCounter


class ContextRuntimeBoundary(_ContextRuntimeBoundary, Protocol):
    pass


class TurnNoticeProvider(_TurnNoticeProvider, Protocol):
    pass


class ToolCallRouterBoundary(_ToolCallRouterBoundary, Protocol):
    pass


class StructuredLoggerBoundary(_StructuredLoggerBoundary, Protocol):
    pass


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
    hook_manager: HookManager | None = None
    session_state: SessionState | None = None
    turn_notice_provider: TurnNoticeProvider | None = None
    retry_policy: RetryPolicy | None = None
    structured_logger: StructuredLoggerBoundary | None = None
    tool_result_budget: ToolResultBudget = field(default_factory=ToolResultBudget)
    token_counter: TokenCounter = field(default_factory=HeuristicTokenCounter)
    max_tool_iterations: int = 8
    _provider_stream_counter: int = field(default=0, init=False, repr=False)
    _interrupted: bool = field(default=False, init=False, repr=False)
    _async_provider_event_loop: asyncio.AbstractEventLoop | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        """把 context 投影依赖绑定到 request builder。"""

        self.request_builder._bind_context_source(
            self.context_runtime,
            self.token_counter,
        )

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

    def set_async_provider_event_loop(
        self,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """为 async serving path 设置 provider I/O 所属 event loop。"""

        self._async_provider_event_loop = loop

    def clear_async_provider_event_loop(self) -> None:
        """清除 async provider I/O event loop。"""

        self._async_provider_event_loop = None

    def build_request(self) -> ProviderRequest:
        """构建下一次 provider request，并在请求前执行窗口压缩。"""

        return self._build_request().request

    def _build_request(self) -> ProviderRequestBuild:
        """构建带 temporary receipt 的下一次 provider request。"""

        if self.compression_runtime is not None:
            self.compression_runtime.maybe_compress()
        try:
            return self.request_builder.build()
        finally:
            self._clear_runtime_notices()

    def run_turn(
        self,
        user_message: str,
        *,
        attachments: list[object] | None = None,
    ) -> str:
        """运行一轮 user -> provider -> assistant。"""

        final_content = ""
        for event in self.run_turn_stream(user_message, attachments=attachments):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return final_content

    def run_turn_stream(
        self,
        user_message: str,
        options: RunOptions | None = None,
        *,
        attachments: list[object] | None = None,
    ) -> Iterator[TurnStreamEvent]:
        """运行一轮 user -> provider -> assistant，并产出 typed stream events。"""

        run_options = options or RunOptions()
        self._raise_if_interrupted()
        turn = self._start_turn(user_message)
        self._log("turn_start", user_message_length=len(user_message))
        stored_user_message = self._prepare_user_message(
            user_message,
            attachments or [],
        )
        user = self.message_runtime.append_user(stored_user_message)
        self._emit(
            UserMessageAppendedEvent(
                message_id=user.id,
                **self._event_context(turn),
            ),
        )
        yield TurnStreamStarted(user_message=user_message)
        yield StatusUpdate(
            stage="received",
            message="我已收到请求，先整理上下文再开始执行。",
        )
        yield PlanUpdated(
            status="created",
            summary="先装载上下文和可用能力，再由模型决定是否调用工具或 skill，最后整合结果。",
        )

        try:
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
            self._log("turn_end")
            yield TurnStreamCompleted(content=response_content)
        finally:
            self._clear_turn_loaded_attachments()

    def _prepare_user_message(
        self,
        user_message: str,
        attachments: list[object],
    ) -> str:
        """把附件折叠成安全占位符，并安排首轮 provider request 展开。"""

        if not attachments:
            return user_message
        attachment_runtime = getattr(self.request_builder, "attachment_runtime", None)
        if attachment_runtime is None:
            raise RuntimeError("attachment runtime is required for attachments")
        prepare_user_message = getattr(
            attachment_runtime,
            "prepare_user_message",
            None,
        )
        if not callable(prepare_user_message):
            raise RuntimeError(
                "attachment_runtime must define prepare_user_message()",
            )
        return prepare_user_message(user_message, attachments)

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
            self._clear_turn_loaded_attachments()

    def _clear_runtime_notices(self) -> None:
        """清空 context runtime 中可能残留的一次性 runtime notice。"""

        clear_runtime_notices = getattr(
            self.context_runtime,
            "clear_runtime_notices",
            None,
        )
        if callable(clear_runtime_notices):
            clear_runtime_notices()

    def _clear_turn_loaded_attachments(self) -> None:
        """清空 attachment runtime 中的 turn-scoped attachment state。"""

        attachment_runtime = getattr(self.request_builder, "attachment_runtime", None)
        clear_turn_loaded_attachments = getattr(
            attachment_runtime,
            "clear_turn_loaded_attachments",
            None,
        )
        if callable(clear_turn_loaded_attachments):
            clear_turn_loaded_attachments()

    def _run_provider_loop_stream(
        self,
        turn: TurnState | None,
        options: RunOptions,
    ) -> Iterator[TurnStreamEvent]:
        """执行 provider streaming loop，直到返回 final assistant response。"""

        iterations = 0
        applied_tool_signatures: set[str] = set()
        while True:
            self._raise_if_interrupted()
            yield StatusUpdate(
                stage="context",
                message="正在装载会话上下文、工作状态和可用能力。",
            )
            response: ProviderResponse | None = None
            for event in self._provider_attempt_events(options, turn):
                if isinstance(event, ProviderContentDelta):
                    yield AssistantContentDelta(index=event.index, text=event.text)
                elif isinstance(event, ProviderThinkingDelta):
                    if options.show_thinking:
                        yield AssistantThinkingDelta(index=event.index, text=event.text)
                elif isinstance(event, ProviderStreamCompleted):
                    response = event.response
                elif isinstance(event, (ContextLoaded, StatusUpdate)):
                    yield event
            if response is None:
                raise RuntimeError("provider stream ended without completion event")
            self._emit(ProviderResponseReceivedEvent(**self._event_context(turn)))
            tool_calls = [
                ToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=tool_call.arguments,
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
                yield FinalResult(content=response.content)
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
                yield StatusUpdate(
                    stage="tool",
                    message=f"准备调用工具 `{tool_call.name}`。",
                    detail=tool_call.id,
                )
                yield ToolStreamStarted(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
                self._log(
                    "tool_exec",
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
                    duplicate_result = duplicate_tool_call_result(
                        tool_call,
                        applied_tool_signatures,
                    )
                    if duplicate_result is not None:
                        result = duplicate_result
                    else:
                        hook_result = self._before_tool_call(tool_call)
                        if hook_result is not None:
                            result = hook_result
                        else:
                            result = self.tool_call_router.execute_tool_call(tool_call)
                        result = self._after_tool_call(tool_call, result)
                        applied_tool_signatures.add(tool_call_signature(tool_call))
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
                result = self._cap_tool_result(tool_call, result, turn)
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
                skill_event = skill_loaded_event(tool_call, result)
                if skill_event is not None:
                    yield skill_event
                yield StatusUpdate(
                    stage="tool_result",
                    message=f"已读取 `{tool_call.name}` 的结果，继续推理。",
                    detail=tool_call.id,
                )

    def _cap_tool_result(
        self,
        tool_call: ProviderToolCall,
        result: ToolExecutionResult,
        turn: TurnState | None,
    ) -> ToolExecutionResult:
        capped = cap_tool_result_content(
            tool_name=tool_call.name,
            content=result.content,
            budget=self.tool_result_budget,
            token_counter=self.token_counter,
        )
        if not capped.capped:
            return result
        self._emit(
            ToolResultCappedEvent(
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                actual_tokens=capped.actual_tokens,
                cap=capped.cap,
                **self._event_context(turn),
            ),
        )
        return ToolExecutionResult(
            tool_call_id=result.tool_call_id,
            content=capped.content,
        )

    def _provider_attempt_events(
        self,
        options: RunOptions,
        turn: TurnState | None,
    ) -> Iterator[ProviderStreamEvent | ContextLoaded | StatusUpdate]:
        """运行一次可 retry 的 Provider attempt 序列。"""

        prepared_request: ProviderRequest | None = None

        def prepare(request: ProviderRequest) -> ProviderRequest:
            nonlocal prepared_request
            prepared_request = self._prepare_provider_call(request, turn)
            return prepared_request

        runner = ProviderAttemptRunner(
            request_factory=self._build_request,
            stream_provider=lambda request, stream_options: provider_stream_events(
                self.provider,
                request,
                stream_options,
                async_event_loop=self._async_provider_event_loop,
                request_id_factory=self._next_provider_stream_request_id,
                cancel_requested=lambda: self._interrupted,
            ),
            before_call=prepare,
            after_call=self._after_provider_call,
            ensure_usable=ensure_provider_response_usable,
            consume_temporary=self.message_runtime.consume_temporary_refs,
            retry_policy=self.retry_policy,
            on_retry=self._on_provider_retry,
        )
        events = runner.run_stream(
            ProviderStreamOptions(
                thinking=options.thinking,
                show_thinking=options.show_thinking,
            ),
        )
        announced = False
        for event in events:
            if not announced:
                if prepared_request is None:
                    raise RuntimeError("provider attempt did not prepare a request")
                yield ContextLoaded(
                    source="runtime",
                    summary=(
                        f"已装载 {len(prepared_request.messages)} 条消息和 "
                        f"{len(prepared_request.tools)} 个工具声明。"
                    ),
                )
                yield StatusUpdate(
                    stage="model",
                    message="正在请求模型生成下一步响应。",
                )
                announced = True
            yield event

    def _prepare_provider_call(
        self,
        request: ProviderRequest,
        turn: TurnState | None,
    ) -> ProviderRequest:
        request = self._before_provider_call(request)
        self._emit(ProviderRequestBuiltEvent(**self._event_context(turn)))
        self._log(
            "provider_call",
            message_count=len(request.messages),
            tool_count=len(request.tools),
        )
        return request

    def _on_provider_retry(self, attempt: int, error: Exception) -> None:
        policy = self.retry_policy
        if policy is None:
            raise RuntimeError("provider retry policy is missing")
        delay = policy.delay_for_attempt(attempt)
        self._emit(
            ProviderRetryEvent(
                attempt=attempt,
                max_retries=policy.max_retries,
                error=str(error),
                delay_seconds=delay,
                **self._event_context(None),
            ),
        )
        self._log(
            "provider_retry",
            attempt=attempt,
            max_retries=policy.max_retries,
            error=str(error),
            delay_seconds=delay,
        )
        policy.sleep(delay)

    def _before_provider_call(self, request: ProviderRequest) -> ProviderRequest:
        """执行 before_provider_call hook，可 deny 或替换 request。"""

        result = self._dispatch_hook(
            "before_provider_call",
            {"request": request},
        )
        self._raise_if_denied(result, "provider call")
        if result.action == "modify" and result.payload is not None:
            modified_request = result.payload.get("request")
            if isinstance(modified_request, ProviderRequest):
                return modified_request
        return request

    def _after_provider_call(
        self,
        request: ProviderRequest,
        response: ProviderResponse,
    ) -> ProviderResponse:
        """执行 after_provider_call hook，可替换 response。"""

        result = self._dispatch_hook(
            "after_provider_call",
            {"request": request, "response": response},
        )
        self._raise_if_denied(result, "provider response")
        if result.action == "modify" and result.payload is not None:
            modified_response = result.payload.get("response")
            if isinstance(modified_response, ProviderResponse):
                return modified_response
        return response

    def _before_tool_call(
        self,
        tool_call: object,
    ) -> ToolExecutionResult | None:
        """执行 before_tool_call hook，deny 时生成明确 tool result。"""

        tool_name = str(getattr(tool_call, "name", ""))
        tool_call_id = str(getattr(tool_call, "id", ""))
        result = self._dispatch_hook(
            "before_tool_call",
            {
                "tool_call": tool_call,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
            },
        )
        if result.action == "deny":
            reason = result.reason or "denied"
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                content=f"tool call denied by hook: {reason}",
            )
        return None

    def _after_tool_call(
        self,
        tool_call: object,
        result: object,
    ) -> object:
        """执行 after_tool_call hook，可替换 tool result。"""

        hook_result = self._dispatch_hook(
            "after_tool_call",
            {
                "tool_call": tool_call,
                "tool_name": str(getattr(tool_call, "name", "")),
                "tool_call_id": str(getattr(tool_call, "id", "")),
                "result": result,
            },
        )
        self._raise_if_denied(hook_result, "tool result")
        if hook_result.action == "modify" and hook_result.payload is not None:
            modified_result = hook_result.payload.get("result")
            if modified_result is not None:
                return modified_result
        return result

    def _dispatch_hook(
        self,
        hook_name: str,
        payload: dict[str, object] | None = None,
    ) -> HookResult:
        """执行可选 HookManager。"""

        if self.hook_manager is None:
            return HookResult(action="allow", payload=dict(payload or {}))
        return self.hook_manager.dispatch(  # type: ignore[arg-type]
            hook_name,
            payload,
        )

    def _raise_if_denied(self, result: HookResult, target: str) -> None:
        """把 hook deny 转为明确 RuntimeError。"""

        if result.action == "deny":
            reason = result.reason or "denied"
            raise RuntimeError(f"{target} denied by hook: {reason}")

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

    def _next_provider_stream_request_id(self) -> str:
        """生成 QueryLoop fallback provider stream request id。"""

        self._provider_stream_counter += 1
        return f"provider_{self._provider_stream_counter}"

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

    def _log(self, event: str, **fields: object) -> None:
        """通过可选结构化日志边界记录 runtime metadata。"""

        if self.structured_logger is None:
            return
        context = self._event_context(None)
        if context["session_id"] is not None:
            fields.setdefault("session_id", context["session_id"])
        if context["turn_id"] is not None:
            fields.setdefault("turn_id", context["turn_id"])
        self.structured_logger.log(event, **fields)

    def _event_context(self, turn: TurnState | None) -> dict[str, str | None]:
        """返回 typed event 使用的 session/turn id。"""

        return {
            "session_id": self.session_state.id
            if self.session_state is not None
            else None,
            "turn_id": turn.id if turn is not None else None,
        }
