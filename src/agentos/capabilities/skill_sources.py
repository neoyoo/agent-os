from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from agentos._sync_work import run_sync
from agentos.capabilities._skill_resources import _guess_mime_type, _safe_resource_path
from agentos.capabilities.skill_trust import SkillVerificationSubject
from agentos.capabilities.skill_types import (
    SkillDefinition,
    SkillDescriptor,
    SkillLoadResult,
    SkillMetadata,
    SkillResourceLoadResult,
    SkillResourceRef,
    SkillSource,
    _require_skill_name,
)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SkillContentSource(ABC):
    """异步 Skill 内容来源。"""

    @abstractmethod
    def current_subject(self, name: str) -> SkillVerificationSubject | None:
        """返回内存中的当前验证主体；无法证明时返回 None。"""

    @abstractmethod
    async def list_skills(self) -> list[SkillDescriptor]:
        """列出不含正文或路径的 Skill 描述。"""

    @abstractmethod
    async def load_skill(self, name: str) -> SkillLoadResult:
        """按名称加载完整 Skill 正文。"""

    @abstractmethod
    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        """列出 Skill 可按需加载的资源。"""

    @abstractmethod
    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        """加载 Skill 资源内容。"""

    async def load_resources(
        self,
        name: str,
        paths: Iterable[str],
    ) -> list[SkillResourceLoadResult]:
        """批量加载资源；外部存储实现可重写为 pipeline。"""

        return await asyncio.gather(
            *(self.load_resource(name, path) for path in paths),
        )

    async def aclose(self) -> None:
        """释放 Source 持有的异步资源。"""


class BuiltinSkillSource(SkillContentSource):
    """把内置 Skill 暴露为普通异步 Source。"""

    def __init__(self, skills: Iterable[SkillDefinition]) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        for skill in skills:
            _require_skill_name(skill.name)
            if skill.name in self._skills:
                raise ValueError(f"duplicate skill: {skill.name}")
            self._skills[skill.name] = skill

    async def list_skills(self) -> list[SkillDescriptor]:
        return [skill.descriptor() for skill in self._skills.values()]

    def current_subject(self, name: str) -> SkillVerificationSubject | None:
        try:
            skill = self._skills[name]
        except KeyError:
            return None
        return SkillVerificationSubject.from_content(
            source_id="builtin",
            skill_name=name,
            source_revision=skill.source_revision,
            content=skill.content,
        )

    async def load_skill(self, name: str) -> SkillLoadResult:
        try:
            skill = self._skills[name]
        except KeyError as error:
            raise KeyError(name) from error
        return SkillLoadResult(
            name=name,
            content=skill.content,
            metadata=skill.descriptor().metadata,
            subject=SkillVerificationSubject.from_content(
                source_id="builtin",
                skill_name=name,
                source_revision=skill.source_revision,
                content=skill.content,
            ),
        )

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        if name not in self._skills:
            raise KeyError(name)
        return ()

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        if name not in self._skills:
            raise KeyError(name)
        raise KeyError(path)


@dataclass(frozen=True, slots=True)
class _FileSkillRecord:
    descriptor: SkillDescriptor
    path: Path


class FileSystemSkillSource(SkillContentSource):
    """从本地目录异步发现并按需加载 Markdown Skill。"""

    def __init__(
        self,
        skill_dirs: Iterable[Path],
        *,
        allowed: set[str] | None = None,
    ) -> None:
        self._skill_dirs = tuple(Path(skill_dir) for skill_dir in skill_dirs)
        self._allowed = allowed
        self._records: dict[str, _FileSkillRecord] | None = None
        source_text = "\n".join(str(path.resolve()) for path in self._skill_dirs)
        self._source_id = f"filesystem:{sha256(source_text.encode()).hexdigest()}"

    async def list_skills(self) -> list[SkillDescriptor]:
        records = await self._records_by_name()
        return [record.descriptor for record in records.values()]

    def current_subject(self, name: str) -> SkillVerificationSubject | None:
        # Filesystem Skill 固定为 untrusted，不进入 SystemEnvelope。
        return None

    async def load_skill(self, name: str) -> SkillLoadResult:
        records = await self._records_by_name()
        try:
            record = records[name]
        except KeyError as error:
            raise KeyError(name) from error
        raw = await run_sync(record.path.read_text, encoding="utf-8")
        frontmatter, content = _parse_frontmatter(raw)
        loaded_name = frontmatter.get("name", _fallback_name(record.path))
        if loaded_name != name:
            raise KeyError(name)
        metadata = _descriptor_from_frontmatter(frontmatter, record.path).metadata
        return SkillLoadResult(
            name=name,
            content=content,
            metadata=metadata,
            subject=SkillVerificationSubject.from_content(
                source_id=self._source_id,
                skill_name=name,
                source_revision=sha256(raw.encode("utf-8")).hexdigest(),
                content=content,
            ),
        )

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        record = await self._record(name)
        if record.path.name != "SKILL.md":
            return ()
        return await run_sync(self._list_skill_resources, record.path)

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        record = await self._record(name)
        if record.path.name != "SKILL.md":
            raise KeyError(name)
        resource_path = _safe_resource_path(record.path.parent, path)
        if resource_path is None or not resource_path.is_file():
            raise KeyError(path)
        content = await run_sync(resource_path.read_text, encoding="utf-8")
        return SkillResourceLoadResult(
            skill_name=name,
            path=Path(path).as_posix(),
            content=content,
            mime_type=_guess_mime_type(resource_path),
        )

    async def _record(self, name: str) -> _FileSkillRecord:
        records = await self._records_by_name()
        try:
            return records[name]
        except KeyError as error:
            raise KeyError(name) from error

    async def _records_by_name(self) -> dict[str, _FileSkillRecord]:
        if self._records is None:
            self._records = await run_sync(self._discover_skills)
        return self._records

    def _discover_skills(self) -> dict[str, _FileSkillRecord]:
        records: dict[str, _FileSkillRecord] = {}
        for skill_dir in self._skill_dirs:
            for path, source in _discover_skill_files(skill_dir):
                raw = path.read_text(encoding="utf-8")
                frontmatter, _ = _parse_frontmatter(raw)
                descriptor = _descriptor_from_frontmatter(frontmatter, path)
                name = descriptor.metadata.name
                if source != "learned" and self._allowed is not None:
                    if name not in self._allowed:
                        continue
                _require_skill_name(name)
                if name in records:
                    raise ValueError(f"duplicate skill: {name}")
                records[name] = _FileSkillRecord(
                    descriptor=descriptor,
                    path=path,
                )
        return records

    def _list_skill_resources(self, skill_path: Path) -> tuple[SkillResourceRef, ...]:
        resources = []
        for path in sorted(skill_path.parent.rglob("*")):
            if not path.is_file() or path == skill_path:
                continue
            relative_path = path.relative_to(skill_path.parent)
            if any(part.startswith(".") for part in relative_path.parts):
                continue
            resources.append(
                SkillResourceRef(
                    path=relative_path.as_posix(),
                    mime_type=_guess_mime_type(path),
                ),
            )
        return tuple(resources)


class ChainedSkillSource(SkillContentSource):
    """按顺序组合多个 Skill Source。"""

    def __init__(self, sources: Iterable[SkillContentSource]) -> None:
        self._sources = tuple(sources)

    async def list_skills(self) -> list[SkillDescriptor]:
        skills = []
        for source in self._sources:
            skills.extend(await source.list_skills())
        return skills

    def current_subject(self, name: str) -> SkillVerificationSubject | None:
        subjects = tuple(
            subject
            for source in self._sources
            if (subject := source.current_subject(name)) is not None
        )
        if len(subjects) != 1:
            return None
        return subjects[0]

    async def load_skill(self, name: str) -> SkillLoadResult:
        source = await self._source_for(name)
        return await source.load_skill(name)

    async def list_resources(self, name: str) -> tuple[SkillResourceRef, ...]:
        source = await self._source_for(name)
        return await source.list_resources(name)

    async def load_resource(
        self,
        name: str,
        path: str,
    ) -> SkillResourceLoadResult:
        source = await self._source_for(name)
        return await source.load_resource(name, path)

    async def aclose(self) -> None:
        for source in reversed(self._sources):
            await source.aclose()

    async def _source_for(self, name: str) -> SkillContentSource:
        for source in self._sources:
            if any(
                descriptor.metadata.name == name
                for descriptor in await source.list_skills()
            ):
                return source
        raise KeyError(name)


def _descriptor_from_frontmatter(
    frontmatter: dict[str, str],
    path: Path,
) -> SkillDescriptor:
    description = frontmatter.get("description", "")
    return SkillDescriptor(
        metadata=SkillMetadata(
            name=frontmatter.get("name", _fallback_name(path)),
            description=description,
            loadable=True,
            trust="untrusted",
        ),
        when_to_use=frontmatter.get("when_to_use") or description,
    )


def _fallback_name(path: Path) -> str:
    return path.parent.name if path.name == "SKILL.md" else path.stem


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
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
        value = value.strip()
        if not key:
            index += 1
            continue
        if value in {"|", ">"}:
            block_lines: list[str] = []
            index += 1
            while index < len(lines):
                next_line = lines[index]
                if next_line and not next_line.startswith((" ", "\t")):
                    break
                block_lines.append(next_line.strip())
                index += 1
            values[key] = (
                " ".join(item for item in block_lines if item)
                if value == ">"
                else "\n".join(block_lines).strip()
            )
            continue
        values[key] = value
        index += 1
    return values, raw[match.end() :]


def _discover_skill_files(skills_dir: Path) -> list[tuple[Path, SkillSource]]:
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
