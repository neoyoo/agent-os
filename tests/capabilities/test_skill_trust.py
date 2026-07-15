from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentos.capabilities.skills import (
    BuiltinSkillSource,
    ChainedSkillSource,
    FileSystemSkillSource,
    SkillContentSource,
    SkillDefinition,
    SkillDescriptor,
    SkillLoadResult,
    SkillMetadata,
    SkillRegistry,
    SkillResourceLoadResult,
    SkillResourceRef,
    SkillTrustDecision,
    SkillVerificationSubject,
)


class ClosingSkillSource(SkillContentSource):
    def __init__(self) -> None:
        self.closed = False

    async def list_skills(self) -> list[SkillDescriptor]:
        return []

    async def load_skill(self, name: str) -> SkillLoadResult:
        raise KeyError(name)

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        raise KeyError(name)

    async def load_resource(self, name: str, path: str) -> SkillResourceLoadResult:
        raise KeyError(path)

    async def aclose(self) -> None:
        self.closed = True


def test_skill_metadata_never_contains_body_or_path() -> None:
    metadata = SkillMetadata(
        name="review",
        description="Review code.",
        loadable=True,
        trust="untrusted",
    )

    assert not hasattr(metadata, "content")
    assert not hasattr(metadata, "path")


def test_filesystem_skill_metadata_defaults_to_untrusted(tmp_path: Path) -> None:
    (tmp_path / "review.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\n# Review\n",
        encoding="utf-8",
    )

    metadata = asyncio.run(FileSystemSkillSource([tmp_path]).list_skills())[0]

    assert metadata.metadata.trust == "untrusted"
    assert metadata.metadata.loadable is True
    assert not hasattr(metadata, "content")
    assert not hasattr(metadata, "path")


def test_skill_verification_subject_binds_name_revision_and_content() -> None:
    first = SkillVerificationSubject.from_content(
        source_id="builtin",
        skill_name="review",
        source_revision="1",
        content="# Review\nFirst.",
    )
    second = SkillVerificationSubject.from_content(
        source_id="builtin",
        skill_name="review",
        source_revision="1",
        content="# Review\nSecond.",
    )

    assert first.content_digest != second.content_digest
    assert SkillTrustDecision(True, "tests", first).subject == first


def test_registry_rejects_duplicate_names_across_sources() -> None:
    definition = SkillDefinition(
        name="review",
        description="Review code.",
        when_to_use="Review code.",
        content="# Review",
        source="builtin",
        trust="trusted",
    )

    async def load() -> SkillRegistry:
        return await SkillRegistry.aload(
            ChainedSkillSource(
                (BuiltinSkillSource((definition,)), BuiltinSkillSource((definition,))),
            ),
        )

    with pytest.raises(ValueError, match="duplicate skill"):
        asyncio.run(load())


def test_registry_closes_async_source_lifecycle() -> None:
    source = ClosingSkillSource()

    async def close() -> None:
        registry = await SkillRegistry.aload(source)
        await registry.aclose()

    asyncio.run(close())

    assert source.closed is True
