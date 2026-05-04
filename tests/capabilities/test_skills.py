from pathlib import Path

import pytest

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.skills import (
    SkillRegistry,
    builtin_schema_template_skill,
    register_skill_loader_tool,
)
from agentos.providers import ProviderToolCall


def write_skill(path: Path, frontmatter: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")


def test_skill_registry_discovers_flat_directory_and_learned_layouts(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / "debugging.md",
        (
            "name: systematic-debugging\n"
            "description: Debug failures.\n"
            "when_to_use: 遇到 bug 或测试失败时使用。\n"
        ),
        "# Debugging\nRead errors first.\n",
    )
    write_skill(
        tmp_path / "review" / "SKILL.md",
        (
            "name: code-review\n"
            "description: Review code.\n"
            "when_to_use: 审查已完成改动时使用。\n"
        ),
        "# Review\nFind bugs before summaries.\n",
    )
    write_skill(
        tmp_path / "learned" / "repo-style" / "SKILL.md",
        (
            "name: repo-style\n"
            "description: Repo conventions.\n"
            "when_to_use: 修改本仓库代码前使用。\n"
        ),
        "# Repo Style\nUse agentos imports.\n",
    )

    registry = SkillRegistry.from_paths(
        [tmp_path],
        allowed={"systematic-debugging", "code-review"},
    )

    declarations = registry.capability_declarations()
    assert [(item.name, item.when_to_use) for item in declarations] == [
        ("repo-style", "修改本仓库代码前使用。"),
        ("systematic-debugging", "遇到 bug 或测试失败时使用。"),
        ("code-review", "审查已完成改动时使用。"),
    ]
    assert registry.load("repo-style").content == "# Repo Style\nUse agentos imports.\n"


def test_skill_loader_tool_returns_content_or_deterministic_error(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / "debugging.md",
        (
            "name: systematic-debugging\n"
            "description: Debug failures.\n"
            "when_to_use: 遇到 bug 或测试失败时使用。\n"
        ),
        "# Debugging\nRead errors first.\n",
    )
    skills = SkillRegistry.from_paths([tmp_path])
    tools = ToolRegistry()
    register_skill_loader_tool(tools, skills)
    router = ToolCallRouter(tool_registry=tools)

    loaded = router.execute_tool_call(
        ProviderToolCall(
            id="call_1",
            name="load_skill",
            arguments={"skill_name": "systematic-debugging"},
        ),
    )
    missing = router.execute_tool_call(
        ProviderToolCall(
            id="call_2",
            name="load_skill",
            arguments={"skill_name": "unknown"},
        ),
    )

    assert loaded.tool_call_id == "call_1"
    assert "# Skill: systematic-debugging" in loaded.content
    assert "# Debugging" in loaded.content
    assert missing.content == (
        '{"error": "Skill \'unknown\' not found", '
        '"available_skills": ["systematic-debugging"]}'
    )


def test_skill_frontmatter_supports_yaml_multiline_values(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "planning.md",
        (
            "name: planning\n"
            "description: Plan tasks.\n"
            "when_to_use: |\n"
            "  第一行规则。\n"
            "  第二行规则。\n"
        ),
        "# Planning\nWrite a plan.\n",
    )

    registry = SkillRegistry.from_paths([tmp_path])

    assert registry.capability_declarations()[0].when_to_use == (
        "第一行规则。\n第二行规则。"
    )


def test_builtin_schema_template_skill_is_available_but_not_special_cased() -> None:
    registry = SkillRegistry(builtin_skills=[builtin_schema_template_skill()])

    result = registry.load("schema-template")

    assert "declare_schema" in result.content
    assert "update_state" in result.content
    assert registry.capability_declarations()[0].name == "schema-template"


def test_skill_registry_rejects_duplicate_builtin_names() -> None:
    first = builtin_schema_template_skill()
    second = builtin_schema_template_skill()

    with pytest.raises(ValueError, match="duplicate skill"):
        SkillRegistry(builtin_skills=[first, second])
