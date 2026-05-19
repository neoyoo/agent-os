from pathlib import Path
from abc import ABC

import pytest

from agentos.capabilities import ToolCallRouter, ToolRegistry
from agentos.capabilities.skills import (
    FileSystemSkillSource,
    SkillContentSource,
    SkillDefinition,
    SkillLoadResult,
    SkillRegistry,
    SkillResourceRef,
    builtin_schema_template_skill,
    register_skill_loader_tools,
    register_skill_loader_tool,
)
from agentos.providers import ProviderToolCall


class LazySkillSource(SkillContentSource):
    def __init__(self) -> None:
        self.loaded: list[str] = []

    def list_skills(self):
        return [
            SkillDefinition(
                name="redis-backed",
                description="Loaded from hot cache.",
                when_to_use="Use for cached SaaS skills.",
                content="",
            ),
        ]

    def load_skill(self, skill_name: str):
        self.loaded.append(skill_name)
        return SkillLoadResult(
            name=skill_name,
            content="# Redis Backed\nLoaded lazily.",
            content_hash="sha256:lazy",
        )

    def list_resources(self, skill_name: str):
        return ()

    def load_resource(self, skill_name: str, path: str):
        raise KeyError(path)


def test_skill_content_source_is_abc_contract() -> None:
    assert issubclass(SkillContentSource, ABC)
    with pytest.raises(TypeError):
        SkillContentSource()  # type: ignore[abstract]


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


def test_skill_registry_delegates_body_loading_to_content_source() -> None:
    source = LazySkillSource()
    registry = SkillRegistry(source=source)

    loaded = registry.load("redis-backed")

    assert source.loaded == ["redis-backed"]
    assert loaded.content == "# Redis Backed\nLoaded lazily."
    assert loaded.content_hash == "sha256:lazy"
    assert registry.capability_declarations()[0].when_to_use == (
        "Use for cached SaaS skills."
    )


def test_skill_registry_keeps_builtin_skills_when_using_content_source() -> None:
    source = LazySkillSource()
    registry = SkillRegistry(
        builtin_skills=[builtin_schema_template_skill()],
        source=source,
    )

    loaded_builtin = registry.load("schema-template")
    loaded_source = registry.load("redis-backed")

    assert "declare_schema" in loaded_builtin.content
    assert loaded_source.content == "# Redis Backed\nLoaded lazily."
    assert source.loaded == ["redis-backed"]


def test_filesystem_skill_source_discovers_resources_without_bundling_body(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / "drawing-quotation" / "SKILL.md",
        (
            "name: drawing-quotation\n"
            "description: Analyze mechanical drawings.\n"
        ),
        (
            "# Drawing Quotation\n"
            "Load `references/output-contract.md` only when output schema details "
            "are needed.\n"
        ),
    )
    resource = tmp_path / "drawing-quotation" / "references" / "output-contract.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("# Output Contract\nReturn drawing_info.\n", encoding="utf-8")

    source = FileSystemSkillSource([tmp_path])
    registry = SkillRegistry(source=source)

    loaded = registry.load("drawing-quotation")
    resources = registry.list_resources("drawing-quotation")

    assert "Drawing Quotation" in loaded.content
    assert loaded.content_hash.startswith("sha256:")
    assert "Output Contract" not in loaded.content
    assert resources == (
        SkillResourceRef(
            path="references/output-contract.md",
            content_hash=resources[0].content_hash,
        ),
    )


def test_skill_resource_loader_tool_returns_allowed_resource_content(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / "drawing-quotation" / "SKILL.md",
        (
            "name: drawing-quotation\n"
            "description: Analyze mechanical drawings.\n"
        ),
        "# Drawing Quotation\nUse references on demand.\n",
    )
    resource = tmp_path / "drawing-quotation" / "references" / "output-contract.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("# Output Contract\nReturn drawing_info.\n", encoding="utf-8")
    registry = SkillRegistry(source=FileSystemSkillSource([tmp_path]))
    tools = ToolRegistry()
    register_skill_loader_tools(tools, registry)
    router = ToolCallRouter(tool_registry=tools)

    loaded_skill = router.execute_tool_call(
        ProviderToolCall(
            id="call_skill",
            name="load_skill",
            arguments={"skill_name": "drawing-quotation"},
        ),
    )
    loaded_resource = router.execute_tool_call(
        ProviderToolCall(
            id="call_resource",
            name="load_skill_resource",
            arguments={
                "skill_name": "drawing-quotation",
                "path": "references/output-contract.md",
            },
        ),
    )
    denied_resource = router.execute_tool_call(
        ProviderToolCall(
            id="call_denied",
            name="load_skill_resource",
            arguments={
                "skill_name": "drawing-quotation",
                "path": "../secret.txt",
            },
        ),
    )

    assert "# Drawing Quotation" in loaded_skill.content
    assert "Output Contract" not in loaded_skill.content
    assert "# Skill Resource: drawing-quotation/references/output-contract.md" in (
        loaded_resource.content
    )
    assert "Return drawing_info." in loaded_resource.content
    assert "not found" in denied_resource.content


def test_skill_resource_loader_does_not_expose_skill_body_as_resource(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / "drawing-quotation" / "SKILL.md",
        (
            "name: drawing-quotation\n"
            "description: Analyze mechanical drawings.\n"
        ),
        "# Drawing Quotation\nUse references on demand.\n",
    )
    registry = SkillRegistry(source=FileSystemSkillSource([tmp_path]))

    assert registry.list_resources("drawing-quotation") == ()
    with pytest.raises(KeyError):
        registry.load_resource("drawing-quotation", "SKILL.md")


def test_flat_skill_file_does_not_expose_sibling_files_as_resources(
    tmp_path: Path,
) -> None:
    write_skill(
        tmp_path / "debugging.md",
        (
            "name: systematic-debugging\n"
            "description: Debug failures.\n"
        ),
        "# Debugging\nRead errors first.\n",
    )
    (tmp_path / "secret.md").write_text("do not expose", encoding="utf-8")

    registry = SkillRegistry(source=FileSystemSkillSource([tmp_path]))

    assert registry.list_resources("systematic-debugging") == ()
    with pytest.raises(KeyError):
        registry.load_resource("systematic-debugging", "secret.md")


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
