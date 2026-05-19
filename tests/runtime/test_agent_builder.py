import pytest

from agentos import Agent, AgentBuilder
from agentos.capabilities import RegisteredTool, ToolCallRouter, ToolRegistry
from agentos.compression import CompressionRuntime, RuleBasedCompressor
from agentos.context import ContextRenderer, ContextRuntime, RuntimeContract
from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_NAMES
from agentos.messages import MessageRuntime
from agentos.policies import BudgetPolicy
from agentos.providers import FakeProvider
from agentos.providers import ProviderResponse, ProviderToolCall
from agentos.providers import provider_message_to_dict
from agentos.providers import provider_tool_spec_to_dict
from agentos.runtime import EventBus, TurnStartedEvent


def test_agent_builder_creates_runnable_standard_agent() -> None:
    provider = FakeProvider(["Built response."])

    agent = AgentBuilder().provider(provider).build()
    result = agent.run("Build an agent.")

    assert isinstance(agent, Agent)
    assert result.content == "Built response."
    assert [provider_message_to_dict(message) for message in provider.requests[0].messages] == [
        {"role": "user", "content": "Build an agent."},
    ]


def test_agent_builder_wires_attachment_runtime() -> None:
    provider = FakeProvider(["ok"])

    agent = AgentBuilder().provider(provider).build()
    attachment = agent.attachments.upload_bytes(
        b"image-bytes",
        filename="diagram.png",
        mime_type="image/png",
    )

    assert attachment.handle.startswith("att_")


def test_agent_builder_tools_are_visible_and_executable() -> None:
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
    result = agent.run("Use lookup_status.")

    assert result.content == "Tool result was handled."
    assert "lookup_status" in provider.requests[0].system
    assert provider_message_to_dict(provider.requests[1].messages[2]) == {
        "role": "tool",
        "content": "tool status: green",
        "tool_call_id": "call_lookup",
    }
    assert any(
        provider_tool_spec_to_dict(spec).get("function", {}).get("name")
        == "lookup_status"
        for spec in provider.requests[0].tools
    )


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
                                    "type": "str",
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
    result = agent.run("Track this task.")

    tool_names = {spec.function.name for spec in provider.requests[0].tools}
    assert CONTEXT_PROTOCOL_TOOL_NAMES.issubset(tool_names)
    assert "load_image" in tool_names
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

    agent.run("First detail")
    result = agent.run("Current task")

    assert result.content == "recalled done"
    assert "First detail" not in str(
        [provider_message_to_dict(message) for message in provider.requests[2].messages[:-1]],
    )
    assert provider_message_to_dict(provider.requests[2].messages[-1]) == {
        "role": "tool",
        "content": (
            '<recalled-context source="compressed_history" handle="seg_1">\n'
            '  <message role="user" id="msg_1">\n'
            "    First detail\n"
            "  </message>\n"
            '  <message role="assistant" id="msg_2">\n'
            "    Captured first history.\n"
            "  </message>\n"
            "</recalled-context>"
        ),
        "tool_call_id": "call_recall",
    }


def test_agent_builder_with_compression_creates_compression_runtime() -> None:
    agent = AgentBuilder().provider(FakeProvider(["ok"])).with_compression().build()

    assert isinstance(agent.query_loop.compression_runtime, CompressionRuntime)
    assert isinstance(
        agent.query_loop.compression_runtime.compressor,
        RuleBasedCompressor,
    )


def test_agent_builder_uses_component_overrides() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    renderer = ContextRenderer(
        runtime_contract=RuntimeContract(identity="custom builder identity"),
    )
    bus = EventBus()

    agent = (
        AgentBuilder()
        .provider(FakeProvider(["override response"]))
        .context_runtime(context)
        .message_runtime(messages)
        .context_renderer(renderer)
        .event_bus(bus)
        .build()
    )
    result = agent.run("Use overrides.")

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
    result = agent.run("Use router_tool.")

    assert result.content == "router done"
    assert agent.query_loop.tool_call_router is router
    assert "router_tool" in provider.requests[0].system
    assert (
        provider_message_to_dict(provider.requests[1].messages[2])["content"]
        == "router tool result"
    )
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
