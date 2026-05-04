from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

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
class SkillLoadResult:
    """`load_skill` 工具返回的结构化结果。"""

    name: str
    content: str

    def render_tool_result(self) -> str:
        """渲染为写入 tool result 的文本。"""

        return f"# Skill: {self.name}\n\n{self.content}"


class SkillRegistry:
    """保存可被 Capability Plane 摘要和 `load_skill` 使用的 skills。"""

    def __init__(self, builtin_skills: Iterable[SkillDefinition] = ()) -> None:
        """创建 skill registry，并先注册内置 skills。"""

        self._skills: dict[str, SkillDefinition] = {}
        for skill in builtin_skills:
            self.register(skill)

    @classmethod
    def from_paths(
        cls,
        skill_dirs: Iterable[Path],
        *,
        allowed: set[str] | None = None,
        builtin_skills: Iterable[SkillDefinition] = (),
    ) -> "SkillRegistry":
        """从一个或多个目录发现 Markdown skills。"""

        registry = cls(builtin_skills=builtin_skills)
        for skill_dir in skill_dirs:
            for path, source in _discover_skill_files(Path(skill_dir)):
                skill = _parse_skill_file(path, source)
                if (
                    source != "learned"
                    and allowed is not None
                    and skill.name not in allowed
                ):
                    continue
                if skill.name in registry._skills:
                    continue
                registry.register(skill)
        return registry

    def register(self, skill: SkillDefinition) -> None:
        """注册一个 skill，名称必须唯一。"""

        _validate_skill_name(skill.name)
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill: {skill.name}")
        self._skills[skill.name] = skill

    def load(self, skill_name: str) -> SkillLoadResult:
        """按名称加载 skill 完整内容。"""

        try:
            skill = self._skills[skill_name]
        except KeyError as error:
            raise KeyError(skill_name) from error
        return SkillLoadResult(name=skill.name, content=skill.content)

    def available_skill_names(self) -> list[str]:
        """返回当前可加载 skill 名称。"""

        return sorted(self._skills)

    def capability_declarations(self) -> list[SkillDeclaration]:
        """返回 LLM 可见 Capability Plane 使用的 skill 摘要。"""

        return [
            SkillDeclaration(name=skill.name, when_to_use=skill.when_to_use)
            for skill in self._skills.values()
        ]


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


def register_skill_loader_tool(
    tool_registry: ToolRegistry,
    skill_registry: SkillRegistry,
) -> None:
    """把 `load_skill` 注册成 provider-callable skill tool。"""

    def load_skill(arguments: dict[str, object]) -> str:
        skill_name = str(arguments.get("skill_name", ""))
        try:
            return skill_registry.load(skill_name).render_tool_result()
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
    return values, raw[match.end():]


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
