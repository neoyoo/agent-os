import asyncio
from pathlib import Path

from agentos.capabilities.skills import (
    BuiltinSkillSource,
    ChainedSkillSource,
    FileSystemSkillSource,
    SkillDefinition,
    SkillRegistry,
)


def test_chained_skill_source_combines_builtin_and_filesystem(
    tmp_path: Path,
) -> None:
    (tmp_path / "review.md").write_text(
        (
            "---\n"
            "name: review\n"
            "description: Review code.\n"
            "when_to_use: 审查代码时使用。\n"
            "---\n"
            "# Review\nFind bugs first.\n"
        ),
        encoding="utf-8",
    )
    builtin = SkillDefinition(
        name="schema-template",
        description="Schema.",
        when_to_use="声明 schema 时使用。",
        content="# Schema",
        source="builtin",
    )

    async def load_registry() -> SkillRegistry:
        return await SkillRegistry.aload(
            ChainedSkillSource(
                [
                    BuiltinSkillSource([builtin]),
                    FileSystemSkillSource([tmp_path]),
                ],
            ),
        )

    registry = asyncio.run(load_registry())

    assert registry.available_skill_names() == ["review", "schema-template"]
    assert asyncio.run(registry.load("review")).content.startswith("# Review")
    assert asyncio.run(registry.load("schema-template")).content == "# Schema"
