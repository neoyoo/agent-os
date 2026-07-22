from __future__ import annotations

from collections.abc import Sequence

from agentos.distributed.errors import (
    LegacyDistributedSchemaError,
    MigrationChecksumMismatchError,
    MigrationVersionError,
    SchemaMigrationRequiredError,
)
from agentos.distributed.migrations.models import MigrationPlan, MigrationReport
from agentos.distributed.postgres._database import (
    AsyncConnection,
    PostgresPool,
    Row,
    fetchall,
    fetchone,
)


_LOCK_NAME = "agentos.schema.migrations"
_LEDGER = "agentos_schema_migrations"
_LEGACY_MARKER = "agentos_distributed_schema"


class PostgresMigrationPort:
    """在 PostgreSQL 单事务边界检查或应用 canonical migration plan。"""

    def __init__(self, database: PostgresPool) -> None:
        self._database = database

    async def check(self, plan: MigrationPlan) -> MigrationReport:
        async with self._database.connection() as connection:
            ledger_exists = await _require_supported_relations(connection)
            if not ledger_exists:
                raise SchemaMigrationRequiredError()
            rows = await _ledger_rows(connection)
        current_version = _validate_history(rows, plan)
        if current_version != plan.target_version:
            raise SchemaMigrationRequiredError()
        return MigrationReport(
            current_version=current_version,
            target_version=plan.target_version,
            applied_versions=(),
            changed=False,
        )

    async def apply(self, plan: MigrationPlan) -> MigrationReport:
        async with self._database.transaction() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (_LOCK_NAME,),
            )
            ledger_exists = await _require_supported_relations(connection)
            if not ledger_exists:
                await connection.execute(
                    """
                    CREATE TABLE agentos_schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        sha256 CHAR(64) NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL
                            DEFAULT clock_timestamp()
                    )
                    """,
                )
            current_version = _validate_history(
                await _ledger_rows(connection),
                plan,
            )
            pending = plan.entries[current_version:]
            for entry in pending:
                await connection.execute(entry.up_sql)
                await connection.execute(
                    """
                    INSERT INTO agentos_schema_migrations
                        (version, name, sha256)
                    VALUES (%s, %s, %s)
                    """,
                    (entry.version, entry.name, entry.sha256),
                )
        applied_versions = tuple(entry.version for entry in pending)
        return MigrationReport(
            current_version=plan.target_version,
            target_version=plan.target_version,
            applied_versions=applied_versions,
            changed=bool(applied_versions),
        )


async def _require_supported_relations(connection: AsyncConnection) -> bool:
    row = await fetchone(
        connection,
        """
        SELECT
            to_regclass(%s) AS ledger,
            to_regclass(%s) AS legacy
        """,
        (_LEDGER, _LEGACY_MARKER),
    )
    if row is None:
        raise SchemaMigrationRequiredError()
    if row["legacy"] is not None:
        raise LegacyDistributedSchemaError()
    return row["ledger"] is not None


async def _ledger_rows(connection: AsyncConnection) -> list[Row]:
    return await fetchall(
        connection,
        """
        SELECT version, name, sha256
        FROM agentos_schema_migrations
        ORDER BY version
        """,
    )


def _validate_history(rows: Sequence[Row], plan: MigrationPlan) -> int:
    versions = [row.get("version") for row in rows]
    if versions != list(range(1, len(rows) + 1)):
        raise MigrationVersionError()
    if len(rows) > plan.target_version:
        raise MigrationVersionError()
    for row, entry in zip(rows, plan.entries):
        if row.get("name") != entry.name:
            raise MigrationVersionError()
        if row.get("sha256") != entry.sha256:
            raise MigrationChecksumMismatchError()
    return len(rows)


__all__ = ["PostgresMigrationPort"]
