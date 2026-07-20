from threading import get_ident

from agentos.capabilities import (
    InProcessExecutionBackend,
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolCompensationContext,
    ToolCompensationInvocation,
    ToolInvocation,
    ToolInvocationContext,
    ToolRegistry,
)
from agentos.capabilities.executor import ToolExecutor
from agentos.capabilities.mcp import MCPToolAdapter
from agentos.observability.instrumented_tools import InstrumentedToolCallRouter
from agentos.policies import ResourcePolicy, SecurityPolicy
from tests.planning._async import async_test


def _invocation() -> ToolInvocation:
    return ToolInvocation(
        "lookup",
        {"query": "drawing"},
        ToolInvocationContext(
            invocation_id="invocation_ea91f27fdf596bceb7c90d2578c5e988",
            operation_id="operation_d340f3861e0c6a7eefbaf707fdc69d3d",
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            tool_call_id="call_1",
            attempt=1,
        ),
    )


def _executor(handler) -> ToolExecutor:  # type: ignore[no-untyped-def]
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            "lookup",
            "Lookup.",
            {"type": "object"},
            handler,
            SideEffectPolicy.PURE,
        ),
    )
    return ToolExecutor(registry, SecurityPolicy())


@async_test
async def test_async_execution_passes_only_tool_invocation_to_handler() -> None:
    received = []

    async def handler(invocation: ToolInvocation) -> str:
        received.append(invocation)
        return "found"

    invocation = _invocation()
    result = await _executor(handler).execute(invocation)

    assert received == [invocation]
    assert result.tool_call_id == "call_1"
    assert result.content == "found"


@async_test
async def test_sync_handler_is_offloaded_only_inside_backend() -> None:
    caller_thread = get_ident()
    handler_threads = []

    def handler(_invocation: ToolInvocation) -> str:
        handler_threads.append(get_ident())
        return "found"

    backend = InProcessExecutionBackend()
    tool = RegisteredTool(
        "lookup",
        "Lookup.",
        {"type": "object"},
        handler,
        SideEffectPolicy.PURE,
    )
    result = await backend.execute(
        tool,
        _invocation(),
        resource_policy=ResourcePolicy(),
    )

    assert result == "found"
    assert handler_threads and handler_threads[0] != caller_thread


@async_test
async def test_sync_compensation_is_offloaded_with_typed_invocation() -> None:
    caller_thread = get_ident()
    received = []
    handler_threads = []

    def compensate(invocation: ToolCompensationInvocation) -> None:
        received.append(invocation)
        handler_threads.append(get_ident())

    tool = RegisteredTool(
        "lookup",
        "Lookup.",
        {"type": "object"},
        lambda _invocation: "found",
        SideEffectPolicy.COMPENSATABLE,
        compensation_handler=compensate,
    )
    invocation = ToolCompensationInvocation(
        {"query": "drawing"},
        ToolCompensationContext(
            original_operation_id="operation_d340f3861e0c6a7eefbaf707fdc69d3d",
            compensation_operation_id=(
                "operation_00000000000000000000000000000000"
            ),
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            invocation_id="invocation_ea91f27fdf596bceb7c90d2578c5e988",
            attempt=1,
        ),
        None,
    )

    await InProcessExecutionBackend().execute_compensation(
        tool,
        invocation,
        resource_policy=ResourcePolicy(),
    )

    assert received == [invocation]
    assert handler_threads and handler_threads[0] != caller_thread


@async_test
async def test_executor_routes_only_declared_compensation_handler() -> None:
    received = []

    async def compensate(invocation: ToolCompensationInvocation) -> None:
        received.append(invocation)

    registry = ToolRegistry()
    tool = RegisteredTool(
        "lookup",
        "Lookup.",
        {"type": "object"},
        lambda _invocation: "found",
        SideEffectPolicy.COMPENSATABLE,
        compensation_handler=compensate,
    )
    registry.register(tool)
    invocation = ToolCompensationInvocation(
        {"query": "drawing"},
        ToolCompensationContext(
            original_operation_id="operation_d340f3861e0c6a7eefbaf707fdc69d3d",
            compensation_operation_id=(
                "operation_00000000000000000000000000000000"
            ),
            tenant_id=None,
            session_id="session_1",
            run_id="run_1",
            turn_id="turn_1",
            invocation_id="invocation_ea91f27fdf596bceb7c90d2578c5e988",
            attempt=1,
        ),
        None,
    )

    await ToolExecutor(registry, SecurityPolicy()).execute_compensation(
        tool,
        invocation,
    )

    assert received == [invocation]


def test_legacy_tool_execution_methods_are_absent() -> None:
    assert not hasattr(ToolCallRouter, "execute_tool_call")
    assert not hasattr(ToolCallRouter, "async_execute_tool_call")
    assert not hasattr(ToolExecutor, "async_execute")
    assert not hasattr(InProcessExecutionBackend, "run")
    assert not hasattr(InProcessExecutionBackend, "async_run")
    assert not hasattr(MCPToolAdapter, "execute")
    assert not hasattr(InstrumentedToolCallRouter, "execute_tool_call")
    assert not hasattr(InstrumentedToolCallRouter, "async_execute_tool_call")
