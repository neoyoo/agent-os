from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import partial

from agentos.capabilities.executor import ToolExecutionResult
from agentos.compression import CompressionRuntime
from agentos.messages import MessageRuntime, ToolCall
from agentos.policies import ToolResultBudget
from agentos.providers import (
    Provider,
    ProviderContentDelta,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamCompleted,
    ProviderStreamEvent,
    ProviderStreamOptions,
    ProviderThinkingDelta,
)
from agentos.runtime.async_provider_attempt import (
    AsyncProviderAttemptRunner,
    async_provider_stream_events,
)
from agentos.runtime.provider_attempt import ensure_provider_response_usable
from agentos.runtime.event_bus import (
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderResponseReceivedEvent,
    ProviderRetryEvent,
    ToolCallRequestedEvent,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
    ToolResultAppendedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    UserMessageAppendedEvent,
)
from agentos.runtime.provider_request_builder import ProviderRequestBuilder
from agentos.runtime.query_loop import (
    ContextRuntimeBoundary,
    QueryLoop,
    ToolCallRouterBoundary,
    TurnNoticeProvider,
)
import agentos.runtime.query_loop_support as query_loop_support
from agentos.runtime.retry import RetryPolicy
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    PlanUpdated,
    RunOptions,
    StatusUpdate,
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


@dataclass(frozen=True, slots=True)
class _FinalContent:
    content: str


@dataclass(slots=True)
class AsyncQueryLoop:
    """异步 agent turn 调度器，原生 await provider 和 tool I/O。"""

    context_runtime: ContextRuntimeBoundary
    message_runtime: MessageRuntime
    request_builder: ProviderRequestBuilder
    provider: Provider
    compression_runtime: CompressionRuntime | None = None
    tool_call_router: ToolCallRouterBoundary | None = None
    event_bus: EventBus | None = None
    hook_manager: object | None = None
    session_state: SessionState | None = None
    turn_notice_provider: TurnNoticeProvider | None = None
    retry_policy: RetryPolicy | None = None
    tool_result_budget: ToolResultBudget = field(default_factory=ToolResultBudget)
    token_counter: TokenCounter = field(default_factory=HeuristicTokenCounter)
    max_tool_iterations: int = 8
    sync_loop: QueryLoop = field(init=False)
    _interrupted: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """构造兼容 sync facade 的 QueryLoop，但 native async 路径不委托它执行。"""

        self.sync_loop = QueryLoop(
            context_runtime=self.context_runtime,
            message_runtime=self.message_runtime,
            request_builder=self.request_builder,
            provider=self.provider,
            compression_runtime=self.compression_runtime,
            tool_call_router=self.tool_call_router,
            event_bus=self.event_bus,
            hook_manager=self.hook_manager,  # type: ignore[arg-type]
            session_state=self.session_state,
            turn_notice_provider=self.turn_notice_provider,
            retry_policy=self.retry_policy,
            tool_result_budget=self.tool_result_budget,
            token_counter=self.token_counter,
            max_tool_iterations=self.max_tool_iterations,
        )

    @property
    def interrupted(self) -> bool:
        """判断当前 async loop 是否已收到显式中断请求。"""

        return self._interrupted

    def request_interrupt(self) -> None:
        """请求在下一个安全点中断运行。"""

        self._interrupted = True
        self.sync_loop.request_interrupt()

    def clear_interrupt(self) -> None:
        """清除中断请求。"""

        self._interrupted = False
        self.sync_loop.clear_interrupt()

    async def run_turn(self, user_message: str) -> str:
        """异步运行完整 turn，返回最终内容。"""

        final_content = ""
        async for event in self.run_turn_stream(user_message):
            if isinstance(event, TurnStreamCompleted):
                final_content = event.content
        return final_content

    async def run_turn_stream(
        self,
        user_message: str,
        options: RunOptions | None = None,
        *,
        attachments: list[object] | None = None,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 turn，产出 typed stream events。"""

        run_options = options or RunOptions()
        self._raise_if_interrupted()
        turn = self.sync_loop._start_turn(user_message)
        self.sync_loop._log("turn_start", user_message_length=len(user_message))
        stored_user_message = self.sync_loop._prepare_user_message(
            user_message,
            attachments or [],
        )
        user = self.message_runtime.append_user(stored_user_message)
        self.sync_loop._emit(
            UserMessageAppendedEvent(
                message_id=user.id,
                **self.sync_loop._event_context(turn),
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
                response_content = ""
                async for event in self._run_provider_loop_stream(turn, run_options):
                    if isinstance(event, _FinalContent):
                        response_content = event.content
                    else:
                        yield event
            except Exception as error:
                if turn is not None:
                    turn.fail(str(error))
                self.sync_loop._emit(
                    TurnFailedEvent(
                        error=str(error),
                        **self.sync_loop._event_context(turn),
                    ),
                )
                yield TurnStreamFailed(error=error)
                raise

            if turn is not None:
                turn.complete()
            self.sync_loop._emit(
                TurnCompletedEvent(**self.sync_loop._event_context(turn)),
            )
            self.sync_loop._log("turn_end")
            yield TurnStreamCompleted(content=response_content)
        finally:
            self.sync_loop._clear_turn_loaded_attachments()

    async def run_continuation_stream(
        self,
        options: RunOptions | None = None,
    ) -> AsyncIterator[TurnStreamEvent]:
        """异步运行 runtime continuation turn。"""

        run_options = options or RunOptions()
        self._raise_if_interrupted()
        notices = self.sync_loop._consume_turn_notices()
        if not notices:
            return
        turn = self.sync_loop._start_turn("", is_continuation=True)
        self.sync_loop._set_runtime_notices(notices)
        try:
            yield TurnStreamStarted(user_message="")
            try:
                response_content = ""
                async for event in self._run_provider_loop_stream(turn, run_options):
                    if isinstance(event, _FinalContent):
                        response_content = event.content
                    else:
                        yield event
            except Exception as error:
                if turn is not None:
                    turn.fail(str(error))
                self.sync_loop._emit(
                    TurnFailedEvent(
                        error=str(error),
                        **self.sync_loop._event_context(turn),
                    ),
                )
                yield TurnStreamFailed(error=error)
                raise

            if turn is not None:
                turn.complete()
            self.sync_loop._emit(
                TurnCompletedEvent(**self.sync_loop._event_context(turn)),
            )
            yield TurnStreamCompleted(content=response_content)
        finally:
            self.sync_loop._clear_runtime_notices()
            self.sync_loop._clear_turn_loaded_attachments()

    async def _run_provider_loop_stream(
        self,
        turn: TurnState | None,
        options: RunOptions,
    ) -> AsyncIterator[TurnStreamEvent | _FinalContent]:
        iterations = 0
        applied_tool_signatures: set[str] = set()
        while True:
            self._raise_if_interrupted()
            yield StatusUpdate(
                stage="context",
                message="正在装载会话上下文、工作状态和可用能力。",
            )
            response: ProviderResponse | None = None
            async for event in self._provider_attempt_events(options, turn):
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
            self.sync_loop._emit(
                ProviderResponseReceivedEvent(**self.sync_loop._event_context(turn)),
            )
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
            self.sync_loop._emit(
                AssistantMessageAppendedEvent(
                    message_id=assistant.id,
                    **self.sync_loop._event_context(turn),
                ),
            )
            yield AssistantCompleted(response=response)

            if not response.tool_calls:
                yield FinalResult(content=response.content)
                yield _FinalContent(response.content)
                return
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
                self.sync_loop._log(
                    "tool_exec",
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
                self.sync_loop._emit(
                    ToolCallRequestedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self.sync_loop._event_context(turn),
                    ),
                )
                self.sync_loop._emit(
                    ToolExecutionStartedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self.sync_loop._event_context(turn),
                    ),
                )
                try:
                    duplicate_result = query_loop_support.duplicate_tool_call_result(
                        tool_call,
                        applied_tool_signatures,
                    )
                    if duplicate_result is not None:
                        result = duplicate_result
                    else:
                        hook_result = self.sync_loop._before_tool_call(tool_call)
                        if hook_result is not None:
                            result = hook_result
                        else:
                            result = await self._execute_tool_call(tool_call)
                        result = self.sync_loop._after_tool_call(tool_call, result)
                        applied_tool_signatures.add(
                            query_loop_support.tool_call_signature(tool_call),
                        )
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
                self.sync_loop._emit(
                    ToolExecutionCompletedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        **self.sync_loop._event_context(turn),
                    ),
                )
                result = self.sync_loop._cap_tool_result(tool_call, result, turn)
                tool_result = self.message_runtime.append_tool_result(
                    tool_call_id=result.tool_call_id,
                    content=result.content,
                )
                appended_message_ids.append(tool_result.id)
                self.sync_loop._emit(
                    ToolResultAppendedEvent(
                        tool_name=tool_call.name,
                        tool_call_id=tool_call.id,
                        message_id=tool_result.id,
                        **self.sync_loop._event_context(turn),
                    ),
                )
                yield ToolStreamCompleted(
                    tool_name=tool_call.name,
                    tool_call_id=tool_call.id,
                    content=result.content,
                )
                skill_event = query_loop_support.skill_loaded_event(tool_call, result)
                if skill_event is not None:
                    yield skill_event
                yield StatusUpdate(
                    stage="tool_result",
                    message=f"已读取 `{tool_call.name}` 的结果，继续推理。",
                    detail=tool_call.id,
                )

    async def _execute_tool_call(self, tool_call: object) -> ToolExecutionResult:
        if self.tool_call_router is None:
            raise RuntimeError("tool call router is required for tool calls")
        async_execute = getattr(self.tool_call_router, "async_execute_tool_call", None)
        if callable(async_execute):
            return await async_execute(tool_call)
        execute = getattr(self.tool_call_router, "execute_tool_call", None)
        if callable(execute):
            return await asyncio.to_thread(execute, tool_call)
        raise RuntimeError("tool call router must define execute_tool_call()")

    async def _provider_attempt_events(
        self,
        options: RunOptions,
        turn: TurnState | None,
    ) -> AsyncIterator[ProviderStreamEvent | ContextLoaded | StatusUpdate]:
        """运行一次可 retry 的异步 Provider attempt 序列。"""

        prepared_request: ProviderRequest | None = None

        def prepare(request: ProviderRequest) -> ProviderRequest:
            nonlocal prepared_request
            prepared_request = self.sync_loop._prepare_provider_call(request, turn)
            return prepared_request

        runner = AsyncProviderAttemptRunner(
            request_factory=self.sync_loop._build_request,
            stream_provider=partial(
                async_provider_stream_events,
                self.provider,
                request_id_factory=self.sync_loop._next_provider_stream_request_id,
            ),
            before_call=prepare,
            after_call=self.sync_loop._after_provider_call,
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
        async for event in events:
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

    async def _on_provider_retry(self, attempt: int, error: Exception) -> None:
        policy = self.retry_policy
        if policy is None:
            raise RuntimeError("provider retry policy is missing")
        delay = policy.delay_for_attempt(attempt)
        self.sync_loop._emit(
            ProviderRetryEvent(
                attempt=attempt,
                max_retries=policy.max_retries,
                error=str(error),
                delay_seconds=delay,
                **self.sync_loop._event_context(None),
            ),
        )
        self.sync_loop._log(
            "provider_retry",
            attempt=attempt,
            max_retries=policy.max_retries,
            error=str(error),
            delay_seconds=delay,
        )
        await asyncio.to_thread(policy.sleep, delay)

    def _raise_if_interrupted(self) -> None:
        if self._interrupted:
            raise RuntimeError("agent run interrupted")
