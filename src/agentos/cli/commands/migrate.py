from __future__ import annotations

from typing import TextIO

from agentos.cli.application import CliHostFactory
from agentos.cli.output import write_json_line
from agentos.distributed.migrations.models import MigrationReport


async def run_migrate(
    host_factory: CliHostFactory,
    *,
    check: bool,
    stdout: TextIO,
) -> None:
    """通过独立 migration host 检查或应用 canonical schema 迁移。"""

    if type(check) is not bool:
        raise TypeError("check must be bool")
    async with host_factory.open_migration_host() as host:
        report = (
            await host.migrations.check()
            if check
            else await host.migrations.apply()
        )
    if type(report) is not MigrationReport:
        raise RuntimeError("migration service returned an invalid report")
    write_json_line(
        {
            "current_version": report.current_version,
            "target_version": report.target_version,
            "applied_versions": list(report.applied_versions),
            "changed": report.changed,
        },
        stream=stdout,
    )


__all__ = ["run_migrate"]
