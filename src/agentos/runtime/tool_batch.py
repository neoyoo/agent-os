from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from functools import partial

from agentos.capabilities.executor import (
    ToolExecutionError,
    ToolExecutionOutcome,
    ToolExecutionResult,
)
from agentos.messages import ActiveWindow, MessageRuntime
from agentos.policies import SecurityPolicyError, ToolResultBudget
from agentos.providers import ProviderToolCall
from agentos.runtime._execution_control import WaitingCheckpointRequest
from agentos.runtime._tool_observation import ToolObservationMapper
from agentos.runtime.event_bus import AgentEvent, ToolResultAppendedEvent
from agentos.runtime.query_loop_hooks import QueryLoopHooks
from agentos.runtime.query_loop_support import (
    _ToolCallFailure,
    StructuredLoggerBoundary,
    ToolCallRouterBoundary,
    duplicate_tool_call_result,
    map_tool_result_cap,
    resolve_waiting_tool_batch,
    skill_loaded_event,
    tool_call_signature,
    waiting_peer_completion,
)
from agentos.runtime.stream_events import (
    StatusUpdate,
    ToolStreamCompleted,
    ToolStreamFailed,
    ToolStreamStarted,
    TurnStreamEvent,
)
from agentos.runtime.tool_scheduler import ToolCallScheduler, ToolExecutionContext
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
    logger: StructuredLoggerBoundary | None = None

    async def events(
        self,
        *,
        calls: tuple[ProviderToolCall, ...],
        applied_signatures: set[str],
        assistant_id: str,
    ) -> AsyncIterator[TurnStreamEvent | WaitingCheckpointRequest]:
        immediate: dict[str, ToolExecutionResult] = {}
        scheduled: list[tuple[int, ProviderToolCall]] = []
        appended_result_ids: list[str] = []
        started_calls: list[ProviderToolCall] = []
        started_events_emitted = False
        waiting_handoff = False
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
                result = duplicate_tool_call_result(call, applied_signatures)
                if result is None:
                    result = self.hooks.before_tool_call(call)
                    applied_signatures.add(tool_call_signature(call))
                if result is None:
                    scheduled.append((batch_index, call))
                else:
                    immediate[call.id] = result
                    started_calls.append(call)

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
                    return await self.router.async_execute_tool_call(call)
                except SecurityPolicyError as error:
                    raise _ToolCallFailure(call, error) from error
                except Exception as error:
                    raise _ToolCallFailure(
                        call,
                        error,
                        expose_error=False,
                    ) from error

            completed = await self.scheduler.execute_batch(
                calls=tuple(call for _, call in scheduled),
                batch_indexes=tuple(index for index, _ in scheduled),
                batch_size=len(calls),
                policy_for=self.router.concurrency_policy_for,
                execute=execute,
                on_completed=lambda item: self.emit(observation.completed(item)),
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
                        call,
                        result,
                        self.hooks.after_tool_call,
                        cap_result,
                    )
                    if cap_event is not None:
                        self.emit(cap_event)
                    yield completed_event
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
                )
                return

            for call in started_calls:
                yield ToolStreamStarted(call.name, call.id)
            started_events_emitted = True
            raw_results = immediate | {
                item.tool_call.id: item.result for item in completed
            }
            final_results = []
            for call in calls:
                result = self.hooks.after_tool_call(call, raw_results[call.id])
                capped = cap_result(call, result)
                final_results.append((call, capped.result, capped.event))
            committed_results = []
            for call, result, cap_event in final_results:
                stored = self.messages.append_tool_result(
                    result.tool_call_id,
                    result.content,
                )
                appended_result_ids.append(stored.id)
                committed_results.append((call, result, stored.id, cap_event))
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
