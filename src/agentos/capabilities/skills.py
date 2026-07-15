from __future__ import annotations

import json
from collections.abc import Iterable

from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.skill_sources import (
    BuiltinSkillSource,
    ChainedSkillSource,
    FileSystemSkillSource,
    SkillContentSource,
    SkillLoadResult,
)
from agentos.capabilities.skill_trust import (
    SkillTrustDecision,
    SkillTrustPolicy,
    SkillVerificationSubject,
)
from agentos.capabilities.skill_types import (
    SkillDefinition,
    SkillDescriptor,
    SkillMetadata,
    SkillResourceLoadResult,
    SkillResourceRef,
    SkillSource,
    SkillTrust,
)
from agentos.capabilities.tools import RegisteredTool
from agentos.context.projection import SkillDeclaration


class SkillRegistry:
    """保存 Skill 描述并按需委托 Source 加载正文。"""

    def __init__(
        self,
        source: SkillContentSource | None = None,
        skills: Iterable[SkillDescriptor] = (),
    ) -> None:
        self._source = source
        self._skills: dict[str, SkillDescriptor] = {}
        for skill in skills:
            self._register_metadata(skill)

    @classmethod
    async def aload(
        cls,
        source: SkillContentSource | None = None,
        *,
        builtin_skills: Iterable[SkillDefinition] = (),
    ) -> "SkillRegistry":
        """异步加载 Source 元数据并创建 Registry。"""

        sources = []
        if source is not None:
            sources.append(source)
        builtin_skills = tuple(builtin_skills)
        if builtin_skills:
            sources.append(BuiltinSkillSource(builtin_skills))
        combined_source: SkillContentSource | None
        if not sources:
            combined_source = None
        elif len(sources) == 1:
            combined_source = sources[0]
        else:
            combined_source = ChainedSkillSource(sources)
        skills = [] if combined_source is None else await combined_source.list_skills()
        return cls(source=combined_source, skills=skills)

    def available_skill_names(self) -> list[str]:
        """返回当前可加载 Skill 名称。"""

        return sorted(self._skills)

    def descriptors(self) -> tuple[SkillDescriptor, ...]:
        """按发现顺序返回安全 Skill 描述。"""

        return tuple(self._skills.values())

    def capability_declarations(self) -> list[SkillDeclaration]:
        """返回 Capability Plane 使用的 Skill 摘要。"""

        return [
            SkillDeclaration(
                name=skill.metadata.name,
                when_to_use=skill.when_to_use,
            )
            for skill in self._skills.values()
        ]

    async def load(self, skill_name: str) -> SkillLoadResult:
        """按名称异步加载 Skill 完整内容。"""

        descriptor = self._require_known_skill(skill_name)
        if self._source is None:
            raise KeyError(skill_name)
        loaded = await self._source.load_skill(skill_name)
        if loaded.metadata != descriptor.metadata:
            raise ValueError("skill metadata changed during load")
        if loaded.subject.skill_name != skill_name:
            raise ValueError("skill verification subject mismatch")
        return loaded

    async def list_resources(self, skill_name: str) -> tuple[SkillResourceRef, ...]:
        """列出 Skill 资源 manifest。"""

        self._require_known_skill(skill_name)
        if self._source is None:
            raise KeyError(skill_name)
        return await self._source.list_resources(skill_name)

    async def load_resource(
        self,
        skill_name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """加载 Skill 资源。"""

        self._require_known_skill(skill_name)
        if self._source is None:
            raise KeyError(skill_name)
        return await self._source.load_resource(skill_name, path)

    async def aclose(self) -> None:
        """关闭 Registry 持有的 Source。"""

        if self._source is not None:
            await self._source.aclose()

    def _register_metadata(self, skill: SkillDescriptor) -> None:
        name = skill.metadata.name
        if name in self._skills:
            raise ValueError(f"duplicate skill: {name}")
        self._skills[name] = skill

    def _require_known_skill(self, skill_name: str) -> SkillDescriptor:
        try:
            return self._skills[skill_name]
        except KeyError as error:
            raise KeyError(skill_name) from error


def builtin_schema_template_skill() -> SkillDefinition:
    """返回内置 schema template Skill。"""

    return SkillDefinition(
        name="schema-template",
        description="Guide working state schema declarations.",
        when_to_use="需要声明或调整 working state schema 时使用。",
        source="builtin",
        trust="trusted",
        content=(
            "# Schema Template\n\n"
            "Use `declare_schema` at the start of a multi-step task when no "
            "working state schema exists. Use `extend_schema` when the current "
            "schema lacks a field, `update_state` when facts or plans change, "
            "and `start_chapter` when the task materially changes.\n\n"
            "Common fields:\n\n"
            "- `task_goal`: 当前任务目标和完成标准。\n"
            "- `constraints`: 用户、项目或安全约束。\n"
            "- `key_decisions`: 已确认且后续必须遵守的设计决策。\n"
            "- `verified_facts`: 已经通过阅读、运行或用户确认验证过的事实。\n"
            "- `open_questions`: 仍未确认、可能影响方案的问题。\n"
            "- `next_steps`: 下一步要做的具体动作。\n"
        ),
    )


def register_skill_loader_tools(
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
) -> None:
    """把 Skill loader tools 注册成 provider-callable async tools。"""

    tool_registry.register(_load_skill_tool(skill_registry))
    tool_registry.register(_load_skill_resource_tool(skill_registry))


def _load_skill_tool(skill_registry: SkillRegistry) -> RegisteredTool:
    async def load_skill(arguments: dict[str, object]) -> str:
        skill_name = str(arguments.get("skill_name", ""))
        try:
            result = await skill_registry.load(skill_name)
            resources = await skill_registry.list_resources(skill_name)
            return result.render_tool_result(resource_manifest=resources)
        except KeyError:
            return json.dumps(
                {
                    "error": f"Skill '{skill_name}' not found",
                    "available_skills": skill_registry.available_skill_names(),
                },
                ensure_ascii=False,
            )

    return RegisteredTool(
        name="load_skill",
        description=(
            "Load a skill's full instructions by name. "
            "Use this before tasks that match an available skill summary."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to load.",
                },
            },
            "required": ["skill_name"],
            "additionalProperties": False,
        },
        handler=load_skill,
        kind="skill",
    )


def _load_skill_resource_tool(skill_registry: SkillRegistry) -> RegisteredTool:
    async def load_skill_resource(arguments: dict[str, object]) -> str:
        skill_name = str(arguments.get("skill_name", ""))
        path = str(arguments.get("path", ""))
        try:
            result = await skill_registry.load_resource(skill_name, path)
            return result.render_tool_result()
        except KeyError:
            return json.dumps(
                {
                    "error": f"Resource '{path}' for skill '{skill_name}' not found",
                    "available_skills": skill_registry.available_skill_names(),
                },
                ensure_ascii=False,
            )

    return RegisteredTool(
        name="load_skill_resource",
        description="Load an additional resource for a Skill by path.",
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["skill_name", "path"],
            "additionalProperties": False,
        },
        handler=load_skill_resource,
        kind="skill",
    )


__all__ = [
    "BuiltinSkillSource",
    "ChainedSkillSource",
    "FileSystemSkillSource",
    "SkillContentSource",
    "SkillDefinition",
    "SkillDescriptor",
    "SkillLoadResult",
    "SkillMetadata",
    "SkillRegistry",
    "SkillResourceLoadResult",
    "SkillResourceRef",
    "SkillSource",
    "SkillTrust",
    "SkillTrustDecision",
    "SkillTrustPolicy",
    "SkillVerificationSubject",
    "builtin_schema_template_skill",
    "register_skill_loader_tools",
]
