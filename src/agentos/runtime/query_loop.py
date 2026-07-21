from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from functools import partial

from agentos._sync_work import SyncWorkTracker, bind_sync_work_tracker
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
)
from agentos.runtime._execution_lease import ExecutionLease
from agentos.runtime._run_failure_control import fail_open_run
from agentos.runtime.agent_stream import AgentStream
from agentos.runtime.event_bus import (
    AgentEvent,
    AssistantMessageAppendedEvent,
    EventBus,
    ProviderRequestBuiltEvent,
    ProviderResponseReceivedEvent,
)
from agentos.runtime._execution_control import (
    ExecutionControl,
    PendingToolsCheckpointRequest,
    RunningCheckpointRequest,
    TerminalFailureRequest,
    WaitingCheckpointRequest,
)
from agentos.runtime.continuation import ContinuationRuntime
from agentos.runtime.provider_attempt import (
    ProviderAttemptRunner,
    announced_provider_attempt_events,
    ensure_provider_response_usable,
    provider_stream_events,
    record_provider_retry,
)
from agentos.runtime.provider_request_builder import (
    ProviderRequestBuild,
    ProviderRequestBuilder,
)
from agentos.runtime.query_loop_hooks import QueryLoopHooks
from agentos.runtime.query_loop_support import (
    _FinalContent,
    ContextRuntimeBoundary,
    ArtifactRuntimeBoundary,
    StructuredLoggerBoundary,
    ToolCallRouterBoundary,
    TurnNoticeProvider,
)
from agentos.runtime.query_loop_recovery import restore_prepared_tool_loop
from agentos.runtime.retry import RetryPolicy
from agentos.runtime.execution import (
    AcceptedTurnExecution,
    RestoreAcceptedTurn,
    RunExecutionCursor,
)
from agentos.runtime.run import LocalContinuationInput, RunOptions, RunRequest, UserTurnInput
from agentos.runtime.run_commit import CheckpointCommitStore, RunCommitRuntime
from agentos.runtime.checkpoint import RuntimeCheckpointSource
from agentos.runtime.run_runtime import InMemoryRunStore, RunRuntime, RunWriteGuard
from agentos.runtime.run_driver import RunDriver
from agentos.runtime.session import SessionState
from agentos.runtime.stream_events import (
    AssistantCompleted,
    AssistantContentDelta,
    AssistantThinkingDelta,
    ContextLoaded,
    FinalResult,
    StatusUpdate,
    TurnStreamEvent,
)
from agentos.runtime.tool_batch import ToolBatchRunner
from agentos.runtime.tool_invocations import prepare_tool_invocation_batch
from agentos.runtime.tool_payloads import ToolPayloadRuntime
from agentos.runtime.tool_scheduler import ToolCallScheduler
from agentos.runtime.side_effect_memory import InMemorySideEffectStore
from agentos.runtime.side_effect_store import SideEffectStore
from agentos.runtime.side_effect_resume import SideEffectResume
from agentos.runtime.side_effect_resume_validator import (
    SideEffectResumeValidator,
    require_side_effect_resume_validator,
)
from agentos.runtime.completed_result_projection import CompletedResultProjector
from agentos.runtime.tool_side_effect_runtime import ToolSideEffectRuntime
from agentos.runtime.tool_result_refs import ToolResultRefProjector
from agentos.runtime.turn import TurnState
from agentos.runtime.turn_lifecycle import TurnLifecycle
from agentos.runtime.waiting import LocalWaitingRuntime, WaitingRuntime
from agentos.tokens import HeuristicTokenCounter, TokenCounter


@dataclass(slots=True, weakref_slot=True)
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
    checkpoint_source: RuntimeCheckpointSource | None = None
    checkpoint_store: CheckpointCommitStore | None = None
    tool_payload_runtime: ToolPayloadRuntime | None = None
    artifact_runtime: ArtifactRuntimeBoundary | None = None
    side_effect_store: SideEffectStore | None = None
    side_effect_resume_validator: SideEffectResumeValidator | None = None
    tool_result_ref_projector: ToolResultRefProjector | None = None
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
    _side_effects: ToolSideEffectRuntime = field(init=False, repr=False)
    _tool_payloads: ToolPayloadRuntime = field(init=False, repr=False)

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
        if self.session_state is None:
            self.session_state = SessionState(id=self.run_runtime.session_id)
        elif self.session_state.id != self.run_runtime.session_id:
            raise ValueError("session state and run runtime must share session_id")
        if self.tool_payload_runtime is None:
            self.tool_payload_runtime = ToolPayloadRuntime.for_session(
                self.run_runtime.session_id,
            )
        self._tool_payloads = self.tool_payload_runtime
        if self.side_effect_store is None:
            self.side_effect_store = InMemorySideEffectStore()
        if self.waiting_runtime is None and self.checkpoint_store is None:
            if not isinstance(self.side_effect_store, InMemorySideEffectStore):
                raise TypeError(
                    "local waiting requires InMemorySideEffectStore",
                )
            self.waiting_runtime = LocalWaitingRuntime(
                self.run_runtime,
                self.side_effect_store,
            )
        self._side_effects = ToolSideEffectRuntime(
            store=self.side_effect_store,
            completed_result_projector=CompletedResultProjector(
                context_runtime=self.context_runtime,  # type: ignore[arg-type]
                artifact_runtime=self.artifact_runtime,
            ),
            result_ref_projector=self.tool_result_ref_projector,
        )
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
            continuation_runtime=self.continuation_runtime,
            event_bus=self.event_bus,
            structured_logger=self.structured_logger,
        )
        self._run_driver = RunDriver(
            self.run_runtime,
            self._lifecycle,
            RunCommitRuntime(
                self.run_runtime,
                self.waiting_runtime,
                checkpoint_source=self.checkpoint_source,
                checkpoint_store=self.checkpoint_store,
            ),
            tool_payloads=self.tool_payload_runtime,
        )

    async def execute(self, request: RunRequest) -> AgentStream:
        """校验请求、立即获取执行租约并返回惰性事件流。"""

        if type(request) is not RunRequest:
            raise TypeError("request must be a RunRequest")
        if not isinstance(
            request.input,
            (UserTurnInput, LocalContinuationInput, AcceptedTurnExecution),
        ):
            raise TypeError("run request contains an unsupported input")
        if type(request.input) is AcceptedTurnExecution:
            require_side_effect_resume_validator(
                request.input.preparation,
                self.side_effect_resume_validator,
            )
        self._lifecycle.session_state = self.session_state
        reservation = self._execution_lease.reserve()
        try:
            tracker = SyncWorkTracker()
            run_id = await self._run_driver.prepare(request.input)
            events = bind_sync_work_tracker(
                self._run_driver.events(
                    request,
                    run_id,
                    self._run_provider_tool_events,
                ),
                tracker,
            )
            try:
                return self._execution_lease.open_reserved_stream(
                    reservation,
                    events,
                    cleanup=lambda: self._run_driver.cleanup_open(run_id),
                    pending_sync_work=tracker,
                    failure_control=partial(fail_open_run, self._run_driver, run_id),
                )
            except BaseException:
                await self._run_driver.cancel_open(run_id)
                await events.aclose()
                raise
        finally:
            self._execution_lease.cancel_reservation(reservation)

    def interrupt(self) -> bool:
        """请求取消当前执行租约。"""

        return self._execution_lease.interrupt()

    def _wait_until_idle(self) -> None:
        self._execution_lease.wait_until_idle()

    async def _run_provider_tool_events(
        self,
        run_id: str,
        turn: TurnState | None,
        options: RunOptions,
        preparation: RestoreAcceptedTurn | SideEffectResume,
        guard_source: Callable[[], RunWriteGuard] | None = None,
    ) -> AsyncIterator[TurnStreamEvent | _FinalContent | ExecutionControl]:
        current_guard = guard_source or (lambda: RunWriteGuard(0))
        recovery, terminal_error = await restore_prepared_tool_loop(
            run_id=run_id,
            preparation=preparation,
            messages=self.message_runtime,
            payloads=self._tool_payloads,
            router=self.tool_call_router,
            side_effects=self._side_effects,
            validator=self.side_effect_resume_validator,
            guard=current_guard(),
        )
        if terminal_error is not None:
            yield TerminalFailureRequest(terminal_error)
            return
        assert recovery is not None
        iterations = recovery.iterations
        provider_call_index = recovery.provider_call_index
        recovered_plan = recovery.pending_plan

        while True:
            pending_checkpoint_required = recovered_plan is None
            if pending_checkpoint_required:
                yield StatusUpdate(
                    "context",
                    "正在装载会话上下文、工作状态和可用能力。",
                )
                response: ProviderResponse | None = None
                async with aclosing(
                    self._provider_attempt_events(options, turn),
                ) as events:
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
                self._emit(
                    ProviderResponseReceivedEvent(
                        **self._lifecycle.event_context(turn),
                    ),
                )
                calls = tuple(response.tool_calls)
                if calls and self.tool_call_router is None:
                    raise RuntimeError("tool call router is required for tool calls")
                if self.tool_call_router is not None:
                    calls = tuple(
                        self.tool_call_router.prepare_call(call)
                        for call in calls
                    )
                if not calls:
                    assistant = self.message_runtime.append_assistant(response.content)
                    self._emit(AssistantMessageAppendedEvent(
                        message_id=assistant.id,
                        **self._lifecycle.event_context(turn),
                    ))
                    yield AssistantCompleted(
                        content=response.content,
                        stop_reason=response.stop_reason,
                        tool_call_count=0,
                    )
                    yield FinalResult(response.content)
                    yield _FinalContent(response.content)
                    return
                if turn is None:
                    raise RuntimeError("tool invocation plan requires turn state")
                assistant_id = f"msg_{self.message_runtime.store.next_id_number()}"
                batch = self._tool_payloads.prepare_batch(
                    run_id=run_id,
                    turn_id=turn.id,
                    provider_call_index=provider_call_index,
                    assistant_message_id=assistant_id,
                    calls=calls,
                    contract_for=self.tool_call_router.tool_contract_for,
                )
                plan = batch.plan
                assistant = self.message_runtime.append_assistant(
                    response.content,
                    tool_calls=[ToolCall(call.id, call.name, call.arguments) for call in calls],
                )
                if assistant.id != assistant_id:
                    raise RuntimeError("assistant message identity changed during planning")
                self._emit(AssistantMessageAppendedEvent(
                    message_id=assistant.id,
                    **self._lifecycle.event_context(turn),
                ))
                yield AssistantCompleted(
                    content=response.content,
                    stop_reason=response.stop_reason,
                    tool_call_count=len(calls),
                )
            else:
                assert recovered_plan is not None
                plan = recovered_plan
                recovered_plan = None
                assistant_id = plan.assistant_message_id
                if self.tool_call_router is None:
                    raise RuntimeError("tool call router is required for tool calls")
                batch = prepare_tool_invocation_batch(
                    plan,
                    self.tool_call_router.tool_contract_for,
                )
            if pending_checkpoint_required:
                yield PendingToolsCheckpointRequest(plan)
            iterations += 1
            if iterations > self.max_tool_iterations:
                raise RuntimeError("provider tool-call loop exceeded max iterations")
            if turn is not None:
                turn.increment_tool_iteration()
            runner = ToolBatchRunner(
                messages=self.message_runtime,
                router=self.tool_call_router,
                scheduler=self.tool_scheduler,
                hooks=self._hooks,
                result_budget=self.tool_result_budget,
                token_counter=self.token_counter,
                event_context=self._lifecycle.event_context(turn),
                emit=self._emit,
                side_effects=self._side_effects,
                logger=self.structured_logger,
            )
            async with aclosing(runner.events(
                batch=batch,
                guard=current_guard(),
            )) as events:
                async for event in events:
                    if isinstance(event, WaitingCheckpointRequest):
                        yield event
                        return
                    yield event
            if turn is not None:
                yield RunningCheckpointRequest(
                    RunExecutionCursor(
                        turn_id=turn.id,
                        stage="after_tools",
                        provider_call_index=plan.provider_call_index,
                        assistant_message_id=assistant_id,
                    ),
                )
            provider_call_index = plan.provider_call_index + 1

    async def _provider_attempt_events(
        self,
        options: RunOptions,
        turn: TurnState | None,
    ) -> AsyncIterator[ProviderStreamEvent | ContextLoaded | StatusUpdate]:
        if self.artifact_runtime is not None:
            await self.artifact_runtime.prepare_projection_cache()
        await self.request_builder.prepare_projection_cache()
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
            on_retry=partial(
                record_provider_retry,
                policy=self.retry_policy,
                emit=self._emit,
                log=self._log,
                event_context=self._lifecycle.event_context(None),
            ),
        )
        stream = runner.run_stream(ProviderStreamOptions(options.thinking, options.show_thinking))
        announced = announced_provider_attempt_events(
            stream,
            lambda: prepared_request,
        )
        async with aclosing(announced):
            async for event in announced:
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
        self._emit(
            ProviderRequestBuiltEvent(**self._lifecycle.event_context(turn)),
        )
        self._log(
            "provider_call",
            message_count=len(request.messages),
            tool_count=len(request.tools),
        )
        return request

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
