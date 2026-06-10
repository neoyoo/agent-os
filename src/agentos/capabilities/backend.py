from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from typing import Protocol

from agentos.capabilities.tools import RegisteredTool
from agentos.policies.resource_policy import ResourcePolicy


class ExecutionBackend(Protocol):
    """工具 handler 的执行后端接缝。"""

    def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        """同步执行 tool handler。"""
        ...

    async def async_run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        """异步执行 tool handler。"""
        ...


@dataclass(slots=True)
class InProcessExecutionBackend:
    """默认 in-process 后端，复用当前工具 handler 调用语义。"""

    def run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        """同步执行 tool handler；ResourcePolicy 由未来沙箱后端消费。"""

        content = tool.handler(arguments)
        if inspect.isawaitable(content):
            close = getattr(content, "close", None)
            if callable(close):
                close()
            raise RuntimeError("async handler requires AsyncQueryLoop")
        return content

    async def async_run(
        self,
        tool: RegisteredTool,
        arguments: dict[str, object],
        *,
        resource_policy: ResourcePolicy,
    ) -> str:
        """异步执行 tool handler；同步 handler 放入线程。"""

        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(arguments)
        return await asyncio.to_thread(
            self.run,
            tool,
            arguments,
            resource_policy=resource_policy,
        )
