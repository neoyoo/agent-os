from __future__ import annotations

from dataclasses import dataclass

from agentos.events import ToolExecutionCompletedEvent, ToolExecutionStartedEvent
from agentos.providers import ProviderToolCall
from agentos.runtime.tool_scheduler import (
    ScheduledToolCallResult,
    ToolExecutionContext,
)


@dataclass(frozen=True, slots=True)
class ToolObservationMapper:
    """把 Scheduler 元数据映射为 QueryLoop 可发布的 typed event。"""

    session_id: str | None
    turn_id: str | None

    def started(
        self,
        call: ProviderToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionStartedEvent:
        """映射开始执行时已知的调度元数据。"""

        return ToolExecutionStartedEvent(
            tool_name=call.name,
            tool_call_id=call.id,
            batch_index=context.batch_index,
            concurrency_policy=context.concurrency_policy.value,
            queue_wait_seconds=context.queue_wait_seconds,
            max_parallel_calls=context.max_parallel_calls,
            batch_size=context.batch_size,
            session_id=self.session_id,
            turn_id=self.turn_id,
        )

    def completed(
        self,
        completed: ScheduledToolCallResult,
    ) -> ToolExecutionCompletedEvent:
        """映射完成结果中的调度元数据。"""

        context = completed.context
        duration = completed.execution_duration_seconds
        if context is None or duration is None:
            raise RuntimeError("scheduled tool result is missing observation metadata")
        return ToolExecutionCompletedEvent(
            tool_name=completed.tool_call.name,
            tool_call_id=completed.tool_call.id,
            batch_index=context.batch_index,
            concurrency_policy=context.concurrency_policy.value,
            queue_wait_seconds=context.queue_wait_seconds,
            max_parallel_calls=context.max_parallel_calls,
            batch_size=context.batch_size,
            execution_duration_seconds=duration,
            session_id=self.session_id,
            turn_id=self.turn_id,
        )
