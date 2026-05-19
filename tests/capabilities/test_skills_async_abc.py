from __future__ import annotations

import asyncio

from agentos.capabilities.skills import (
    SkillContentSource,
    SkillDefinition,
    SkillLoadResult,
    SkillRegistry,
    SkillResourceLoadResult,
    SkillResourceRef,
    register_skill_loader_tools,
)
from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.providers import ProviderToolCall


class AsyncMemorySkillSource(SkillContentSource):
    def __init__(
        self,
        skills: list[SkillDefinition],
        *,
        resources: dict[str, tuple[SkillResourceRef, ...]] | None = None,
    ) -> None:
        self._skills = {skill.name: skill for skill in skills}
        self._resources = resources or {}
        self.loaded: list[str] = []

    async def list_skills(self) -> list[SkillDefinition]:
        await asyncio.sleep(0)
        return list(self._skills.values())

    async def load_skill(self, name: str) -> SkillLoadResult:
        await asyncio.sleep(0)
        self.loaded.append(name)
        try:
            skill = self._skills[name]
        except KeyError as error:
            raise KeyError(name) from error
        return SkillLoadResult(name=skill.name, content=skill.content)

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        await asyncio.sleep(0)
        if name not in self._skills:
            raise KeyError(name)
        return self._resources.get(name, ())

    async def load_resource(self, name: str, path: str) -> SkillResourceLoadResult:
        await asyncio.sleep(0)
        refs = self._resources.get(name, ())
        if not any(ref.path == path for ref in refs):
            raise KeyError(path)
        return SkillResourceLoadResult(
            skill_name=name,
            path=path,
            content=f"resource:{name}:{path}",
            mime_type="text/plain",
        )


def test_skill_registry_uses_async_source_and_keeps_declarations_sync() -> None:
    source = AsyncMemorySkillSource(
        [
            SkillDefinition(
                name="review",
                description="Review code.",
                when_to_use="审查代码时使用。",
                content="# Review\nFind bugs first.",
            ),
        ],
    )

    async def run() -> tuple[SkillRegistry, SkillLoadResult]:
        registry = await SkillRegistry.aload(source)
        loaded = await registry.load("review")
        return registry, loaded

    registry, loaded = asyncio.run(run())

    assert loaded.content.startswith("# Review")
    assert source.loaded == ["review"]
    assert registry.available_skill_names() == ["review"]
    assert registry.capability_declarations()[0].when_to_use == "审查代码时使用。"


def test_load_skill_tool_result_includes_resource_manifest() -> None:
    source = AsyncMemorySkillSource(
        [
            SkillDefinition(
                name="reporting",
                description="Write reports.",
                when_to_use="写报告时使用。",
                content="# Reporting\nUse source docs.",
            ),
        ],
        resources={
            "reporting": (
                SkillResourceRef(path="examples/brief.md", mime_type="text/markdown"),
            ),
        },
    )
    async def run() -> object:
        registry = await SkillRegistry.aload(source)
        tools = ToolRegistry()
        register_skill_loader_tools(tools, registry)
        router = ToolCallRouter(tool_registry=tools)
        return await router.async_execute_tool_call(
            ProviderToolCall(
                id="call_1",
                name="load_skill",
                arguments={"skill_name": "reporting"},
            ),
        )

    loaded = asyncio.run(run())

    assert "# Skill: reporting" in loaded.content
    assert "## Available resources" in loaded.content
    assert "`examples/brief.md` (text/markdown)" in loaded.content
    assert "load_skill_resource" in loaded.content


def test_load_skill_resource_tool_loads_source_resource() -> None:
    source = AsyncMemorySkillSource(
        [
            SkillDefinition(
                name="reporting",
                description="Write reports.",
                when_to_use="写报告时使用。",
                content="# Reporting\nUse source docs.",
            ),
        ],
        resources={
            "reporting": (
                SkillResourceRef(path="examples/brief.md", mime_type="text/markdown"),
            ),
        },
    )
    async def run() -> object:
        registry = await SkillRegistry.aload(source)
        tools = ToolRegistry()
        register_skill_loader_tools(tools, registry)
        router = ToolCallRouter(tool_registry=tools)
        return await router.async_execute_tool_call(
            ProviderToolCall(
                id="call_2",
                name="load_skill_resource",
                arguments={"skill_name": "reporting", "path": "examples/brief.md"},
            ),
        )

    loaded = asyncio.run(run())

    assert loaded.content == "resource:reporting:examples/brief.md"
