import asyncio

from agentos.capabilities import (
    RegisteredTool,
    ToolCallRouter,
    ToolRegistry,
    WaitRequest,
)
from agentos.providers import ProviderToolCall
from agentos.runtime import WaitReason
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

    with pytest.raises(
        RuntimeError,
        match=r"async handler requires ExecutionBackend\.async_run",
    ):
        router.execute_tool_call(
            ProviderToolCall(id="call_1", name="lookup", arguments={}),
        )


def test_async_execute_tool_call_preserves_typed_wait_request() -> None:
    reason = WaitReason("human_input", "approval_1")
    request = WaitRequest(reason)

    async def request_waiting(_arguments: dict[str, object]) -> WaitRequest:
        return request

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="request_waiting",
            description="Request authoritative waiting.",
            parameters={"type": "object", "properties": {}},
            handler=request_waiting,
        ),
    )
    router = ToolCallRouter(tool_registry=registry)

    async def run() -> object:
        return await router.async_execute_tool_call(
            ProviderToolCall("call_1", "request_waiting", {}),
        )

    assert asyncio.run(run()) is request
