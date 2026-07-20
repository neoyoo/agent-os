from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from functools import partial

from agentos._waiting import WaitRequest
from agentos.capabilities.executor import (
    ToolExecutionError,
    ToolExecutionOutcome,
    ToolExecutionResult,
)
from agentos.messages import ActiveWindow, MessageRuntime
from agentos.policies import ToolResultBudget
from agentos.policies.security import SecurityPolicyError
from agentos.providers import ProviderToolCall
from agentos.runtime._execution_control import WaitingCheckpointRequest
from agentos.runtime._tool_observation import ToolObservationMapper
from agentos.runtime.event_bus import (
    AgentEvent,
    ToolResultAppendedEvent,
    ToolResultCappedEvent,
)
from agentos.runtime.query_loop_hooks import QueryLoopHooks
from agentos.runtime.query_loop_support import (
    _ToolCallFailure,
    StructuredLoggerBoundary,
    ToolCallRouterBoundary,
    map_tool_result_cap,
    resolve_waiting_tool_batch,
    skill_loaded_event,
)
from agentos.runtime.stream_events import (
    StatusUpdate,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamEvent,
)
from agentos.runtime.tool_scheduler import ToolCallScheduler, ToolExecutionContext
from agentos.runtime.tool_invocations import PreparedToolInvocationBatch
from agentos.runtime.run_runtime import RunWriteGuard
from agentos.runtime.tool_side_effect_runtime import (
    ToolSideEffectRuntime,
    WaitingToolHandoff,
)
from agentos.tokens import TokenCounter


@dataclass(slots=True)
class ToolBatchRunner:
    """执行一个 Provider Tool batch，并串行提交消息结果。"""

    messages: MessageRuntime
    router: ToolCallRouterBoundary
    scheduler: ToolCallScheduler
    hooks: QueryLoopHooks
    result_budget: ToolResultBudget
    token_counter: TokenCounter
    event_context: dict[str, str | None]
    emit: Callable[[AgentEvent], None]
    side_effects: ToolSideEffectRuntime
    logger: StructuredLoggerBoundary | None = None

    async def events(
        self,
        *,
        batch: PreparedToolInvocationBatch,
        guard: RunWriteGuard,
    ) -> AsyncIterator[TurnStreamEvent | WaitingCheckpointRequest]:
        plan = batch.plan
        calls = plan.provider_calls()
        assistant_id = plan.assistant_message_id
        entry_by_call_id = {
            entry.invocation.context.tool_call_id: entry
            for entry in plan.entries
        }
        contract_by_call_id = {
            entry.invocation.context.tool_call_id: contract
            for entry, contract in zip(
                plan.entries,
                batch.contracts,
                strict=True,
            )
        }
        scheduled: list[tuple[int, ProviderToolCall]] = []
        appended_result_ids: list[str] = []
        started_calls: list[ProviderToolCall] = []
        started_events_emitted = False
        waiting_handoff = False
        waiting_outcomes: dict[str, WaitingToolHandoff] = {}
        cap_events: dict[str, ToolResultCappedEvent] = {}
        sanitized_failure = False
        try:
            for batch_index, call in enumerate(calls):
                yield StatusUpdate("tool", f"准备调用工具 `{call.name}`。", call.id)
                from agentos.runtime.event_bus import ToolCallRequestedEvent

                self.emit(ToolCallRequestedEvent(
                    tool_name=call.name,
                    tool_call_id=call.id,
                    **self.event_context,
                ))
                scheduled.append((batch_index, call))

            observation = ToolObservationMapper(**self.event_context)
            cap_result = partial(
                map_tool_result_cap,
                budget=self.result_budget,
                token_counter=self.token_counter,
                **self.event_context,
            )

            async def execute(
                call: ProviderToolCall,
                context: ToolExecutionContext,
            ) -> ToolExecutionOutcome:
                started_calls.append(call)
                if self.logger is not None:
                    self.logger.log(
                        "tool_exec",
                        tool_name=call.name,
                        tool_call_id=call.id,
                        **self.event_context,
                    )
                self.emit(observation.started(call, context))
                try:
                    entry = entry_by_call_id[call.id]

                    async def produce(invocation):  # type: ignore[no-untyped-def]
                        result = self.hooks.before_tool_call(call)
                        if result is None:
                            result = await self.router.execute(invocation)
                        if isinstance(result, WaitRequest):
                            return result
                        return self.hooks.after_tool_call(call, result)

                    def map_result(result: ToolExecutionResult) -> ToolExecutionResult:
                        capped = cap_result(call, result)
                        if capped.event is not None:
                            cap_events[call.id] = capped.event
                        return capped.result

                    outcome = await self.side_effects.execute(
                        entry,
                        contract_by_call_id[call.id],
                        guard=guard,
                        produce=produce,
                        map_result=map_result,
                    )
                    if isinstance(outcome, WaitingToolHandoff):
                        waiting_outcomes[call.id] = outcome
                        return outcome.request
                    return outcome
                except Exception as error:
                    raise _ToolCallFailure(
                        call,
                        error,
                        expose_error=isinstance(error, SecurityPolicyError),
                    ) from error

            completed = await self.scheduler.execute_batch(
                calls=tuple(call for _, call in scheduled),
                batch_indexes=tuple(index for index, _ in scheduled),
                batch_size=len(calls),
                policy_for=lambda call: contract_by_call_id[
                    call.id
                ].concurrency_policy,
                execute=execute,
                on_completed=lambda item: self.emit(observation.completed(item)),
            )
            waiting = resolve_waiting_tool_batch(
                calls=calls,
                immediate={},
                completed=completed,
            )
            if waiting is not None:
                for call in started_calls:
                    if call.id in waiting.visible_started_call_ids:
                        yield ToolStreamStarted(call.name, call.id)
                started_events_emitted = True
                for call, result in waiting.peer_results:
                    yield ToolStreamCompleted(call.name, call.id, result.content)
                remove_refs = (assistant_id, *appended_result_ids)
                projection = ActiveWindow.from_refs(
                    self.messages.active_window.snapshot_refs(),
                )
                projection.remove_refs(list(remove_refs), self.messages.store)
                waiting_handoff = True
                yield WaitingCheckpointRequest(
                    reason=waiting.request.reason,
                    active_refs=tuple(
                        ref.message_id
                        for ref in projection.snapshot_refs()
                        if not ref.temporary
                    ),
                    remove_active_refs=remove_refs,
                    completion=waiting_outcomes[waiting.tool_call_id].completion,
                )
                return

            for call in started_calls:
                yield ToolStreamStarted(call.name, call.id)
            started_events_emitted = True
            raw_results = {
                item.tool_call.id: item.result for item in completed
            }
            committed_results = []
            for call in calls:
                result = raw_results[call.id]
                if not isinstance(result, ToolExecutionResult):
                    raise RuntimeError("tool batch did not produce a provider result")
                stored = self.messages.append_tool_result(
                    result.tool_call_id,
                    result.content,
                )
                appended_result_ids.append(stored.id)
                committed_results.append(
                    (call, result, stored.id, cap_events.get(call.id)),
                )
        except _ToolCallFailure as failure:
            if not started_events_emitted:
                for call in started_calls:
                    yield ToolStreamStarted(call.name, call.id)
            self._rollback(assistant_id, appended_result_ids)
            if failure.expose_error:
                yield ToolStreamFailed(
                    failure.call.name,
                    failure.call.id,
                    failure.error,
                )
                raise failure.error from None
            yield ToolStreamFailed(
                failure.call.name,
                failure.call.id,
                ToolExecutionError("tool execution failed"),
            )
            sanitized_failure = True
        except BaseException:
            if not waiting_handoff:
                self._rollback(assistant_id, appended_result_ids)
            raise

        if sanitized_failure:
            raise ToolExecutionError("tool execution failed") from None

        for call, _result, message_id, cap_event in committed_results:
            if cap_event is not None:
                self.emit(cap_event)
            self.emit(ToolResultAppendedEvent(
                tool_name=call.name,
                tool_call_id=call.id,
                message_id=message_id,
                **self.event_context,
            ))
        for call, result, _message_id, _cap_event in committed_results:
            yield ToolStreamCompleted(call.name, call.id, result.content)
            skill_event = skill_loaded_event(call, result)
            if skill_event is not None:
                yield skill_event
            yield StatusUpdate(
                "tool_result",
                f"已读取 `{call.name}` 的结果，继续推理。",
                call.id,
            )

    def _rollback(self, assistant_id: str, result_ids: list[str]) -> None:
        self.messages.active_window.remove_refs(
            [assistant_id, *result_ids],
            self.messages.store,
        )


__all__ = ["ToolBatchRunner"]
