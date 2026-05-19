import asyncio

from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.providers import ProviderToolCall
import pytest


def test_async_execute_tool_call_awaits_async_registered_tool() -> None:
    async def lookup(arguments: dict[str, object]) -> str:
        await asyncio.sleep(0)
        return f"value:{arguments['key']}"

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
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    async def run() -> object:
        return await router.async_execute_tool_call(
            ProviderToolCall(id="call_1", name="lookup", arguments={"key": "a"}),
        )

    result = asyncio.run(run())

    assert result.content == "value:a"


def test_sync_execute_tool_call_rejects_async_registered_tool() -> None:
    async def lookup(arguments: dict[str, object]) -> str:
        return "value"

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup a key.",
            parameters={"type": "object", "properties": {}},
            handler=lookup,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    with pytest.raises(RuntimeError, match="async handler requires AsyncQueryLoop"):
        router.execute_tool_call(
            ProviderToolCall(id="call_1", name="lookup", arguments={}),
        )
