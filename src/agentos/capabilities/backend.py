from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
import inspect
from typing import Protocol

from agentos._sync_work import run_sync
from agentos.capabilities.invocation import (
    ToolCompensationInvocation,
    ToolInvocation,
)
from agentos.capabilities.tools import RegisteredTool, ToolHandlerResult
from agentos.policies.resource_policy import ResourcePolicy


class ExecutionBackend(Protocol):
    """工具 handler 的执行后端接缝。"""

    async def execute(
        self,
        tool: RegisteredTool,
        invocation: ToolInvocation,
        *,
        resource_policy: ResourcePolicy,
    ) -> ToolHandlerResult:
        """执行一个 canonical ToolInvocation。"""
        ...

    async def execute_compensation(
        self,
        tool: RegisteredTool,
        invocation: ToolCompensationInvocation,
        *,
        resource_policy: ResourcePolicy,
    ) -> None:
        """执行一个 canonical ToolCompensationInvocation。"""
        ...


@dataclass(slots=True)
class InProcessExecutionBackend:
    """默认 in-process 后端，复用当前工具 handler 调用语义。"""

    async def execute(
        self,
        tool: RegisteredTool,
        invocation: ToolInvocation,
        *,
        resource_policy: ResourcePolicy,
    ) -> ToolHandlerResult:
        """异步执行 tool handler；同步 handler 放入线程。"""

        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(invocation)
        result = await run_sync(
            _run_sync_handler,
            tool,
            invocation,
        )
        return await result if inspect.isawaitable(result) else result

    async def execute_compensation(
        self,
        tool: RegisteredTool,
        invocation: ToolCompensationInvocation,
        *,
        resource_policy: ResourcePolicy,
    ) -> None:
        """异步执行补偿 handler；同步实现放入线程。"""

        handler = tool.compensation_handler
        if handler is None:
            raise RuntimeError("tool does not declare a compensation handler")
        if inspect.iscoroutinefunction(handler):
            await handler(invocation)
            return
        result = await run_sync(_run_sync_compensation_handler, tool, invocation)
        if inspect.isawaitable(result):
            await result


def _run_sync_handler(
    tool: RegisteredTool,
    invocation: ToolInvocation,
) -> ToolHandlerResult | Awaitable[ToolHandlerResult]:
    return tool.handler(invocation)


def _run_sync_compensation_handler(
    tool: RegisteredTool,
    invocation: ToolCompensationInvocation,
) -> None | Awaitable[None]:
    handler = tool.compensation_handler
    if handler is None:
        raise RuntimeError("tool does not declare a compensation handler")
    return handler(invocation)
