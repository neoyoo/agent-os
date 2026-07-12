import asyncio
from pathlib import Path

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.mcp import (
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
from agentos.capabilities.skills import (
    FileSystemSkillSource,
    SkillRegistry,
    register_skill_loader_tools,
)
from agentos.context import ContextRuntime
from agentos.messages import MessageRuntime
from agentos.providers import (
    FakeProvider,
    ProviderResponse,
    ProviderToolCall,
)
from agentos.runtime import AsyncQueryLoop, ProviderRequestBuilder, QueryLoop
from tests._context_protocol_fixtures import default_context_renderer


class FakeMCPClient:
    def list_tools(self) -> list[MCPToolInfo]:
        return [
            MCPToolInfo(
                name="lookup",
                description="Lookup a value.",
                input_schema={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            ),
        ]

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> str:
        return f"{tool_name}:{arguments['key']}"


def test_query_loop_loads_skill_body_through_tool_result(tmp_path: Path) -> None:
    (tmp_path / "review.md").write_text(
        (
            "---\n"
            "name: code-review\n"
            "description: Review code.\n"
            "when_to_use: 审查代码时使用。\n"
            "---\n"
            "# Review Body\n"
            "Find bugs first.\n"
        ),
        encoding="utf-8",
    )
    async def load_registry() -> SkillRegistry:
        return await SkillRegistry.aload(FileSystemSkillSource([tmp_path]))

    skill_registry = asyncio.run(load_registry())
    tool_registry = ToolRegistry()
    register_skill_loader_tools(tool_registry, skill_registry)
    messages = MessageRuntime()
    router = ToolCallRouter(tool_registry=tool_registry)
    provider = FakeProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[
                    ProviderToolCall(
                        id="call_skill",
                        name="load_skill",
                        arguments={"skill_name": "code-review"},
                    ),
                ],
            ),
            "I will follow the review skill.",
        ],
    )
    context_runtime = ContextRuntime()
    loop = AsyncQueryLoop(
        context_runtime=context_runtime,
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
    )

    response = asyncio.run(loop.run_turn("Review this code"))

    assert response == "I will follow the review skill."
    tool_names = {tool.function.name for tool in provider.requests[0].tools}
    assert "load_skill" in tool_names
    assert "code-review" not in provider.requests[0].system
    tool_result = provider.requests[1].messages[-1]
    assert tool_result.role == "tool"
    assert "# Review Body" in tool_result.content[0].text  # type: ignore[union-attr]
    assert "# Review Body" not in provider.requests[0].system


def test_query_loop_executes_mcp_tool_call() -> None:
    mcp_registry = MCPRegistry()
    mcp_registry.register(
        MCPServerRegistration(
            name="docs",
            description="Documentation lookup.",
            client=FakeMCPClient(),
        ),
    )
    mcp_registry.refresh()
    mcp_adapter = MCPToolAdapter(mcp_registry)
    router = ToolCallRouter(tool_registry=ToolRegistry(), mcp_adapter=mcp_adapter)
    messages = MessageRuntime()
    provider = FakeProvider(
        [
            ProviderResponse(
                content="",
                tool_calls=[
                    ProviderToolCall(
                        id="call_mcp",
                        name="mcp__docs__lookup",
                        arguments={"key": "phase5"},
                    ),
                ],
            ),
            "MCP result consumed.",
        ],
    )
    loop = QueryLoop(
        context_runtime=ContextRuntime(),
        message_runtime=messages,
        request_builder=ProviderRequestBuilder(
            context_renderer=default_context_renderer(),
            message_runtime=messages,
            tools=router.tool_specs(),
        ),
        provider=provider,
        tool_call_router=router,
    )

    response = loop.run_turn("Lookup docs")

    assert response == "MCP result consumed."
    tool_names = {tool.function.name for tool in provider.requests[0].tools}
    assert "mcp__docs__lookup" in tool_names
    assert "docs" not in provider.requests[0].system
    tool_result = provider.requests[1].messages[-1]
    assert tool_result.role == "tool"
    assert tool_result.content[0].text == "lookup:phase5"  # type: ignore[union-attr]
