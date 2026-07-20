from pathlib import Path

from agentos.distributed.postgres.schema import SCHEMA_STATEMENTS


_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_NAME = "2026-07-20-postgres-distributed-runtime.sql"


def test_schema_has_tenant_scoped_active_run_truth_constraint() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS).lower()

    assert "agentos_distributed_one_active_run" in schema
    assert "on agentos_distributed_runs (tenant_id, session_id)" in schema
    assert "where status in ('created', 'queued', 'running', 'waiting')" in schema


def test_schema_keeps_fence_claim_input_outbox_and_ledger_truth() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS).lower()

    for fragment in (
        "fencing_token",
        "active_claim_expires_at",
        "agentos_distributed_accepted_inputs",
        "agentos_distributed_execution_cursors",
        "agentos_distributed_outbox",
        "agentos_distributed_side_effects",
        "agentos_distributed_artifacts",
    ):
        assert fragment in schema
    assert "clock_timestamp()" in schema


def test_schema_scopes_idempotency_keys_by_tenant() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS).lower()

    assert "primary key (tenant_id, submission_id)" in schema
    assert "primary key (tenant_id, command_id)" in schema
    assert "unique (tenant_id, upload_id)" in schema
    assert "agentos_distributed_artifact_deletions" in schema
    assert "primary key (tenant_id, deletion_id)" in schema


def test_accepted_input_preserves_authoritative_principal() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS).lower()
    accepted = schema.split(
        "create table if not exists agentos_distributed_accepted_inputs",
        1,
    )[1].split(")\n", 1)[0]

    assert "principal_id text not null" in accepted


def test_checkpoint_uses_database_monotonic_session_order() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS).lower()

    assert "checkpoint_sequence bigint generated always as identity unique" in schema


def test_phase6_migration_is_packaged_without_drift() -> None:
    documented = (_ROOT / "docs" / "migrations" / _MIGRATION_NAME).read_text(
        encoding="utf-8",
    )
    packaged = (_ROOT / "src" / "agentos" / "migrations" / _MIGRATION_NAME).read_text(
        encoding="utf-8",
    )

    assert documented == packaged
    assert "-- migrate:up" in documented
    assert "-- migrate:down" in documented
    for table in (
        "agentos_distributed_sessions",
        "agentos_distributed_runs",
        "agentos_distributed_artifacts",
        "agentos_distributed_artifact_deletions",
        "agentos_distributed_submissions",
        "agentos_distributed_commands",
        "agentos_distributed_accepted_inputs",
        "agentos_distributed_checkpoints",
        "agentos_distributed_execution_cursors",
        "agentos_distributed_outbox",
        "agentos_distributed_side_effects",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in documented


def test_phase6_migration_create_statements_match_runtime_schema() -> None:
    migration = (_ROOT / "docs" / "migrations" / _MIGRATION_NAME).read_text(
        encoding="utf-8",
    )
    up = migration.split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]
    statements = tuple(
        statement.strip()
        for statement in up.split(";")
        if statement.strip()
    )

    assert len(statements) == len(SCHEMA_STATEMENTS) + 1
    for deployed, runtime in zip(
        statements[:len(SCHEMA_STATEMENTS)],
        SCHEMA_STATEMENTS,
        strict=True,
    ):
        assert " ".join(deployed.split()) == " ".join(runtime.split())
    assert statements[-1].startswith(
        "INSERT INTO agentos_distributed_schema (version)",
    )
