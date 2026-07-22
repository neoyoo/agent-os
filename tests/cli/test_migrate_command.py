from __future__ import annotations

from contextlib import asynccontextmanager
from io import StringIO

from agentos.cli.commands.migrate import run_migrate
from agentos.distributed.migrations.models import MigrationReport
from tests.planning._async import async_test


class _Migrations:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def check(self) -> MigrationReport:
        self.calls.append("check")
        return MigrationReport(2, 2, (), False)

    async def apply(self) -> MigrationReport:
        self.calls.append("apply")
        return MigrationReport(2, 2, (1, 2), True)


class _Factory:
    def __init__(self) -> None:
        self.migrations = _Migrations()
        self.opened: list[str] = []
        self.closed: list[str] = []

    @asynccontextmanager
    async def open_migration_host(self):  # type: ignore[no-untyped-def]
        self.opened.append("migration")
        try:
            yield type("Host", (), {"migrations": self.migrations})()
        finally:
            self.closed.append("migration")


@async_test
async def test_migrate_check_uses_only_read_only_migration_host() -> None:
    factory = _Factory()
    stdout = StringIO()

    await run_migrate(factory, check=True, stdout=stdout)  # type: ignore[arg-type]

    assert factory.migrations.calls == ["check"]
    assert factory.opened == ["migration"]
    assert factory.closed == ["migration"]
    assert stdout.getvalue() == (
        '{"applied_versions":[],"changed":false,'
        '"current_version":2,"target_version":2}\n'
    )


@async_test
async def test_migrate_apply_reports_exact_applied_versions() -> None:
    factory = _Factory()
    stdout = StringIO()

    await run_migrate(factory, check=False, stdout=stdout)  # type: ignore[arg-type]

    assert factory.migrations.calls == ["apply"]
    assert stdout.getvalue() == (
        '{"applied_versions":[1,2],"changed":true,'
        '"current_version":2,"target_version":2}\n'
    )
