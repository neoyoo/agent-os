from __future__ import annotations

from typing import Protocol

from agentos.distributed.migrations.models import MigrationPlan, MigrationReport


class DistributedMigrationPort(Protocol):
    """定义 schema 精确检查和原子迁移的基础设施端口。"""

    async def check(self, plan: MigrationPlan) -> MigrationReport: ...

    async def apply(self, plan: MigrationPlan) -> MigrationReport: ...


__all__ = ["DistributedMigrationPort"]
