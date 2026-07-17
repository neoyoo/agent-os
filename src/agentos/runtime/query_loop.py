from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from functools import partial

from agentos._waiting import WaitRequest
from agentos._sync_work import SyncWorkTracker, bind_sync_work_tracker, run_sync
from agentos.capabilities.executor import ToolExecutionOutcome, ToolExecutionResult
from agentos.compression import CompressionRuntime
from agentos.hooks import HookManager
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
    ProviderToolCall,
)
from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.event_bus import (
    AgentEvent,
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderRequestBuiltEvent,
    ProviderResponseReceivedEvent,
    ProviderRetryEvent,
    ToolCallRequestedEvent,
    ToolResultAppendedEvent,
)
from agentos.runtime._tool_observation import ToolObservationMapper
from agentos.runtime.continuation import ContinuationRuntime
from agentos.runtime.provider_attempt import (
    ProviderAttemptRunner,
    ensure_provider_response_usable,
    provider_stream_events,
)
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestBuilder,
)
from agentos.runtime.query_loop_hooks import QueryLoopHooks
from agentos.runtime.query_loop_support import (
    _FinalContent,
    _ToolCallFailure,
    ContextRuntimeBoundary,
    ArtifactRuntimeBoundary,
    StructuredLoggerBoundary,
    ToolCallRouterBoundary,
    TurnNoticeProvider,
    duplicate_tool_call_result,
    map_tool_result_cap,
    resolve_waiting_tool_batch,
    skill_loaded_event,
    tool_call_signature,
    waiting_peer_completion,
)
from agentos.runtime.retry import RetryPolicy
from agentos.runtime.run import LocalContinuationInput, RunOptions, RunRequest, UserTurnInput
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime
from agentos.runtime.run_driver import RunDriver
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    StatusUpdate,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamEvent,
)
from agentos.runtime.tool_scheduler import ToolCallScheduler, ToolExecutionContext
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle
from agentos.runtime.waiting import LocalWaitingRuntime, WaitingRuntime
from agentos.tokens import HeuristicTokenCounter, TokenCounter


@dataclass(slots=True)
class QueryLoop:
    """唯一的原生异步 agent turn 调度器。"""

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
    waiting_runtime: WaitingRuntime | None = None
    artifact_runtime: ArtifactRuntimeBoundary | None = None
    continuation_runtime: ContinuationRuntime = field(
        default_factory=ContinuationRuntime,
    )
    run_runtime: RunRuntime | None = None
    retry_policy: RetryPolicy | None = None
    structured_logger: StructuredLoggerBoundary | None = None
    tool_result_budget: ToolResultBudget = field(default_factory=ToolResultBudget)
    token_counter: TokenCounter = field(default_factory=HeuristicTokenCounter)
    max_tool_iterations: int = 8
    tool_scheduler: ToolCallScheduler = field(default_factory=ToolCallScheduler)
    _provider_stream_counter: int = field(default=0, init=False, repr=False)
    _execution_lease: ExecutionLease = field(default_factory=ExecutionLease, init=False, repr=False)
    _hooks: QueryLoopHooks = field(init=False, repr=False)
    _lifecycle: TurnLifecycle = field(init=False, repr=False)
    _run_driver: RunDriver = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.run_runtime is None:
            session_id = (
                self.session_state.id
                if self.session_state is not None
                else getattr(self.context_runtime, "session_id", None) or "session_local"
            )
            self.run_runtime = RunRuntime(
                session_id=session_id,
                store=InMemoryRunStore(),
            )
        if self.waiting_runtime is None:
            self.waiting_runtime = LocalWaitingRuntime(self.run_runtime)
        if all(
            provider is not self.continuation_runtime
            for provider in self.request_builder.input_projections
        ):
            self.request_builder.input_projections = (
                self.continuation_runtime,
                *self.request_builder.input_projections,
            )
        self.request_builder._bind_context_source(
            self.context_runtime,
            self.token_counter,
        )
        self._hooks = QueryLoopHooks(self.hook_manager)
        self._lifecycle = TurnLifecycle(
            context_runtime=self.context_runtime,
            message_runtime=self.message_runtime,
            artifact_runtime=self.artifact_runtime,
            session_state=self.session_state,
            turn_notice_provider=self.turn_notice_provider,
            waiting_runtime=self.waiting_runtime,
            continuation_runtime=self.continuation_runtime,
            event_bus=self.event_bus,
            structured_logger=self.structured_logger,
        )
        self._run_driver = RunDriver(self.run_runtime, self._lifecycle)

    async def execute(self, request: RunRequest) -> AgentStream:
        """校验请求、立即获取执行租约并返回惰性事件流。"""

        if type(request) is not RunRequest:
            raise TypeError("request must be a RunRequest")
        if not isinstance(request.input, (UserTurnInput, LocalContinuationInput)):
            raise TypeError("run request contains an unsupported input")
        self._lifecycle.session_state = self.session_state
        tracker = SyncWorkTracker()
        run_id = self._run_driver.prepare()
        events = bind_sync_work_tracker(
            self._run_driver.events(
                request,
                run_id,
                self._run_provider_tool_events,
            ),
            tracker,
        )
        try:
            return self._execution_lease.open_stream(
                events,
                cleanup=lambda: self._run_driver.cancel_open(run_id),
                pending_sync_work=tracker,
            )
        except BaseException:
            self._run_driver.cancel_open(run_id)
            await events.aclose()
            raise

    def interrupt(self) -> bool:
        """请求取消当前执行租约。"""

        return self._execution_lease.interrupt()

    def _wait_until_idle(self) -> None:
        self._execution_lease.wait_until_idle()

    async def _run_provider_tool_events(
        self,
        turn: TurnState | None,
        options: RunOptions,
    ) -> AsyncIterator[TurnStreamEvent | _FinalContent | WaitRequest]:
        iterations = 0
        applied_signatures: set[str] = set()
        while True:
            yield StatusUpdate(
                "context",
                "正在装载会话上下文、工作状态和可用能力。",
            )
            response: ProviderResponse | None = None
            async with aclosing(self._provider_attempt_events(options, turn)) as events:
                async for event in events:
                    if isinstance(event, ProviderContentDelta):
                        yield AssistantContentDelta(event.index, event.text)
                    elif isinstance(event, ProviderThinkingDelta):
                        if options.show_thinking:
                            yield AssistantThinkingDelta(event.index, event.text)
                    elif isinstance(event, ProviderStreamCompleted):
                        response = event.response
                    elif isinstance(event, (ContextLoaded, StatusUpdate)):
                        yield event
            if response is None:
                raise RuntimeError("provider stream ended without completion event")
            self._emit(ProviderResponseReceivedEvent(**self._event_context(turn)))
            assistant = self.message_runtime.append_assistant(
                response.content,
                tool_calls=[
                    ToolCall(call.id, call.name, call.arguments)
                    for call in response.tool_calls
                ],
            )
            self._emit(
                AssistantMessageAppendedEvent(
                    message_id=assistant.id,
                    **self._event_context(turn),
                ),
            )
            yield AssistantCompleted(response)
            if not response.tool_calls:
                yield FinalResult(response.content)
                yield _FinalContent(response.content)
                return
            if self.tool_call_router is None:
                raise RuntimeError("tool call router is required for tool calls")
            iterations += 1
            if iterations > self.max_tool_iterations:
                raise RuntimeError("provider tool-call loop exceeded max iterations")
            if turn is not None:
                turn.increment_tool_iteration()
            async with aclosing(
                self._run_tool_batch(
                    tuple(response.tool_calls),
                    turn,
                    applied_signatures,
                    assistant.id,
                ),
            ) as events:
                async for event in events:
                    if isinstance(event, WaitRequest):
                        yield event
                        return
                    yield event

    async def _run_tool_batch(
        self,
        calls: tuple[ProviderToolCall, ...],
        turn: TurnState | None,
        applied_signatures: set[str],
        assistant_id: str,
    ) -> AsyncIterator[TurnStreamEvent | WaitRequest]:
        immediate: dict[str, ToolExecutionResult] = {}
        # 保留原 batch 索引与大小，避免 immediate 结果让后续调用重编号。
        scheduled: list[tuple[int, ProviderToolCall]] = []
        appended_result_ids: list[str] = []
        started_calls: list[ProviderToolCall] = []
        started_events_emitted = False
        try:
            for batch_index, call in enumerate(calls):
                yield StatusUpdate("tool", f"准备调用工具 `{call.name}`。", call.id)
                self._emit(ToolCallRequestedEvent(
                    tool_name=call.name,
                    tool_call_id=call.id,
                    **self._event_context(turn),
                ))
                result = duplicate_tool_call_result(call, applied_signatures)
                if result is None:
                    result = self._hooks.before_tool_call(call)
                    applied_signatures.add(tool_call_signature(call))
                if result is None:
                    scheduled.append((batch_index, call))
                else:
                    immediate[call.id] = result
                    started_calls.append(call)

            observation = ToolObservationMapper(**self._event_context(turn))
            cap_result = partial(
                map_tool_result_cap, budget=self.tool_result_budget,
                token_counter=self.token_counter, **self._event_context(turn),
            )

            async def execute(call: ProviderToolCall, context: ToolExecutionContext) -> ToolExecutionOutcome:
                started_calls.append(call)
                self._log("tool_exec", tool_name=call.name, tool_call_id=call.id)
                self._emit(observation.started(call, context))
                try:
                    return await self._execute_tool_call(call)
                except Exception as error:
                    raise _ToolCallFailure(call, error) from error

            completed = await self.tool_scheduler.execute_batch(
                calls=tuple(call for _, call in scheduled),
                batch_indexes=tuple(index for index, _ in scheduled),
                batch_size=len(calls),
                policy_for=self.tool_call_router.concurrency_policy_for,
                execute=execute,
                on_completed=lambda item: self._emit(observation.completed(item)),
            )
            waiting = resolve_waiting_tool_batch(
                calls=calls,
                immediate=immediate,
                completed=completed,
            )
            if waiting is not None:
                for call in started_calls:
                    if call.id in waiting.visible_started_call_ids:
                        yield ToolStreamStarted(call.name, call.id)
                started_events_emitted = True
                for call, result in waiting.peer_results:
                    completed_event, cap_event = waiting_peer_completion(
                        call, result, self._hooks.after_tool_call, cap_result,
                    )
                    if cap_event is not None:
                        self._emit(cap_event)
                    yield completed_event
                self._rollback_tool_batch(assistant_id, appended_result_ids)
                yield waiting.request
                return
            for call in started_calls:
                yield ToolStreamStarted(call.name, call.id)
            started_events_emitted = True
            raw_results = immediate | {item.tool_call.id: item.result for item in completed}
            final_results: list[tuple[ProviderToolCall, ToolExecutionResult, AgentEvent | None]] = []
            for call in calls:
                result = self._hooks.after_tool_call(call, raw_results[call.id])
                capped = cap_result(call, result)
                final_results.append((call, capped.result, capped.event))
            committed_results: list[
                tuple[ProviderToolCall, ToolExecutionResult, str, AgentEvent | None]
            ] = []
            for call, result, cap_event in final_results:
                stored = self.message_runtime.append_tool_result(result.tool_call_id, result.content)
                appended_result_ids.append(stored.id)
                committed_results.append((call, result, stored.id, cap_event))
        except _ToolCallFailure as failure:
            if not started_events_emitted:
                for call in started_calls:
                    yield ToolStreamStarted(call.name, call.id)
            self._rollback_tool_batch(assistant_id, appended_result_ids)
            yield ToolStreamFailed(failure.call.name, failure.call.id, failure.error)
            raise failure.error from None
        except BaseException:
            self._rollback_tool_batch(assistant_id, appended_result_ids)
            raise

        for call, _result, message_id, cap_event in committed_results:
            if cap_event is not None:
                self._emit(cap_event)
            self._emit(ToolResultAppendedEvent(
                tool_name=call.name,
                tool_call_id=call.id,
                message_id=message_id,
                **self._event_context(turn),
            ))
        for call, result, _message_id, _cap_event in committed_results:
            yield ToolStreamCompleted(call.name, call.id, result.content)
            skill_event = skill_loaded_event(call, result)
            if skill_event is not None:
                yield skill_event
            yield StatusUpdate("tool_result", f"已读取 `{call.name}` 的结果，继续推理。", call.id)

    def _rollback_tool_batch(self, assistant_id: str, result_ids: list[str]) -> None:
        self.message_runtime.active_window.remove_refs(
            [assistant_id, *result_ids],
            self.message_runtime.store,
        )

    async def _execute_tool_call(self, call: ProviderToolCall) -> ToolExecutionOutcome:
        if self.tool_call_router is None:
            raise RuntimeError("tool call router is required for tool calls")
        return await self.tool_call_router.async_execute_tool_call(call)

    async def _provider_attempt_events(
        self,
        options: RunOptions,
        turn: TurnState | None,
    ) -> AsyncIterator[ProviderStreamEvent | ContextLoaded | StatusUpdate]:
        prepared_request: ProviderRequest | None = None

        def prepare(request: ProviderRequest) -> ProviderRequest:
            nonlocal prepared_request
            prepared_request = self._prepare_provider_call(request, turn)
            return prepared_request

        runner = ProviderAttemptRunner(
            request_factory=self._build_request,
            stream_provider=partial(
                provider_stream_events,
                self.provider,
                request_id_factory=self._next_provider_stream_request_id,
            ),
            before_call=prepare,
            after_call=self._hooks.after_provider_call,
            ensure_usable=ensure_provider_response_usable,
            consume_temporary=self.message_runtime.consume_temporary_refs,
            retry_policy=self.retry_policy,
            on_retry=self._on_provider_retry,
        )
        announced = False
        stream = runner.run_stream(ProviderStreamOptions(options.thinking, options.show_thinking))
        async with aclosing(stream):
            async for event in stream:
                if not announced:
                    if prepared_request is None:
                        raise RuntimeError("provider attempt did not prepare a request")
                    yield ContextLoaded(
                        "runtime",
                        f"已装载 {len(prepared_request.messages)} 条消息和 "
                        f"{len(prepared_request.tools)} 个工具声明。",
                    )
                    yield StatusUpdate("model", "正在请求模型生成下一步响应。")
                    announced = True
                yield event

    def _build_request(self) -> ProviderRequestBuild:
        if self.compression_runtime is not None:
            self.compression_runtime.maybe_compress()
        return self.request_builder.build()

    def _prepare_provider_call(
        self,
        request: ProviderRequest,
        turn: TurnState | None,
    ) -> ProviderRequest:
        request = self._hooks.before_provider_call(request)
        self._emit(ProviderRequestBuiltEvent(**self._event_context(turn)))
        self._log(
            "provider_call",
            message_count=len(request.messages),
            tool_count=len(request.tools),
        )
        return request

    async def _on_provider_retry(self, attempt: int, error: Exception) -> None:
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
        await run_sync(policy.sleep, delay)

    def _next_provider_stream_request_id(self) -> str:
        self._provider_stream_counter += 1
        return f"provider_{self._provider_stream_counter}"

    def _emit(self, event: AgentEvent) -> None:
        if self.event_bus is not None:
            self.event_bus.emit(event)

    def _log(self, event: str, **fields: object) -> None:
        if self.structured_logger is not None:
            if self.session_state is not None:
                fields.setdefault("session_id", self.session_state.id)
            self.structured_logger.log(event, **fields)

    def _event_context(self, turn: TurnState | None) -> dict[str, str | None]:
        return self._lifecycle.event_context(turn)
