import asyncio

import pytest

from agentos import Agent
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.context import ContextRuntime
from agentos.hooks import HookContext, HookManager, HookRegistry, HookResult
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderInputItem,
)
from agentos.runtime import (
    AgentResult,
    EventBus,
    ProviderRequestBuilder,
    QueryLoop,
    ToolExecutionCompletedEvent,
    ToolExecutionStartedEvent,
)
from tests._context_protocol_fixtures import default_context_renderer


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
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs() if router is not None else [],
        ),
        provider=provider,
        tool_call_router=router,
        hook_manager=hook_manager,
    )


def run_turn(loop: QueryLoop, content: str) -> str:
    outcome = asyncio.run(Agent(loop).run(content))
    assert isinstance(outcome, AgentResult)
    return outcome.content


def test_before_provider_call_hook_can_deny_provider_call() -> None:
    registry = HookRegistry()
    registry.register(
        "before_provider_call",
        lambda context: HookResult(action="deny", reason="blocked provider"),
    )
    provider = FakeProvider(["unused"])
    loop = build_loop(provider, HookManager(registry))

    with pytest.raises(RuntimeError, match="blocked provider"):
        run_turn(loop, "hello")

    assert provider.requests == []


def test_before_provider_call_hook_can_modify_request() -> None:
    registry = HookRegistry()

    def modify_request(context: HookContext) -> HookResult:
        return HookResult(
            action="modify",
            payload={
                "request": ProviderRequest(
                    system="modified system",
                    messages=[ProviderInputItem.business_user("modified user")],
                ),
            },
        )

    registry.register("before_provider_call", modify_request)
    provider = FakeProvider(["done"])
    loop = build_loop(provider, HookManager(registry))

    run_turn(loop, "hello")

    assert provider.requests[0].system == "modified system"
    assert provider.requests[0].messages == (
        ProviderInputItem.business_user("modified user"),
    )


def test_after_provider_call_hook_observes_response() -> None:
    observed: list[str] = []
    registry = HookRegistry()
    registry.register(
        "after_provider_call",
        lambda context: observed.append(context.payload["response"].content),  # type: ignore[attr-defined]
    )
    provider = FakeProvider(["done"])
    loop = build_loop(provider, HookManager(registry))

    run_turn(loop, "hello")

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

    result = run_turn(loop, "use lookup")

    second_request_messages = provider.requests[1].messages
    assert result == "final after denied tool"
    assert called == []
    tool_result = second_request_messages[-1]
    assert (tool_result.role, tool_result.tool_call_id) == ("tool", "call_lookup")
    assert tool_result.content[0].text == "tool call denied by hook: tool blocked"  # type: ignore[union-attr]


def test_tool_events_keep_original_index_after_immediate_hook_result() -> None:
    registry = HookRegistry()

    def deny_first(context: HookContext) -> HookResult:
        tool_call = context.payload["tool_call"]
        if isinstance(tool_call, ProviderToolCall) and tool_call.id == "immediate":
            return HookResult(action="deny", reason="handled immediately")
        return HookResult(action="allow")

    registry.register("before_tool_call", deny_first)
    context = ContextRuntime()
    messages = MessageRuntime()
    tools = ToolRegistry()
    tools.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _arguments: "scheduled result",
        ),
    )
    router = ToolCallRouter(tool_registry=tools, context_runtime=context)
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall("immediate", "lookup", {"key": "first"}),
                    ProviderToolCall("scheduled", "lookup", {"key": "second"}),
                ],
            ),
            "done",
        ],
    )
    event_bus = EventBus()
    loop = QueryLoop(
        context_runtime=context,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
        hook_manager=HookManager(registry),
        event_bus=event_bus,
    )

    assert run_turn(loop, "use tools") == "done"

    started = [
        event for event in event_bus.events if isinstance(event, ToolExecutionStartedEvent)
    ]
    completed = [
        event
        for event in event_bus.events
        if isinstance(event, ToolExecutionCompletedEvent)
    ]
    assert len(started) == len(completed) == 1
    assert started[0].tool_call_id == completed[0].tool_call_id == "scheduled"
    assert started[0].batch_index == completed[0].batch_index == 1
    assert started[0].batch_size == completed[0].batch_size == 2
    assert started[0].concurrency_policy == completed[0].concurrency_policy == "exclusive"
    assert started[0].max_parallel_calls == completed[0].max_parallel_calls == 8
    assert started[0].queue_wait_seconds is not None
    assert started[0].queue_wait_seconds >= 0
    assert not hasattr(started[0], "execution_duration_seconds")
    assert completed[0].execution_duration_seconds is not None
    assert completed[0].execution_duration_seconds >= 0


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

    run_turn(loop, "use lookup")

    assert observed == ["lookup result"]


def test_after_tool_call_hook_ignores_invalid_modified_result_type() -> None:
    registry = HookRegistry()
    registry.register(
        "after_tool_call",
        lambda _context: HookResult(
            action="modify",
            payload={"result": "invalid result"},
        ),
    )
    tool_registry = ToolRegistry()
    tool_registry.register(
        RegisteredTool(
            name="lookup",
            description="Lookup.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _arguments: "lookup result",
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

    assert run_turn(loop, "use lookup") == "final"
    tool_result = provider.requests[1].messages[-1]
    assert tool_result.role == "tool"
    assert tool_result.tool_call_id == "call_lookup"
    assert tool_result.content[0].text == "lookup result"  # type: ignore[union-attr]
