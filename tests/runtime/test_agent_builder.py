import asyncio
from typing import get_type_hints

import pytest

from agentos import Agent, AgentBuilder
from agentos.artifacts import ArtifactRuntime
from agentos.capabilities import (
    RegisteredTool,
    SideEffectPolicy,
    ToolCallRouter,
    ToolInvocation,
    ToolRegistry,
)
from agentos.compression import CompressionRuntime, RuleBasedCompressor
from agentos.context import ContextRenderer, ContextRuntime
from agentos.context.models import (
    ContextSlotProjection,
    ProjectionVariant,
    SystemEnvelope,
)
from agentos.context.xml import XmlElement
from agentos.context_protocol import CONTEXT_PROTOCOL_TOOL_NAMES
from agentos.messages import MessageRuntime
from agentos.persistence import (
    InMemoryDurableSessionStore,
    InMemoryHotSessionStore,
)
from agentos.policies import BudgetPolicy, TokenBudgetPolicy
from agentos.providers import FakeProvider
from agentos.providers import ProviderResponse, ProviderToolCall
from agentos.providers import provider_tool_spec_to_dict
from agentos.recall import InMemoryRecallIndex, SegmentRepository
from agentos.runtime import EventBus, QueryLoop, TurnStartedEvent
from agentos.runtime.run import UserTurnInput
from tests.tool_invocation import make_tool_invocation


class StructuralContextRendererStub:
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self) -> SystemEnvelope:
        return SystemEnvelope(text=self.text)


class NonRepositoryMemorySink:
    def record_compressed_segment(self, package: object) -> None:
        return None


class StaticProjectionProvider:
    def __init__(self, projection: ContextSlotProjection) -> None:
        self.projection = projection
        self.calls = 0

    def projections(self) -> tuple[ContextSlotProjection, ...]:
        self.calls += 1
        return (self.projection,)


def projection(
    slot: str,
    owner: str,
    element: XmlElement,
) -> ContextSlotProjection:
    return ContextSlotProjection(
        slot=slot,  # type: ignore[arg-type]
        owner=owner,
        variants=(ProjectionVariant(element),),
    )


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


def test_agent_builder_wires_session_scoped_artifact_runtime() -> None:
    async def scenario() -> None:
        provider = FakeProvider(["ok"])

        agent = AgentBuilder().provider(provider).build(session_id="session_local")
        artifact = await agent.artifacts.upload(
            data=b"image-bytes",
            filename="diagram.png",
            media_type="image/png",
        )

        assert isinstance(agent.artifacts, ArtifactRuntime)
        assert artifact.id.startswith("art_")
        assert agent.artifacts.session_id == "session_local"
        assert agent.query_loop.session_state is not None
        assert agent.query_loop.session_state.id == "session_local"
        assert agent.query_loop.context_runtime.session_id == "session_local"

    asyncio.run(scenario())


def test_agent_artifacts_property_has_domain_return_type() -> None:
    assert Agent.artifacts.fget is not None
    assert get_type_hints(Agent.artifacts.fget)["return"] is ArtifactRuntime


def test_agent_builder_projects_user_upload_and_clears_turn_mount() -> None:
    async def scenario() -> None:
        provider = FakeProvider(["图纸已分析"])
        agent = AgentBuilder().provider(provider).build(session_id="session_local")
        artifact = await agent.artifacts.upload(
            data=b"image-bytes",
            filename="diagram.png",
            media_type="image/png",
        )

        result = await agent.run(
            UserTurnInput(
                content="分析图纸",
                artifact_handles=(artifact.id,),
            )
        )

        assert result.content == "图纸已分析"
        assert [item.kind for item in provider.requests[0].messages] == [
            "context_snapshot",
            "business_message",
            "context_mount",
        ]
        stored_user = agent.query_loop.message_runtime.store.all()[0]
        assert stored_user.content == "分析图纸"
        assert stored_user.artifact_refs[0].artifact_id == artifact.id
        assert agent.artifacts.active_mounts() == ()

    asyncio.run(scenario())


def test_agent_builder_generates_isolated_default_session_ids() -> None:
    builder = AgentBuilder().provider(FakeProvider(["one", "two"]))

    first = builder.build()
    second = builder.build()

    assert first.artifacts.session_id.startswith("session_")
    assert second.artifacts.session_id.startswith("session_")
    assert first.artifacts.session_id != second.artifacts.session_id


def test_agent_builder_rejects_explicit_empty_session_id() -> None:
    builder = AgentBuilder().provider(FakeProvider(["unused"]))

    with pytest.raises(ValueError, match="session_id must not be empty"):
        builder.build(session_id="")


def test_agent_builder_composes_extension_projection_providers() -> None:
    plan = StaticProjectionProvider(
        projection(
            "active-plan",
            "PlannerRuntime",
            XmlElement(
                "active-plan",
                (("status", "in-progress"),),
                children=(XmlElement("goal", text="ship phase 4"),),
            ),
        ),
    )
    memory = StaticProjectionProvider(
        projection(
            "memory-context",
            "MemoryRuntime",
            XmlElement(
                "memory-context",
                children=(
                    XmlElement(
                        "memory",
                        (
                            ("handle", "mem_1"),
                            ("kind", "semantic"),
                            ("category", "preference"),
                            ("instructional", "false"),
                        ),
                        text="use Chinese",
                    ),
                ),
            ),
        ),
    )
    skills = StaticProjectionProvider(
        projection(
            "available-skills",
            "SkillRuntime",
            XmlElement(
                "available-skills",
                (("truncated", "false"),),
                children=(
                    XmlElement(
                        "skill",
                        (
                            ("name", "review"),
                            ("description", "review code"),
                            ("loadable", "true"),
                            ("trust", "trusted"),
                        ),
                    ),
                ),
            ),
        ),
    )
    provider = FakeProvider(["ok"])
    agent = (
        AgentBuilder()
        .provider(provider)
        .context_projections((skills, plan, memory))
        .build(session_id="session_local")
    )

    asyncio.run(agent.run("hello"))

    snapshot = provider.requests[0].messages[0].content[0].text  # type: ignore[union-attr]
    assert snapshot.index("<active-plan") < snapshot.index("<memory-context")
    assert snapshot.index("<memory-context") < snapshot.index("<available-skills")
    assert plan.calls == memory.calls == skills.calls == 1


def test_agent_builder_tools_are_available_only_through_provider_tools() -> None:
    tool = RegisteredTool(
        name="lookup_status",
        description="Lookup task status.",
        parameters={"type": "object", "properties": {}},
        handler=lambda _invocation: "tool status: green",
        side_effect_policy=SideEffectPolicy.PURE,
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
        handler=lambda _invocation: "unused",
        side_effect_policy=SideEffectPolicy.PURE,
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
    async def async_lookup(_invocation: ToolInvocation) -> str:
        await asyncio.sleep(0)
        return "async tool status: green"

    tool = RegisteredTool(
        name="async_lookup_status",
        description="Lookup task status asynchronously.",
        parameters={"type": "object", "properties": {}},
        handler=async_lookup,
        side_effect_policy=SideEffectPolicy.PURE,
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


def test_agent_builder_rejects_non_repository_compression_sink() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        memory_sink=NonRepositoryMemorySink(),
    )

    with pytest.raises(ValueError, match="memory_sink must be a SegmentRepository"):
        (
            AgentBuilder()
            .provider(FakeProvider(["ok"]))
            .context_runtime(context)
            .message_runtime(messages)
            .compression_runtime(compression)
            .build()
        )


def test_agent_builder_shares_configured_segment_repository_with_recall() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    repository = SegmentRepository(
        hot_store=InMemoryHotSessionStore(),
        durable_store=InMemoryDurableSessionStore(),
        recall_index=InMemoryRecallIndex(),
    )
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        memory_sink=repository,
        session_id="session_1",
    )

    agent = (
        AgentBuilder()
        .provider(FakeProvider(["ok"]))
        .context_runtime(context)
        .message_runtime(messages)
        .compression_runtime(compression)
        .build()
    )

    router = agent.query_loop.tool_call_router
    assert isinstance(router, ToolCallRouter)
    assert router.recall_runtime is not None
    assert router.recall_runtime.segment_repository is repository


def test_agent_builder_requires_session_for_configured_segment_repository() -> None:
    context = ContextRuntime()
    messages = MessageRuntime()
    repository = SegmentRepository(
        hot_store=InMemoryHotSessionStore(),
        durable_store=InMemoryDurableSessionStore(),
        recall_index=InMemoryRecallIndex(),
    )
    compression = CompressionRuntime(
        context_runtime=context,
        message_runtime=messages,
        budget_policy=BudgetPolicy(max_active_messages=2, retain_latest_messages=1),
        memory_sink=repository,
    )

    with pytest.raises(ValueError, match="session_id is required"):
        (
            AgentBuilder()
            .provider(FakeProvider(["ok"]))
            .context_runtime(context)
            .message_runtime(messages)
            .compression_runtime(compression)
            .build()
        )


def test_agent_builder_accepts_tool_call_router_override() -> None:
    async def scenario() -> None:
        context = ContextRuntime()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="router_tool",
                description="Tool from router override.",
                parameters={"type": "object", "properties": {}},
                handler=lambda _invocation: "router tool result",
                side_effect_policy=SideEffectPolicy.PURE,
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
        result = await agent.run("Use router_tool.")

        assert result.content == "router done"
        assert agent.query_loop.tool_call_router is router
        assert "router_tool" not in provider.requests[0].system
        assert provider.requests[1].messages[-1].content[0].text == "router tool result"  # type: ignore[union-attr]
        record = await agent.artifacts.upload(
            data=b"image",
            filename="drawing.png",
            media_type="image/png",
        )
        artifact_result = await router.execute(
            make_tool_invocation(
                "load_attachment",
                {"handle": record.id},
            ),
        )

        assert artifact_result.content.startswith("附件已挂载：")
        assert agent.artifacts.active_mounts()[0].artifact_id == record.id

    asyncio.run(scenario())


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
