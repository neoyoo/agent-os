from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    """一个有序、带 canonical checksum 的不可变迁移条目。"""

    version: int
    name: str
    sha256: str
    up_sql: str

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise ValueError("migration version must be positive")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("migration name must not be empty")
        if (
            type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ValueError("migration checksum must be lowercase SHA-256")
        if type(self.up_sql) is not str or not self.up_sql.strip():
            raise ValueError("migration up SQL must not be empty")


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """连续迁移目录及其精确目标版本。"""

    entries: tuple[MigrationEntry, ...]
    target_version: int

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if not entries or any(type(entry) is not MigrationEntry for entry in entries):
            raise ValueError("migration plan entries are invalid")
        if [entry.version for entry in entries] != list(
            range(1, len(entries) + 1),
        ):
            raise ValueError("migration plan versions must be contiguous")
        if len({entry.name for entry in entries}) != len(entries):
            raise ValueError("migration plan names must be unique")
        if type(self.target_version) is not int or self.target_version != len(entries):
            raise ValueError("migration target must match the catalog")
        object.__setattr__(self, "entries", entries)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """一次 schema 检查或应用操作的不可变结果。"""

    current_version: int
    target_version: int
    applied_versions: tuple[int, ...]
    changed: bool

    def __post_init__(self) -> None:
        if (
            type(self.current_version) is not int
            or type(self.target_version) is not int
            or self.target_version < 1
            or not 0 <= self.current_version <= self.target_version
        ):
            raise ValueError("migration report versions are invalid")
        applied = tuple(self.applied_versions)
        if (
            any(type(version) is not int or version < 1 for version in applied)
            or tuple(sorted(set(applied))) != applied
            or (applied and applied[-1] != self.current_version)
        ):
            raise ValueError("migration report applied versions are invalid")
        if type(self.changed) is not bool or self.changed != bool(applied):
            raise ValueError("migration report changed flag is invalid")
        object.__setattr__(self, "applied_versions", applied)


__all__ = ["MigrationEntry", "MigrationPlan", "MigrationReport"]
