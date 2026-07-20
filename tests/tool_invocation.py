from __future__ import annotations

import inspect

from agentos.capabilities import (
    RegisteredTool,
    ToolHandlerResult,
    ToolInvocation,
    ToolInvocationContext,
)


def make_tool_invocation(
    tool_name: str,
    arguments: dict[str, object],
) -> ToolInvocation:
    return ToolInvocation(
        tool_name,
        arguments,
        ToolInvocationContext(
            invocation_id=f"invocation_{'0' * 32}",
            operation_id=f"operation_{'1' * 32}",
            tenant_id=None,
            session_id="session_test",
            run_id="run_test",
            turn_id="turn_test",
            tool_call_id=f"call_{tool_name}",
            attempt=1,
        ),
    )


async def call_tool(
    tool: RegisteredTool,
    arguments: dict[str, object],
) -> ToolHandlerResult:
    result = tool.handler(make_tool_invocation(tool.name, arguments))
    if inspect.isawaitable(result):
        return await result
    return result


def call_sync_tool(
    tool: RegisteredTool,
    arguments: dict[str, object],
) -> ToolHandlerResult:
    result = tool.handler(make_tool_invocation(tool.name, arguments))
    if inspect.isawaitable(result):
        raise AssertionError("expected a synchronous tool handler")
    return result


__all__ = ["call_sync_tool", "call_tool", "make_tool_invocation"]
