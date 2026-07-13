from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import cast

from agentos.capabilities.executor import ToolExecutionResult
from agentos.providers import ProviderToolCall
from agentos.runtime._async_bridge import _await_cleanup_preserving_cancellation


class ToolConcurrencyPolicy(str, Enum):
    """Level 1 工具调用的临时私有并发策略。"""

    EXCLUSIVE = "exclusive"
    PARALLEL_SAFE = "parallel_safe"


@dataclass(frozen=True, slots=True)
class ScheduledToolCallResult:
    """保留 Provider 原始索引的工具调用结果。"""

    index: int
    tool_call: ProviderToolCall
    result: ToolExecutionResult


def _raise_if_cancelling() -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling() > 0:
        raise asyncio.CancelledError


@dataclass(slots=True)
class ToolSchedulerCandidate:
    """验证有界 FIFO 与独占屏障语义的私有调度候选。"""

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
        policy_for: Callable[[ProviderToolCall], ToolConcurrencyPolicy],
        execute: Callable[[ProviderToolCall], Awaitable[ToolExecutionResult]],
    ) -> tuple[ScheduledToolCallResult, ...]:
        """按 Provider 顺序执行一个完整工具调用批次。"""

        _raise_if_cancelling()
        results: list[ScheduledToolCallResult | None] = [None] * len(calls)
        policies = tuple(self._policy_for(call, policy_for) for call in calls)
        _raise_if_cancelling()

        async def execute_one(index: int) -> ScheduledToolCallResult:
            call = calls[index]
            return ScheduledToolCallResult(index, call, await execute(call))

        async def execute_parallel_segment(segment: list[int]) -> None:
            pending: dict[asyncio.Task[ScheduledToolCallResult], int] = {}
            next_offset = 0

            def fill_available_slots() -> None:
                nonlocal next_offset
                _raise_if_cancelling()
                while (
                    len(pending) < self.max_parallel_calls
                    and next_offset < len(segment)
                ):
                    item_index = segment[next_offset]
                    next_offset += 1
                    submitted = asyncio.create_task(execute_one(item_index))
                    pending[submitted] = item_index

            async def cancel_and_drain() -> None:
                for submitted in pending:
                    submitted.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            fill_available_slots()
            try:
                while pending:
                    done, _ = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    first_error: BaseException | None = None
                    for submitted in sorted(done, key=pending.__getitem__):
                        item_index = pending.pop(submitted)
                        try:
                            results[item_index] = submitted.result()
                        except BaseException as error:
                            if first_error is None:
                                first_error = error
                    if first_error is not None:
                        raise first_error
                    fill_available_slots()
            except BaseException:
                await _await_cleanup_preserving_cancellation(cancel_and_drain)
                _raise_if_cancelling()
                raise

        index = 0
        while index < len(calls):
            if policies[index] is ToolConcurrencyPolicy.EXCLUSIVE:
                _raise_if_cancelling()
                result = await execute_one(index)
                _raise_if_cancelling()
                results[index] = result
                index += 1
                continue

            segment: list[int] = []
            while (
                index < len(calls)
                and policies[index] is ToolConcurrencyPolicy.PARALLEL_SAFE
            ):
                segment.append(index)
                index += 1
            await execute_parallel_segment(segment)

        return tuple(cast(ScheduledToolCallResult, item) for item in results)

    @staticmethod
    def _policy_for(
        call: ProviderToolCall,
        policy_for: Callable[[ProviderToolCall], ToolConcurrencyPolicy],
    ) -> ToolConcurrencyPolicy:
        policy = policy_for(call)
        if policy is ToolConcurrencyPolicy.PARALLEL_SAFE:
            return policy
        return ToolConcurrencyPolicy.EXCLUSIVE
