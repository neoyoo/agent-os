from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillReleaseFile:
    """One file entry in a skill release manifest."""

    path: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SkillReleaseManifest:
    """JSON-safe manifest for an agent-os skill release source."""

    skill_name: str
    version: str
    source: str
    files: tuple[SkillReleaseFile, ...]
    manifest_hash: str

    @property
    def file_count(self) -> int:
        return len(self.files)

    def as_dict(self) -> dict[str, object]:
        return {
            "skill_name": self.skill_name,
            "version": self.version,
            "source": self.source,
            "file_count": self.file_count,
            "manifest_hash": self.manifest_hash,
            "files": tuple(file.as_dict() for file in self.files),
        }


@dataclass(frozen=True, slots=True)
class SkillReleaseDriftReport:
    """Comparison report between expected and installed skill manifests."""

    expected_version: str
    actual_version: str
    version_match: bool
    missing_files: tuple[str, ...]
    extra_files: tuple[str, ...]
    changed_files: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return (
            self.version_match
            and not self.missing_files
            and not self.extra_files
            and not self.changed_files
        )

    @property
    def status(self) -> str:
        return "ok" if self.ready else "drift"

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "status": self.status,
            "expected_version": self.expected_version,
            "actual_version": self.actual_version,
            "version_match": self.version_match,
            "missing_files": self.missing_files,
            "extra_files": self.extra_files,
            "changed_files": self.changed_files,
        }


def build_skill_release_manifest(
    skill_dir: str | Path,
    *,
    version: str,
    source: str,
    skill_name: str = "agent-os",
) -> SkillReleaseManifest:
    """Build a deterministic manifest for a skill directory."""

    if not version.strip():
        raise ValueError("version must not be empty")
    if not source.strip():
        raise ValueError("source must not be empty")
    if not skill_name.strip():
        raise ValueError("skill_name must not be empty")

    root = Path(skill_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"skill_dir not found: {root}")
    if not (root / "SKILL.md").is_file():
        raise FileNotFoundError("skill_dir must contain SKILL.md")

    files = tuple(_iter_skill_files(root))
    manifest_payload = {
        "skill_name": skill_name,
        "version": version,
        "source": source,
        "files": tuple(file.as_dict() for file in files),
    }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest_payload, sort_keys=True).encode("utf-8"),
    ).hexdigest()
    return SkillReleaseManifest(
        skill_name=skill_name,
        version=version,
        source=source,
        files=files,
        manifest_hash=manifest_hash,
    )


def compare_skill_release_manifests(
    expected: SkillReleaseManifest,
    actual: SkillReleaseManifest,
) -> SkillReleaseDriftReport:
    """Compare an expected skill manifest with an installed manifest."""

    expected_files = {file.path: file.sha256 for file in expected.files}
    actual_files = {file.path: file.sha256 for file in actual.files}
    expected_paths = set(expected_files)
    actual_paths = set(actual_files)
    shared_paths = expected_paths & actual_paths
    return SkillReleaseDriftReport(
        expected_version=expected.version,
        actual_version=actual.version,
        version_match=expected.version == actual.version,
        missing_files=tuple(sorted(expected_paths - actual_paths)),
        extra_files=tuple(sorted(actual_paths - expected_paths)),
        changed_files=tuple(
            path
            for path in sorted(shared_paths)
            if expected_files[path] != actual_files[path]
        ),
    )


def _iter_skill_files(root: Path) -> tuple[SkillReleaseFile, ...]:
    entries: list[SkillReleaseFile] = []
    for path in sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda candidate: (
            candidate.relative_to(root).as_posix() != "SKILL.md",
            candidate.relative_to(root).as_posix(),
        ),
    ):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        entries.append(
            SkillReleaseFile(
                path=relative,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            ),
        )
    return tuple(entries)
