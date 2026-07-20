import asyncio

from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolInvocation,
    ToolRegistry,
    WaitRequest,
)
from agentos.runtime import WaitReason
from tests.tool_invocation import make_tool_invocation


def test_execute_awaits_async_registered_tool() -> None:
    async def lookup(invocation: ToolInvocation) -> str:
        await asyncio.sleep(0)
        return f"value:{invocation.arguments['key']}"

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup a key.",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            handler=lookup,
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    async def run() -> object:
        return await router.execute(make_tool_invocation("lookup", {"key": "a"}))

    result = asyncio.run(run())

    assert result.content == "value:a"


def test_execute_awaits_async_callable_object() -> None:
    class Lookup:
        async def __call__(self, invocation: ToolInvocation) -> str:
            await asyncio.sleep(0)
            return f"value:{invocation.arguments['key']}"

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup a key.",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            handler=Lookup(),
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )

    result = asyncio.run(
        ToolCallRouter(tool_registry=registry).execute(
            make_tool_invocation("lookup", {"key": "a"}),
        ),
    )

    assert result.content == "value:a"


def test_execute_runs_sync_registered_tool() -> None:
    def lookup(_invocation: ToolInvocation) -> str:
        return "value"

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup a key.",
            parameters={"type": "object", "properties": {}},
            handler=lookup,
            side_effect_policy=SideEffectPolicy.PURE,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    result = asyncio.run(router.execute(make_tool_invocation("lookup", {})))

    assert result.content == "value"


def test_execute_preserves_typed_wait_request() -> None:
    reason = WaitReason("human_input", "approval_1")
    request = WaitRequest(reason)

    async def request_waiting(_invocation: ToolInvocation) -> WaitRequest:
        return request

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="request_waiting",
            description="Request authoritative waiting.",
            parameters={"type": "object", "properties": {}},
            handler=request_waiting,
            side_effect_policy=SideEffectPolicy.PURE,
            wait_capable=True,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    async def run() -> object:
        return await router.execute(make_tool_invocation("request_waiting", {}))

    assert asyncio.run(run()) is request
