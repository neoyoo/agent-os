import pytest

from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRenderer, ContextRuntime
from agentos.hooks import HookContext, HookManager, HookRegistry, HookResult
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    UserMessage,
    provider_message_to_dict,
)
from agentos.runtime import ProviderRequestBuilder, QueryLoop


def build_loop(
    provider: FakeProvider,
    hook_manager: HookManager,
    *,
    router: ToolCallRouter | None = None,
) -> QueryLoop:
    context = ContextRuntime()
    messages = MessageRuntime()
    return QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=ContextRenderer(),
            message_runtime=messages,
            tools=router.tool_specs() if router is not None else [],
        ),
        provider=provider,
        tool_call_router=router,
        hook_manager=hook_manager,
    )


def test_before_provider_call_hook_can_deny_provider_call() -> None:
    registry = HookRegistry()
    registry.register(
        "before_provider_call",
        lambda context: HookResult(action="deny", reason="blocked provider"),
    )
    provider = FakeProvider(["unused"])
    loop = build_loop(provider, HookManager(registry))

    with pytest.raises(RuntimeError, match="blocked provider"):
        loop.run_turn("hello")

    assert provider.requests == []


def test_before_provider_call_hook_can_modify_request() -> None:
    registry = HookRegistry()

    def modify_request(context: HookContext) -> HookResult:
        return HookResult(
            action="modify",
            payload={
                "request": ProviderRequest(
                    system="modified system",
                    messages=[UserMessage(content="modified user")],
                ),
            },
        )

    registry.register("before_provider_call", modify_request)
    provider = FakeProvider(["done"])
    loop = build_loop(provider, HookManager(registry))

    loop.run_turn("hello")

    assert provider.requests[0].system == "modified system"
    assert provider.requests[0].messages == [UserMessage(content="modified user")]


def test_after_provider_call_hook_observes_response() -> None:
    observed: list[str] = []
    registry = HookRegistry()
    registry.register(
        "after_provider_call",
        lambda context: observed.append(context.payload["response"].content),  # type: ignore[attr-defined]
    )
    provider = FakeProvider(["done"])
    loop = build_loop(provider, HookManager(registry))

    loop.run_turn("hello")

    assert observed == ["done"]


def test_before_tool_call_hook_can_deny_tool_execution_and_write_result() -> None:
    called: list[dict[str, object]] = []
    registry = HookRegistry()
    registry.register(
        "before_tool_call",
        lambda context: HookResult(action="deny", reason="tool blocked"),
    )
    tool = RegisteredTool(
        name="lookup",
        description="Lookup.",
        parameters={"type": "object", "properties": {}},
        handler=lambda arguments: called.append(arguments) or "should not run",
    )
    tool_registry = ToolRegistry()
    tool_registry.register(tool)
    router = ToolCallRouter(tool_registry=tool_registry, context_runtime=ContextRuntime())
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(id="call_lookup", name="lookup", arguments={}),
                ],
            ),
            "final after denied tool",
        ],
    )
    loop = build_loop(provider, HookManager(registry), router=router)

    result = loop.run_turn("use lookup")

    second_request_messages = [
        provider_message_to_dict(message) for message in provider.requests[1].messages
    ]
    assert result == "final after denied tool"
    assert called == []
    assert second_request_messages[2] == {
        "role": "tool",
        "content": "tool call denied by hook: tool blocked",
        "tool_call_id": "call_lookup",
    }


def test_after_tool_call_hook_observes_result() -> None:
    observed: list[str] = []
    registry = HookRegistry()
    registry.register(
        "after_tool_call",
        lambda context: observed.append(context.payload["result"].content),  # type: ignore[attr-defined]
    )
    tool_registry = ToolRegistry()
    tool_registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "lookup result",
        ),
    )
    router = ToolCallRouter(tool_registry=tool_registry, context_runtime=ContextRuntime())
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(id="call_lookup", name="lookup", arguments={}),
                ],
            ),
            "final",
        ],
    )
    loop = build_loop(provider, HookManager(registry), router=router)

    loop.run_turn("use lookup")

    assert observed == ["lookup result"]
