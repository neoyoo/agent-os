from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import cast

from agentos._waiting import WaitRequest
from agentos.capabilities.executor import ToolExecutionOutcome, ToolExecutionResult
from agentos.capabilities.tools import ToolConcurrencyPolicy
from agentos.providers import ProviderToolCall
from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """工具开始执行时已知的不可变批次元数据。"""

    batch_index: int
    concurrency_policy: ToolConcurrencyPolicy
    queue_wait_seconds: float
    max_parallel_calls: int
    batch_size: int


@dataclass(frozen=True, slots=True)
class ScheduledToolCallResult:
    """保留 Provider 原始索引的工具调用结果。"""

    index: int
    tool_call: ProviderToolCall
    result: ToolExecutionOutcome
    context: ToolExecutionContext | None = None
    execution_duration_seconds: float | None = None


def _raise_if_cancelling() -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling() > 0:
        raise asyncio.CancelledError


@dataclass(slots=True)
class ToolCallScheduler:
    """执行有界 FIFO 工具批次并维护独占屏障。"""

    max_parallel_calls: int = 8

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_parallel_calls, int)
            or isinstance(self.max_parallel_calls, bool)
            or self.max_parallel_calls < 1
        ):
            raise ValueError(
                "max_parallel_calls must be an integer greater than zero",
            )

    async def execute_batch(
        self,
        *,
        calls: tuple[ProviderToolCall, ...],
        batch_indexes: tuple[int, ...] | None = None,
        batch_size: int | None = None,
        policy_for: Callable[[ProviderToolCall], ToolConcurrencyPolicy],
        execute: Callable[
            [ProviderToolCall, ToolExecutionContext],
            Awaitable[ToolExecutionOutcome],
        ],
        on_completed: Callable[[ScheduledToolCallResult], None] | None = None,
    ) -> tuple[ScheduledToolCallResult, ...]:
        """按 Provider 顺序返回完整、已收敛的批次结果。"""

        _raise_if_cancelling()
        results: list[ScheduledToolCallResult | None] = [None] * len(calls)
        policies = tuple(self._policy_for(call, policy_for) for call in calls)
        indexes = batch_indexes or tuple(range(len(calls)))
        if len(indexes) != len(calls):
            raise ValueError("batch_indexes must match calls")
        effective_batch_size = len(calls) if batch_size is None else batch_size
        queued_at = monotonic()

        async def execute_one(position: int) -> ScheduledToolCallResult:
            call = calls[position]
            started_at = monotonic()
            context = ToolExecutionContext(
                batch_index=indexes[position],
                concurrency_policy=policies[position],
                queue_wait_seconds=max(0.0, started_at - queued_at),
                max_parallel_calls=self.max_parallel_calls,
                batch_size=effective_batch_size,
            )
            result = await execute(call, context)
            completed = ScheduledToolCallResult(
                indexes[position],
                call,
                result,
                context,
                max(0.0, monotonic() - started_at),
            )
            if on_completed is not None and isinstance(result, ToolExecutionResult):
                on_completed(completed)
            return completed

        async def execute_parallel_segment(segment: list[int]) -> bool:
            pending: dict[asyncio.Task[ScheduledToolCallResult], int] = {}
            next_offset = 0
            waiting_requested = False

            def fill_available_slots() -> None:
                nonlocal next_offset
                _raise_if_cancelling()
                while (
                    not waiting_requested
                    and len(pending) < self.max_parallel_calls
                    and next_offset < len(segment)
                ):
                    item_index = segment[next_offset]
                    next_offset += 1
                    pending[asyncio.create_task(execute_one(item_index))] = item_index

            async def cancel_and_drain() -> None:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            fill_available_slots()
            try:
                while pending:
                    done, _ = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    first_error: BaseException | None = None
                    for task in sorted(done, key=pending.__getitem__):
                        item_index = pending.pop(task)
                        try:
                            completed = task.result()
                            results[item_index] = completed
                            if isinstance(completed.result, WaitRequest):
                                waiting_requested = True
                        except BaseException as error:
                            first_error = first_error or error
                    if first_error is not None:
                        raise first_error
                    fill_available_slots()
            except BaseException as error:
                await _await_cleanup_preserving_cancellation(cancel_and_drain)
                if isinstance(error, asyncio.CancelledError):
                    raise
                _raise_if_cancelling()
                raise
            return waiting_requested

        index = 0
        while index < len(calls):
            if policies[index] is ToolConcurrencyPolicy.EXCLUSIVE:
                completed = await execute_one(index)
                results[index] = completed
                _raise_if_cancelling()
                if isinstance(completed.result, WaitRequest):
                    break
                index += 1
                continue
            segment: list[int] = []
            while (
                index < len(calls)
                and policies[index] is ToolConcurrencyPolicy.PARALLEL_SAFE
            ):
                segment.append(index)
                index += 1
            if await execute_parallel_segment(segment):
                break
        return tuple(
            cast(ScheduledToolCallResult, item)
            for item in results
            if item is not None
        )

    @staticmethod
    def _policy_for(
        call: ProviderToolCall,
        policy_for: Callable[[ProviderToolCall], ToolConcurrencyPolicy],
    ) -> ToolConcurrencyPolicy:
        policy = policy_for(call)
        if policy is ToolConcurrencyPolicy.PARALLEL_SAFE:
            return policy
        return ToolConcurrencyPolicy.EXCLUSIVE
