import asyncio

import pytest

from agentos.capabilities.skills import (
    BuiltinSkillSource,
    SkillDefinition,
    SkillRegistry,
    builtin_schema_template_skill,
)


def test_builtin_skill_source_loads_builtin_skills_through_same_path() -> None:
    async def load_registry() -> SkillRegistry:
        return await SkillRegistry.aload(
            BuiltinSkillSource([builtin_schema_template_skill()]),
        )

    registry = asyncio.run(load_registry())
    loaded = asyncio.run(registry.load("schema-template"))

    assert "declare_schema" in loaded.content
    assert registry.available_skill_names() == ["schema-template"]


def test_builtin_skill_source_rejects_duplicate_names() -> None:
    skill = SkillDefinition(
        name="same",
        description="First.",
        when_to_use="First.",
        content="# First",
        source="builtin",
    )

    async def load_registry() -> SkillRegistry:
        return await SkillRegistry.aload(BuiltinSkillSource([skill, skill]))

    with pytest.raises(ValueError, match="duplicate skill"):
        asyncio.run(load_registry())
