"""Test-only schema view derived from the canonical migration catalog."""

from agentos.distributed.migrations.service import canonical_migration_plan


def _v1_statements() -> tuple[str, ...]:
    v1 = canonical_migration_plan().entries[0]
    return tuple(
        statement.strip()
        for statement in v1.up_sql.split(";")
        if statement.strip()
    )


SCHEMA_STATEMENTS = _v1_statements()


__all__ = ["SCHEMA_STATEMENTS"]
