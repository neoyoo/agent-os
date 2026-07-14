import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Literal

import pytest

from agentos import Agent
from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.mcp import (
    MCPRegistry,
    MCPServerRegistration,
    MCPToolAdapter,
    MCPToolInfo,
)
from agentos.capabilities.skills import (
    FileSystemSkillSource,
    SkillDefinition,
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
from agentos.runtime import ProviderRequestBuilder, QueryLoop
from agentos.runtime.errors import AgentBusyError
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
    loop = QueryLoop(
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

    response = asyncio.run(Agent(loop).run("Review this code"))

    assert response.content == "I will follow the review skill."
    tool_names = {tool.function.name for tool in provider.requests[0].tools}
    assert "load_skill" in tool_names
    assert "code-review" not in provider.requests[0].system
    tool_result = provider.requests[1].messages[-1]
    assert tool_result.role == "tool"
    assert "# Review Body" in tool_result.content[0].text  # type: ignore[union-attr]
    assert "# Review Body" not in provider.requests[0].system


def _assert_agent_close_waits_for_filesystem_skill_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_kind: Literal["discovery", "resource_listing", "resource_read"],
) -> None:
    skill_dir = tmp_path / "review"
    resource_path = skill_dir / "references" / "checklist.md"
    resource_path.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "name: code-review\n"
            "description: Review code.\n"
            "when_to_use: Review code changes.\n"
            "---\n"
            "# Review\n"
            "Find bugs first.\n"
        ),
        encoding="utf-8",
    )
    resource_path.write_text("# Checklist\n- Verify behavior.\n", encoding="utf-8")

    async def scenario() -> None:
        source = FileSystemSkillSource([tmp_path])
        skill_registry = await SkillRegistry.aload(source)
        worker_started = ThreadEvent()
        release_worker = ThreadEvent()
        worker_finished = ThreadEvent()

        if worker_kind == "discovery":
            original_discover_skills = source._discover_skills
            source._skills = None

            def blocking_discover_skills() -> dict[str, SkillDefinition]:
                worker_started.set()
                release_worker.wait()
                try:
                    return original_discover_skills()
                finally:
                    worker_finished.set()

            monkeypatch.setattr(source, "_discover_skills", blocking_discover_skills)
            tool_name = "load_skill"
            arguments = {"skill_name": "code-review"}
        elif worker_kind == "resource_listing":
            original_list_resources = source._list_skill_resources

            def blocking_list_resources(
                skill_path: Path,
            ) -> tuple[object, ...]:
                worker_started.set()
                release_worker.wait()
                try:
                    return original_list_resources(skill_path)
                finally:
                    worker_finished.set()

            monkeypatch.setattr(source, "_list_skill_resources", blocking_list_resources)
            tool_name = "load_skill"
            arguments = {"skill_name": "code-review"}
        else:
            original_read_text = Path.read_text

            def blocking_read_text(
                path: Path,
                encoding: str | None = None,
                errors: str | None = None,
            ) -> str:
                if path != resource_path:
                    return original_read_text(path, encoding=encoding, errors=errors)
                worker_started.set()
                release_worker.wait()
                try:
                    return original_read_text(path, encoding=encoding, errors=errors)
                finally:
                    worker_finished.set()

            monkeypatch.setattr(Path, "read_text", blocking_read_text)
            tool_name = "load_skill_resource"
            arguments = {
                "skill_name": "code-review",
                "path": "references/checklist.md",
            }

        tool_registry = ToolRegistry()
        register_skill_loader_tools(tool_registry, skill_registry)
        router = ToolCallRouter(tool_registry=tool_registry)
        messages = MessageRuntime()
        provider = FakeProvider(
            [
                ProviderResponse(
                    content="",
                    tool_calls=[
                        ProviderToolCall(
                            id="call_skill",
                            name=tool_name,
                            arguments=arguments,
                        ),
                    ],
                ),
                ProviderResponse(content="replacement"),
            ],
        )
        agent = Agent(
            QueryLoop(
                context_runtime=ContextRuntime(),
                message_runtime=messages,
                request_builder=ProviderRequestBuilder(
                    context_renderer=default_context_renderer(),
                    message_runtime=messages,
                    tools=router.tool_specs(),
                ),
                provider=provider,
                tool_call_router=router,
            ),
        )
        stream = await agent.run("first", stream=True)
        consumer = asyncio.create_task(_consume_stream(stream))
        close_task: asyncio.Task[None] | None = None
        try:
            assert await asyncio.to_thread(worker_started.wait, 5)
            close_task = asyncio.create_task(stream.aclose())

            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(close_task), 0.05)
            with pytest.raises(AgentBusyError):
                await agent.run("replacement")
        finally:
            release_worker.set()
            if close_task is not None:
                await asyncio.gather(close_task, return_exceptions=True)
            await asyncio.gather(consumer, return_exceptions=True)

        assert close_task is not None
        assert close_task.exception() is None
        assert consumer.cancelled()
        assert worker_finished.is_set()

        replacement = await agent.run("replacement")
        assert replacement.content == "replacement"

    asyncio.run(scenario())


def test_agent_close_waits_for_filesystem_skill_discovery_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_agent_close_waits_for_filesystem_skill_worker(
        tmp_path,
        monkeypatch,
        "discovery",
    )


def test_agent_close_waits_for_filesystem_skill_resource_listing_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_agent_close_waits_for_filesystem_skill_worker(
        tmp_path,
        monkeypatch,
        "resource_listing",
    )


def test_agent_close_waits_for_filesystem_skill_resource_read_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_agent_close_waits_for_filesystem_skill_worker(
        tmp_path,
        monkeypatch,
        "resource_read",
    )


async def _consume_stream(stream: AsyncIterator[object]) -> None:
    async for _event in stream:
        pass


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

    response = asyncio.run(Agent(loop).run("Lookup docs"))

    assert response.content == "MCP result consumed."
    tool_names = {tool.function.name for tool in provider.requests[0].tools}
    assert "mcp__docs__lookup" in tool_names
    assert "docs" not in provider.requests[0].system
    tool_result = provider.requests[1].messages[-1]
    assert tool_result.role == "tool"
    assert tool_result.content[0].text == "lookup:phase5"  # type: ignore[union-attr]
