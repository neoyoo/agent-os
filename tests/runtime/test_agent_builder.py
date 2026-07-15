import asyncio

import pytest

from agentos import Agent, AgentBuilder
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.compression import CompressionRuntime, RuleBasedCompressor
from agentos.context import ContextRenderer, ContextRuntime
from agentos.context.models import SystemEnvelope
from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_NAMES
from agentos.messages import MessageRuntime
from agentos.policies import BudgetPolicy, TokenBudgetPolicy
from agentos.providers import FakeProvider
from agentos.providers import ProviderResponse, ProviderToolCall
from agentos.providers import provider_tool_spec_to_dict
from agentos.runtime import EventBus, QueryLoop, TurnStartedEvent


class StructuralContextRendererStub:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> SystemEnvelope:
        return SystemEnvelope(text=self.text)


def test_agent_builder_creates_runnable_standard_agent() -> None:
    provider = FakeProvider(["Built response."])

    agent = AgentBuilder().provider(provider).build()
    result = asyncio.run(agent.run("Build an agent."))

    assert isinstance(agent, Agent)
    assert result.content == "Built response."
    assert [message.kind for message in provider.requests[0].messages] == [
        "context_snapshot",
        "business_message",
    ]
    assert provider.requests[0].messages[1].content[0].text == "Build an agent."  # type: ignore[union-attr]


def test_agent_builder_wires_attachment_runtime() -> None:
    provider = FakeProvider(["ok"])

    agent = AgentBuilder().provider(provider).build()
    attachment = agent.attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    assert attachment.handle.startswith("att_")


def test_agent_builder_tools_are_available_only_through_provider_tools() -> None:
    tool = RegisteredTool(
        name="lookup_status",
        description="Lookup task status.",
        parameters={"type": "object", "properties": {}},
        handler=lambda arguments: "tool status: green",
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_lookup",
                        name="lookup_status",
                        arguments={},
                    ),
                ],
            ),
            "Tool result was handled.",
        ],
    )

    agent = AgentBuilder().provider(provider).tools([tool]).build()
    result = asyncio.run(agent.run("Use lookup_status."))

    assert result.content == "Tool result was handled."
    assert "lookup_status" not in provider.requests[0].system
    tool_result = provider.requests[1].messages[-1]
    assert (tool_result.role, tool_result.tool_call_id) == ("tool", "call_lookup")
    assert tool_result.content[0].text == "tool status: green"  # type: ignore[union-attr]
    assert any(
        provider_tool_spec_to_dict(spec).get("function", {}).get("name")
        == "lookup_status"
        for spec in provider.requests[0].tools
    )


def test_agent_builder_default_system_owns_only_required_sections() -> None:
    tool = RegisteredTool(
        name="tool_metadata_marker",
        description="Available metadata must remain outside SystemEnvelope.",
        parameters={"type": "object", "properties": {}},
        handler=lambda arguments: "unused",
    )
    provider = FakeProvider(["ok"])

    agent = AgentBuilder().provider(provider).tools([tool]).build()
    asyncio.run(agent.run("Inspect the default system wiring."))

    request = provider.requests[0]
    assert [
        line for line in request.system.splitlines() if line.startswith("# ")
    ] == [
        "# Runtime Contract",
        "# Interaction Protocol",
        "# Context Management Rules",
    ]
    assert "# Runtime Directives" not in request.system
    assert "# Trusted Skill:" not in request.system
    assert "# Workspace Contract" not in request.system
    assert "tool_metadata_marker" not in request.system
    assert {spec.function.name for spec in request.tools} >= {"tool_metadata_marker"}


def test_agent_builder_runs_async_tool_handler() -> None:
    async def async_lookup(arguments: dict[str, object]) -> str:
        await asyncio.sleep(0)
        return "async tool status: green"

    tool = RegisteredTool(
        name="async_lookup_status",
        description="Lookup task status asynchronously.",
        parameters={"type": "object", "properties": {}},
        handler=async_lookup,
    )
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_async_lookup",
                        name="async_lookup_status",
                        arguments={},
                    ),
                ],
            ),
            "Async tool result was handled.",
        ],
    )

    agent = AgentBuilder().provider(provider).tools([tool]).build()
    result = asyncio.run(agent.run("Use async_lookup_status."))

    assert result.content == "Async tool result was handled."
    assert type(agent.query_loop).__name__ == "QueryLoop"
    tool_result = provider.requests[1].messages[-1]
    assert (tool_result.role, tool_result.tool_call_id) == (
        "tool",
        "call_async_lookup",
    )
    assert tool_result.content[0].text == "async tool status: green"  # type: ignore[union-attr]


def test_agent_builder_exposes_only_the_unified_build_path() -> None:
    agent = AgentBuilder().provider(FakeProvider(["async only"])).build()

    assert isinstance(agent.query_loop, QueryLoop)
    assert asyncio.iscoroutinefunction(agent.run)


def test_agent_builder_default_path_includes_context_protocol_tools() -> None:
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_schema",
                        name="declare_schema",
                        arguments={
                            "fields": [
                                {
                                    "name": "task_goal",
                                    "type": "string",
                                    "purpose": "Current task goal.",
                                },
                            ],
                        },
                    ),
                ],
            ),
            "schema declared",
        ],
    )

    agent = AgentBuilder().provider(provider).build()
    result = asyncio.run(agent.run("Track this task."))

    tool_names = {spec.function.name for spec in provider.requests[0].tools}
    assert CONTEXT_PROTOCOL_TOOL_NAMES.issubset(tool_names)
    assert agent.query_loop.tool_call_router is not None
    assert agent.query_loop.tool_call_router.recall_runtime is not None
    assert result.content == "schema declared"


def test_agent_builder_wires_recall_context_to_compression_index() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )
    provider = FakeProvider(
        [
            "Captured first history.",
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_recall",
                        name="recall_context",
                        arguments={"handle": "seg_1"},
                    ),
                ],
            ),
            "recalled done",
        ],
    )

    agent = (
        AgentBuilder()
        .provider(provider)
        .context_runtime(context)
        .message_runtime(messages)
        .compression_runtime(compression)
        .build()
    )

    asyncio.run(agent.run("First detail"))
    result = asyncio.run(agent.run("Current task"))

    assert result.content == "recalled done"
    projected = provider.requests[2].messages
    assert [item.kind for item in projected[1:]] == [
        "recalled_message",
        "recalled_message",
        "business_message",
        "business_message",
        "tool_result",
    ]
    assert [
        item.content[0].text for item in projected[1:4]  # type: ignore[union-attr]
    ] == [
        "First detail",
        "Captured first history.",
        "Current task",
    ]
    tool_message = provider.requests[2].messages[-1]
    assert (tool_message.role, tool_message.tool_call_id) == (
        "tool",
        "call_recall",
    )
    tool_content = tool_message.content[0].text  # type: ignore[union-attr]
    assert '<recalled-context source="compressed_history" handle="seg_1">' in str(
        tool_content,
    )
    assert "First detail" in tool_content


def test_agent_builder_with_compression_creates_compression_runtime() -> None:
    agent = AgentBuilder().provider(FakeProvider(["ok"])).with_compression().build()

    assert isinstance(agent.query_loop.compression_runtime, CompressionRuntime)
    assert isinstance(
        agent.query_loop.compression_runtime.compressor,
        RuleBasedCompressor,
    )


def test_agent_builder_with_compression_can_use_token_budget_policy() -> None:
    agent = (
        AgentBuilder()
        .provider(FakeProvider(["ok"]))
        .with_compression(
            context_window=100,
            reserve_output_tokens=10,
            retain_latest_tokens=20,
            static_overhead_tokens=5,
        )
        .build()
    )

    assert agent.query_loop.compression_runtime is not None
    policy = agent.query_loop.compression_runtime.budget_policy
    assert isinstance(policy, TokenBudgetPolicy)
    assert policy.context_window == 100
    assert policy.reserve_output_tokens == 10
    assert policy.retain_latest_tokens == 20
    assert policy.static_overhead_tokens == 5


def test_agent_builder_uses_component_overrides() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    renderer = StructuralContextRendererStub("custom builder identity")
    bus = EventBus()

    assert not isinstance(renderer, ContextRenderer)

    agent = (
        AgentBuilder()
        .provider(FakeProvider(["override response"]))
        .context_runtime(context)
        .message_runtime(messages)
        .context_renderer(renderer)
        .event_bus(bus)
        .build()
    )
    result = asyncio.run(agent.run("Use overrides."))

    assert result.content == "override response"
    assert agent.query_loop.context_runtime is context
    assert agent.query_loop.message_runtime is messages
    assert agent.query_loop.event_bus is bus
    assert "custom builder identity" in agent.query_loop.provider.requests[0].system
    assert any(isinstance(event, TurnStartedEvent) for event in bus.events)


def test_agent_builder_accepts_compression_runtime_override() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
    )

    agent = (
        AgentBuilder()
        .provider(FakeProvider(["ok"]))
        .context_runtime(context)
        .message_runtime(messages)
        .compression_runtime(compression)
        .build()
    )

    assert agent.query_loop.compression_runtime is compression


def test_agent_builder_accepts_tool_call_router_override() -> None:
    context = ContextRuntime()
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="router_tool",
            description="Tool from router override.",
            parameters={"type": "object", "properties": {}},
            handler=lambda arguments: "router tool result",
        ),
    )
    router = ToolCallRouter(tool_registry=registry, context_runtime=context)
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        id="call_router",
                        name="router_tool",
                        arguments={},
                    ),
                ],
            ),
            "router done",
        ],
    )

    agent = (
        AgentBuilder()
        .provider(provider)
        .context_runtime(context)
        .tool_call_router(router)
        .build()
    )
    result = asyncio.run(agent.run("Use router_tool."))

    assert result.content == "router done"
    assert agent.query_loop.tool_call_router is router
    assert "router_tool" not in provider.requests[0].system
    assert provider.requests[1].messages[-1].content[0].text == "router tool result"  # type: ignore[union-attr]
    assert router.attachment_runtime is agent.attachments


def test_agent_builder_rejects_missing_provider_and_duplicate_provider() -> None:
    with pytest.raises(ValueError, match="requires .provider"):
        AgentBuilder().build()

    builder = AgentBuilder().provider(FakeProvider(["first"]))
    with pytest.raises(ValueError, match="provider\\(\\) called twice"):
        builder.provider(FakeProvider(["second"]))


def test_agent_builder_returns_independent_default_agents() -> None:
    builder = AgentBuilder().provider(FakeProvider(["one", "two"]))

    first = builder.build()
    second = builder.build()

    assert first is not second
    assert first.query_loop.message_runtime is not second.query_loop.message_runtime
    assert first.query_loop.context_runtime is not second.query_loop.context_runtime


def test_agent_builder_v1_does_not_expose_deferred_api() -> None:
    builder = AgentBuilder()

    assert not hasattr(builder, "system_prompt")
    assert not hasattr(builder, "with_memory")
    assert not hasattr(builder, "with_observability")
    assert not hasattr(builder, "hook_manager")
    assert not hasattr(builder, "with_hooks")
