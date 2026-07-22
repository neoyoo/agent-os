from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files

from agentos.distributed.migrations.models import (
    MigrationEntry,
    MigrationPlan,
    MigrationReport,
)
from agentos.distributed.migrations.protocols import DistributedMigrationPort


_CATALOG = (
    (1, "2026-07-20-postgres-distributed-runtime.sql"),
    (2, "2026-07-21-postgres-team-delivery.sql"),
)
_UP_MARKER = b"-- migrate:up\n"
_DOWN_MARKER = b"-- migrate:down\n"


def canonical_migration_plan() -> MigrationPlan:
    """从 SDK 内置显式目录构建 canonical migration plan。"""

    package = files("agentos.migrations")
    entries = tuple(
        _load_entry(version, name, package.joinpath(name).read_bytes())
        for version, name in _CATALOG
    )
    return MigrationPlan(entries=entries, target_version=entries[-1].version)


def _load_entry(version: int, name: str, source: bytes) -> MigrationEntry:
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if normalized.count(_UP_MARKER) != 1 or normalized.count(_DOWN_MARKER) != 1:
        raise ValueError(f"migration markers are invalid: {name}")
    before_down, down_marker, _ = normalized.partition(_DOWN_MARKER)
    _, up_marker, up_sql = before_down.partition(_UP_MARKER)
    if not up_marker or not down_marker or not up_sql.strip():
        raise ValueError(f"migration up section is invalid: {name}")
    return MigrationEntry(
        version=version,
        name=name,
        sha256=sha256(up_sql).hexdigest(),
        up_sql=up_sql.decode("utf-8"),
    )


@dataclass(frozen=True, slots=True)
class DistributedMigrationService:
    """持有 canonical plan 并协调迁移端口的应用服务。"""

    port: DistributedMigrationPort
    plan: MigrationPlan

    async def check(self) -> MigrationReport:
        return await self.port.check(self.plan)

    async def apply(self) -> MigrationReport:
        return await self.port.apply(self.plan)


__all__ = ["DistributedMigrationService", "canonical_migration_plan"]
