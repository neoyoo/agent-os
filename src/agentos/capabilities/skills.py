from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentos.capabilities.registry import ToolRegistry
from agentos.capabilities.tools import RegisteredTool
from agentos.context.projection import SkillDeclaration


SkillSource = Literal["builtin", "filesystem", "learned"]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """可按需加载的 skill 定义。"""

    name: str
    description: str
    when_to_use: str
    content: str
    source: SkillSource = "filesystem"
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class SkillResourceRef:
    """Skill 附带资源的轻量 manifest 项。"""

    path: str
    mime_type: str = "text/plain"


@dataclass(frozen=True, slots=True)
class SkillLoadResult:
    """`load_skill` 工具返回的结构化结果。"""

    name: str
    content: str

    def render_tool_result(
        self,
        resource_manifest: tuple[SkillResourceRef, ...] = (),
    ) -> str:
        """渲染为写入 tool result 的文本。"""

        body = f"# Skill: {self.name}\n\n{self.content}"
        if resource_manifest:
            resources = "\n".join(
                f"- `{resource.path}` ({resource.mime_type})"
                for resource in resource_manifest
            )
            body = (
                f"{body}\n\n## Available resources\n"
                f"{resources}\n\n"
                "Use `load_skill_resource` to load any of the above."
            )
        return body


@dataclass(frozen=True, slots=True)
class SkillResourceLoadResult:
    """`load_skill_resource` 工具返回的结构化结果。"""

    skill_name: str
    path: str
    content: str
    mime_type: str = "text/plain"

    def render_tool_result(self) -> str:
        """渲染为写入 tool result 的文本。"""

        return self.content


class SkillContentSource(ABC):
    """异步 skill 内容来源。"""

    @abstractmethod
    async def list_skills(self) -> list[SkillDefinition]:
        """列出可用 skill 元数据。"""

    @abstractmethod
    async def load_skill(self, name: str) -> SkillLoadResult:
        """按名称加载 skill 完整内容。"""

    @abstractmethod
    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        """列出 skill 可按需加载的资源。"""

    @abstractmethod
    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """加载 skill 资源内容。"""

    async def load_resources(
        self,
        name: str,
        paths: Iterable[str],
    ) -> list[SkillResourceLoadResult]:
        """批量加载资源；外部存储实现可重写为 pipeline。"""

        return await asyncio.gather(
            *(self.load_resource(name, path) for path in paths),
        )


class BuiltinSkillSource(SkillContentSource):
    """把内置 skill 暴露为普通 async source。"""

    def __init__(self, skills: Iterable[SkillDefinition]) -> None:
        """创建内置 skill source。"""

        self._skills: dict[str, SkillDefinition] = {}
        for skill in skills:
            _validate_skill_name(skill.name)
            if skill.name in self._skills:
                raise ValueError(f"duplicate skill: {skill.name}")
            self._skills[skill.name] = skill

    async def list_skills(self) -> list[SkillDefinition]:
        """列出内置 skills。"""

        return list(self._skills.values())

    async def load_skill(self, name: str) -> SkillLoadResult:
        """加载内置 skill。"""

        try:
            skill = self._skills[name]
        except KeyError as error:
            raise KeyError(name) from error
        return SkillLoadResult(name=skill.name, content=skill.content)

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        """内置 skill 当前不携带资源。"""

        if name not in self._skills:
            raise KeyError(name)
        return ()

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """内置 skill 当前不携带资源。"""

        if name not in self._skills:
            raise KeyError(name)
        raise KeyError(path)


class FileSystemSkillSource(SkillContentSource):
    """从本地目录异步发现和加载 Markdown skills。"""

    def __init__(
        self,
        skill_dirs: Iterable[Path],
        *,
        allowed: set[str] | None = None,
    ) -> None:
        """创建 filesystem source；不在构造阶段执行 I/O。"""

        self._skill_dirs = [Path(skill_dir) for skill_dir in skill_dirs]
        self._allowed = allowed
        self._skills: dict[str, SkillDefinition] | None = None

    async def list_skills(self) -> list[SkillDefinition]:
        """异步发现 skill 文件并缓存元数据。"""

        if self._skills is None:
            self._skills = await asyncio.to_thread(self._discover_skills)
        return list(self._skills.values())

    async def load_skill(self, name: str) -> SkillLoadResult:
        """加载 filesystem skill。"""

        skills = await self._skills_by_name()
        try:
            skill = skills[name]
        except KeyError as error:
            raise KeyError(name) from error
        return SkillLoadResult(name=skill.name, content=skill.content)

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        """filesystem source 当前只加载 Markdown skill 主体。"""

        skills = await self._skills_by_name()
        if name not in skills:
            raise KeyError(name)
        return ()

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """filesystem source 当前没有额外资源索引。"""

        skills = await self._skills_by_name()
        if name not in skills:
            raise KeyError(name)
        raise KeyError(path)

    async def _skills_by_name(self) -> dict[str, SkillDefinition]:
        if self._skills is None:
            await self.list_skills()
        assert self._skills is not None
        return self._skills

    def _discover_skills(self) -> dict[str, SkillDefinition]:
        skills: dict[str, SkillDefinition] = {}
        for skill_dir in self._skill_dirs:
            for path, source in _discover_skill_files(skill_dir):
                skill = _parse_skill_file(path, source)
                if (
                    source != "learned"
                    and self._allowed is not None
                    and skill.name not in self._allowed
                ):
                    continue
                _validate_skill_name(skill.name)
                if skill.name in skills:
                    continue
                skills[skill.name] = skill
        return skills


class ChainedSkillSource(SkillContentSource):
    """按顺序组合多个 skill source。"""

    def __init__(self, sources: Iterable[SkillContentSource]) -> None:
        """创建组合 source。"""

        self._sources = list(sources)

    async def list_skills(self) -> list[SkillDefinition]:
        """按 source 顺序串联 skill 元数据。"""

        skills: list[SkillDefinition] = []
        for source in self._sources:
            skills.extend(await source.list_skills())
        return skills

    async def load_skill(self, name: str) -> SkillLoadResult:
        """从第一个匹配 source 加载 skill。"""

        for source in self._sources:
            if await self._source_has_skill(source, name):
                return await source.load_skill(name)
        raise KeyError(name)

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        """从第一个匹配 source 列出资源。"""

        for source in self._sources:
            if await self._source_has_skill(source, name):
                return await source.list_resources(name)
        raise KeyError(name)

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """从第一个匹配 source 加载资源。"""

        for source in self._sources:
            if await self._source_has_skill(source, name):
                return await source.load_resource(name, path)
        raise KeyError(name)

    async def _source_has_skill(self, source: SkillContentSource, name: str) -> bool:
        return any(skill.name == name for skill in await source.list_skills())


class SkillRegistry:
    """保存可被 Capability Plane 摘要和 `load_skill` 使用的 skills。"""

    def __init__(
        self,
        source: SkillContentSource | None = None,
        skills: Iterable[SkillDefinition] = (),
    ) -> None:
        """创建 skill registry；构造阶段不触发 I/O。"""

        self._source = source
        self._skills: dict[str, SkillDefinition] = {}
        for skill in skills:
            self._register_metadata(skill)

    @classmethod
    async def aload(
        cls,
        source: SkillContentSource | None = None,
        *,
        builtin_skills: Iterable[SkillDefinition] = (),
    ) -> "SkillRegistry":
        """异步加载 source 元数据并创建 registry。"""

        sources: list[SkillContentSource] = []
        if source is not None:
            sources.append(source)
        builtin_skills = list(builtin_skills)
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
        """返回当前可加载 skill 名称。"""

        return sorted(self._skills)

    def capability_declarations(self) -> list[SkillDeclaration]:
        """返回 LLM 可见 Capability Plane 使用的 skill 摘要。"""

        return [
            SkillDeclaration(name=skill.name, when_to_use=skill.when_to_use)
            for skill in self._skills.values()
        ]

    async def load(self, skill_name: str) -> SkillLoadResult:
        """按名称异步加载 skill 完整内容。"""

        self._require_known_skill(skill_name)
        if self._source is None:
            raise KeyError(skill_name)
        return await self._source.load_skill(skill_name)

    async def list_resources(self, skill_name: str) -> tuple[SkillResourceRef, ...]:
        """列出 skill 资源 manifest。"""

        self._require_known_skill(skill_name)
        if self._source is None:
            raise KeyError(skill_name)
        return await self._source.list_resources(skill_name)

    async def load_resource(
        self,
        skill_name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """加载 skill 资源。"""

        self._require_known_skill(skill_name)
        if self._source is None:
            raise KeyError(skill_name)
        return await self._source.load_resource(skill_name, path)

    def _register_metadata(self, skill: SkillDefinition) -> None:
        _validate_skill_name(skill.name)
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill: {skill.name}")
        self._skills[skill.name] = skill

    def _require_known_skill(self, skill_name: str) -> None:
        if skill_name not in self._skills:
            raise KeyError(skill_name)


def builtin_schema_template_skill() -> SkillDefinition:
    """返回内置 schema template skill。"""

    return SkillDefinition(
        name="schema-template",
        description="Guide working state schema declarations.",
        when_to_use="需要声明或调整 working state schema 时使用。",
        source="builtin",
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
    """把 skill loader tools 注册成 provider-callable async tools。"""

    _register_load_skill_tool(tool_registry, skill_registry)
    _register_load_skill_resource_tool(tool_registry, skill_registry)


def _register_load_skill_tool(
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
) -> None:
    """注册 `load_skill`。"""

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

    tool_registry.register(
        RegisteredTool(
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
        ),
    )


def _register_load_skill_resource_tool(
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
) -> None:
    """注册 `load_skill_resource`。"""

    async def load_skill_resource(arguments: dict[str, object]) -> str:
        skill_name = str(arguments.get("skill_name", ""))
        path = str(arguments.get("path", ""))
        try:
            result = await skill_registry.load_resource(skill_name, path)
            return result.render_tool_result()
        except KeyError:
            return json.dumps(
                {
                    "error": (
                        f"Resource '{path}' for skill '{skill_name}' not found"
                    ),
                    "available_skills": skill_registry.available_skill_names(),
                },
                ensure_ascii=False,
            )

    tool_registry.register(
        RegisteredTool(
            name="load_skill_resource",
            description=(
                "Load an additional resource for a previously loaded skill by path."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Name of the skill that owns the resource.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Resource path from the skill manifest.",
                    },
                },
                "required": ["skill_name", "path"],
                "additionalProperties": False,
            },
            handler=load_skill_resource,
            kind="skill",
        ),
    )


def _parse_skill_file(path: Path, source: SkillSource) -> SkillDefinition:
    """读取 Markdown skill 文件。"""

    raw = path.read_text(encoding="utf-8")
    frontmatter, content = _parse_frontmatter(raw)
    fallback_name = path.parent.name if path.name == "SKILL.md" else path.stem
    name = frontmatter.get("name", fallback_name)
    description = frontmatter.get("description", "")
    when_to_use = frontmatter.get("when_to_use") or description
    return SkillDefinition(
        name=name,
        description=description,
        when_to_use=when_to_use,
        content=content,
        source=source,
        path=path,
    )


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """解析轻量 frontmatter，支持 `key: value` 和 YAML block values。"""

    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        return {}, raw

    values: dict[str, str] = {}
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if ":" not in line:
            index += 1
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            index += 1
            continue
        value = value.strip()
        if value in {"|", ">"}:
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if next_line and not next_line.startswith((" ", "\t")):
                    break
                block_lines.append(next_line.strip())
                index += 1
            if value == ">":
                values[key] = " ".join(line for line in block_lines if line)
            else:
                values[key] = "\n".join(block_lines).strip()
            continue
        values[key] = value
        index += 1
    return values, raw[match.end() :]


def _discover_skill_files(skills_dir: Path) -> list[tuple[Path, SkillSource]]:
    """按 agentos 支持的目录布局发现 skill 文件。"""

    if not skills_dir.exists():
        return []

    discovered: list[tuple[Path, SkillSource]] = []
    learned_dir = skills_dir / "learned"
    if learned_dir.exists():
        discovered.extend(
            (path, "learned")
            for path in sorted(learned_dir.glob("*/SKILL.md"))
            if path.is_file()
        )

    discovered.extend(
        (path, "filesystem")
        for path in sorted(skills_dir.glob("*.md"))
        if path.is_file()
    )
    discovered.extend(
        (path, "filesystem")
        for path in sorted(skills_dir.glob("*/SKILL.md"))
        if path.is_file() and path.parent.name != "learned"
    )
    return discovered


def _validate_skill_name(name: str) -> None:
    """校验 skill 名称可安全用于 provider tool 参数。"""

    if not _SKILL_NAME_RE.match(name):
        raise ValueError(f"invalid skill name: {name}")
