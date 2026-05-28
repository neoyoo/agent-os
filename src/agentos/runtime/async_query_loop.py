from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agentos.capabilities.executor import ToolExecutionResult
from agentos.compression import CompressionRuntime
from agentos.messages import MessageRuntime, ToolCall
from agentos.policies import ToolResultBudget
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
from agentos.runtime._async_bridge import iterate_sync_in_executor
from agentos.runtime.event_bus import (
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderRequestBuiltEvent,
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
from agentos.runtime.retry import RetryPolicy
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
            self.sync_loop._clear_turn_loaded_images()

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
            self.sync_loop._clear_turn_loaded_images()

    async def _run_provider_loop_stream(
        self,
        turn: TurnState | None,
        options: RunOptions,
    ) -> AsyncIterator[TurnStreamEvent | _FinalContent]:
        iterations = 0
        applied_tool_signatures: set[str] = set()
        while True:
            self._raise_if_interrupted()
            request = self.sync_loop.build_request()
            request = self.sync_loop._before_provider_call(request)
            self.sync_loop._emit(
                ProviderRequestBuiltEvent(**self.sync_loop._event_context(turn)),
            )
            self.sync_loop._log(
                "provider_call",
                message_count=len(request.messages),
                tool_count=len(request.tools),
            )
            response: ProviderResponse | None = None
            async for event in self._consume_provider_stream(request, options):
                if isinstance(event, ProviderResponse):
                    response = event
                else:
                    yield event
            if response is None:
                raise RuntimeError("provider stream ended without completion event")
            response = self.sync_loop._after_provider_call(request, response)
            self.sync_loop._emit(
                ProviderResponseReceivedEvent(**self.sync_loop._event_context(turn)),
            )
            self.sync_loop._ensure_provider_response_usable(response)
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
            self.sync_loop._emit(
                AssistantMessageAppendedEvent(
                    message_id=assistant.id,
                    **self.sync_loop._event_context(turn),
                ),
            )
            yield AssistantCompleted(response=response)

            if not response.tool_calls:
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
                    duplicate_result = self.sync_loop._duplicate_tool_call_result(
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
                            self.sync_loop._tool_call_signature(tool_call),
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

    async def _consume_provider_stream(
        self,
        request: ProviderRequest,
        options: RunOptions,
    ) -> AsyncIterator[TurnStreamEvent | ProviderResponse]:
        provider_options = ProviderStreamOptions(
            thinking=options.thinking,
            show_thinking=options.show_thinking,
        )
        policy = self.retry_policy
        if policy is not None:
            policy.raise_if_open()
        attempt = 0
        while True:
            emitted_visible_delta = False
            try:
                async for event in self._provider_stream_events(
                    request,
                    provider_options,
                ):
                    if isinstance(event, ProviderContentDelta):
                        emitted_visible_delta = True
                        yield AssistantContentDelta(index=event.index, text=event.text)
                    elif isinstance(event, ProviderThinkingDelta):
                        if options.show_thinking:
                            emitted_visible_delta = True
                            yield AssistantThinkingDelta(
                                index=event.index,
                                text=event.text,
                            )
                    elif isinstance(event, ProviderStreamCompleted):
                        yield event.response
                    elif isinstance(event, ProviderStreamFailed):
                        raise event.error
                    elif isinstance(event, ProviderStreamCancelled):
                        raise RuntimeError(
                            event.reason or "provider stream was cancelled",
                        )
                if policy is not None:
                    policy.record_success()
                break
            except Exception as error:
                attempt += 1
                if (
                    emitted_visible_delta
                    or policy is None
                    or not policy.should_retry(error, attempt)
                ):
                    if policy is not None:
                        policy.record_failure()
                    raise
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

    async def _provider_stream_events(
        self,
        request: ProviderRequest,
        options: ProviderStreamOptions,
    ) -> AsyncIterator[ProviderStreamEvent]:
        async_stream = getattr(self.provider, "async_stream", None)
        if callable(async_stream):
            async for event in async_stream(request, options):
                yield event
            return

        async_complete = getattr(self.provider, "async_complete", None)
        if callable(async_complete):
            response = await async_complete(request)
            for event in complete_response_to_stream_events(
                request_id=self.sync_loop._next_provider_stream_request_id(),
                response=response,
                options=options,
            ):
                yield event
            return

        stream = getattr(self.provider, "stream", None)
        if callable(stream):
            async for event in iterate_sync_in_executor(lambda: stream(request, options)):
                yield event
            return

        response = await asyncio.to_thread(self.provider.complete, request)
        for event in complete_response_to_stream_events(
            request_id=self.sync_loop._next_provider_stream_request_id(),
            response=response,
            options=options,
        ):
            yield event

    def _raise_if_interrupted(self) -> None:
        if self._interrupted:
            raise RuntimeError("agent run interrupted")
