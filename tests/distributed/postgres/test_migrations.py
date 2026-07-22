from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

import pytest

from agentos.distributed.errors import (
    LegacyDistributedSchemaError,
    MigrationChecksumMismatchError,
    MigrationVersionError,
    SchemaMigrationRequiredError,
)
from agentos.distributed.migrations.service import canonical_migration_plan
from agentos.distributed.postgres.migrations import PostgresMigrationPort
from tests.planning._async import async_test


class _Cursor:
    def __init__(self, rows: list[Mapping[str, object]]) -> None:
        self._rows = rows

    async def fetchone(self) -> Mapping[str, object] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[Mapping[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    async def execute(
        self,
        query: str,
        params: Sequence[object] = (),
    ) -> _Cursor:
        normalized = " ".join(query.split())
        self.database.trace.append((normalized, tuple(params)))
        if "pg_advisory_xact_lock" in normalized:
            return _Cursor([])
        if "to_regclass" in normalized:
            return _Cursor(
                [
                    {
                        "ledger": (
                            "agentos_schema_migrations"
                            if self.database.ledger_exists
                            else None
                        ),
                        "legacy": (
                            "agentos_distributed_schema"
                            if self.database.legacy_exists
                            else None
                        ),
                    },
                ],
            )
        if normalized.startswith("CREATE TABLE agentos_schema_migrations"):
            self.database.ledger_exists = True
            return _Cursor([])
        if normalized.startswith("SELECT version, name, sha256"):
            return _Cursor(list(self.database.rows))
        if normalized.startswith("INSERT INTO agentos_schema_migrations"):
            version, name, digest = params
            self.database.rows.append(
                {"version": version, "name": name, "sha256": digest},
            )
            return _Cursor([])
        if "agentos_team_deliveries" in query:
            self.database.applied.append(2)
            if self.database.fail_version == 2:
                raise RuntimeError("migration failed")
            return _Cursor([])
        if "agentos_distributed_sessions" in query:
            self.database.applied.append(1)
            if self.database.fail_version == 1:
                raise RuntimeError("migration failed")
            return _Cursor([])
        raise AssertionError(f"unexpected SQL: {normalized}")


class _Database:
    def __init__(
        self,
        *,
        ledger_exists: bool = False,
        legacy_exists: bool = False,
        rows: list[Mapping[str, object]] | None = None,
        fail_version: int | None = None,
    ) -> None:
        self.ledger_exists = ledger_exists
        self.legacy_exists = legacy_exists
        self.rows = list(rows or [])
        self.fail_version = fail_version
        self.applied: list[int] = []
        self.trace: list[tuple[str, tuple[object, ...]]] = []
        self.transactions: list[str] = []
        self.connection_instance = _Connection(self)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_Connection]:
        yield self.connection_instance

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_Connection]:
        snapshot = (self.ledger_exists, list(self.rows), list(self.applied))
        self.transactions.append("begin")
        try:
            yield self.connection_instance
        except BaseException:
            self.ledger_exists, self.rows, self.applied = snapshot
            self.transactions.append("rollback")
            raise
        self.transactions.append("commit")


def _recorded_rows(*versions: int) -> list[Mapping[str, object]]:
    plan = canonical_migration_plan()
    return [
        {
            "version": entry.version,
            "name": entry.name,
            "sha256": entry.sha256,
        }
        for entry in plan.entries
        if entry.version in versions
    ]


@async_test
async def test_check_is_read_only_and_requires_exact_target() -> None:
    database = _Database()
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    with pytest.raises(SchemaMigrationRequiredError):
        await port.check(canonical_migration_plan())

    assert database.transactions == []
    assert database.ledger_exists is False
    assert database.applied == []


@async_test
async def test_apply_bootstraps_ledger_and_applies_full_catalog_atomically() -> None:
    database = _Database()
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    report = await port.apply(canonical_migration_plan())

    assert report.current_version == 2
    assert report.target_version == 2
    assert report.applied_versions == (1, 2)
    assert report.changed is True
    assert database.transactions == ["begin", "commit"]
    assert [row["version"] for row in database.rows] == [1, 2]
    assert database.applied == [1, 2]
    assert "pg_advisory_xact_lock" in database.trace[0][0]
    assert "to_regclass" in database.trace[1][0]


@async_test
async def test_apply_rolls_back_every_version_when_later_migration_fails() -> None:
    database = _Database(fail_version=2)
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="migration failed"):
        await port.apply(canonical_migration_plan())

    assert database.transactions == ["begin", "rollback"]
    assert database.ledger_exists is False
    assert database.rows == []
    assert database.applied == []


@pytest.mark.parametrize("ledger_exists", [False, True])
@async_test
async def test_legacy_marker_always_fails_closed(ledger_exists: bool) -> None:
    database = _Database(
        ledger_exists=ledger_exists,
        legacy_exists=True,
        rows=_recorded_rows(1, 2) if ledger_exists else None,
    )
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    with pytest.raises(LegacyDistributedSchemaError):
        await port.apply(canonical_migration_plan())

    assert database.transactions == ["begin", "rollback"]
    assert database.applied == []


@async_test
async def test_recorded_checksum_mismatch_fails_before_sql() -> None:
    rows = _recorded_rows(1)
    rows[0] = {**rows[0], "sha256": "0" * 64}
    database = _Database(ledger_exists=True, rows=rows)
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    with pytest.raises(MigrationChecksumMismatchError):
        await port.apply(canonical_migration_plan())

    assert database.applied == []
    assert database.transactions == ["begin", "rollback"]


@pytest.mark.parametrize(
    "rows",
    [
        [{"version": 2, "name": "v2.sql", "sha256": "0" * 64}],
        [
            *_recorded_rows(1, 2),
            {"version": 3, "name": "future.sql", "sha256": "0" * 64},
        ],
    ],
)
@async_test
async def test_version_gap_and_database_ahead_fail_closed(
    rows: list[Mapping[str, object]],
) -> None:
    database = _Database(ledger_exists=True, rows=rows)
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    with pytest.raises(MigrationVersionError):
        await port.apply(canonical_migration_plan())

    assert database.applied == []


@async_test
async def test_exact_target_check_reports_no_change() -> None:
    database = _Database(ledger_exists=True, rows=_recorded_rows(1, 2))
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    report = await port.check(canonical_migration_plan())

    assert report.current_version == 2
    assert report.applied_versions == ()
    assert report.changed is False
    assert database.transactions == []


@async_test
async def test_exact_target_apply_is_an_atomic_no_op() -> None:
    database = _Database(ledger_exists=True, rows=_recorded_rows(1, 2))
    port = PostgresMigrationPort(database)  # type: ignore[arg-type]

    report = await port.apply(canonical_migration_plan())

    assert report.current_version == 2
    assert report.applied_versions == ()
    assert report.changed is False
    assert database.transactions == ["begin", "commit"]
    assert database.applied == []
