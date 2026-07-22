from pathlib import Path

from agentos.distributed.postgres.schema import SCHEMA_STATEMENTS


_ROOT = Path(__file__).resolve().parents[3]
_V1_MIGRATION = "2026-07-20-postgres-distributed-runtime.sql"
_V2_MIGRATION = "2026-07-21-postgres-team-delivery.sql"


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


def test_checkpoint_latest_query_has_matching_partial_index() -> None:
    schema = " ".join("\n".join(SCHEMA_STATEMENTS).lower().split())

    assert "agentos_distributed_checkpoint_latest" in schema
    assert (
        "on agentos_distributed_checkpoints "
        "( tenant_id, session_id, checkpoint_sequence desc ) "
        "where snapshot_json is not null"
    ) in schema


def test_phase6_migration_is_packaged_without_drift() -> None:
    documented = (_ROOT / "docs" / "migrations" / _V1_MIGRATION).read_text(
        encoding="utf-8",
    )
    packaged = (_ROOT / "src" / "agentos" / "migrations" / _V1_MIGRATION).read_text(
        encoding="utf-8",
    )

    assert documented == packaged
    assert "-- migrate:up" in documented
    assert "-- migrate:down" in documented
    assert "agentos_distributed_schema" not in documented
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
    migration = (_ROOT / "docs" / "migrations" / _V1_MIGRATION).read_text(
        encoding="utf-8",
    )
    up = migration.split("-- migrate:up", 1)[1].split("-- migrate:down", 1)[0]
    statements = tuple(
        statement.strip()
        for statement in up.split(";")
        if statement.strip()
    )

    assert len(statements) == len(SCHEMA_STATEMENTS)
    for deployed, runtime in zip(statements, SCHEMA_STATEMENTS, strict=True):
        assert " ".join(deployed.split()) == " ".join(runtime.split())


def test_team_delivery_migration_is_packaged_without_drift() -> None:
    documented = (_ROOT / "docs" / "migrations" / _V2_MIGRATION).read_text(
        encoding="utf-8",
    )
    packaged = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")

    assert documented == packaged
    for table in (
        "agentos_teams",
        "agentos_team_members",
        "agentos_team_messages",
        "agentos_team_deliveries",
        "agentos_team_events",
    ):
        assert f"CREATE TABLE {table}" in documented
    assert "target_session_id TEXT NOT NULL" in documented
    assert "fencing_token BIGINT NOT NULL" in documented


def test_team_message_migration_persists_operation_identity() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    messages = migration.split("CREATE TABLE agentos_team_messages", 1)[1].split(
        "CREATE INDEX agentos_team_messages_order",
        1,
    )[0]

    assert "operation_id TEXT NOT NULL" in messages
    assert "UNIQUE (tenant_id, team_id, operation_id)" in messages
    assert (
        "message_kind IN ('instruction', 'observation', 'result', 'notice')"
        in messages
    )
    assert "octet_length(content) BETWEEN 1 AND 4096" in messages
    teams = migration.split("CREATE TABLE agentos_teams", 1)[1].split(
        "CREATE TABLE agentos_team_members",
        1,
    )[0]
    assert "workspace_id TEXT" in teams
    assert "workspace TEXT" not in teams
    assert "agentos_team_members_one_leader" in migration
    assert "WHERE role = 'leader'" in migration
    assert "jsonb_array_length(capabilities_json) <= 32" in migration


def test_team_migration_enforces_delivery_result_and_event_kinds() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")

    for value in (
        "internal_start",
        "wakeup",
        "rejected_binding_revoked",
        "rejected_nonterminal",
    ):
        assert f"'{value}'" in migration.split(
            "CREATE TABLE agentos_team_deliveries",
            1,
        )[1].split("CREATE INDEX agentos_team_deliveries_pending", 1)[0]
    events = migration.split("CREATE TABLE agentos_team_events", 1)[1].split(
        "CREATE TABLE agentos_team_outbox",
        1,
    )[0]
    event_scope = (
        " ".join(events.lower().split())
        .replace(", ", ",")
        .replace("( ", "(")
        .replace(" )", ")")
    )
    assert "event_kind IN ('delivery_applied', 'delivery_rejected')" in events
    assert "delivery_id text not null" in event_scope
    assert "unique (tenant_id,delivery_id)" in event_scope
    assert (
        "foreign key (tenant_id,delivery_id,team_id) references "
        "agentos_team_deliveries(tenant_id,delivery_id,team_id)"
    ) in event_scope
    deliveries = " ".join(
        migration.split("CREATE TABLE agentos_team_deliveries", 1)[1]
        .split("CREATE INDEX agentos_team_deliveries_pending", 1)[0]
        .lower()
        .split()
    ).replace(", ", ",")
    assert "unique (tenant_id,delivery_id,team_id)" in deliveries
    assert "CHECK (state = 'pending' OR fencing_token > 0)" in migration
    matrix = " ".join(
        migration.split("CREATE TABLE agentos_team_deliveries", 1)[1]
        .split("CREATE INDEX agentos_team_deliveries_pending", 1)[0]
        .lower()
        .split(),
    )
    assert "state = 'applied' and result_kind in ('internal_start', 'wakeup')" in matrix
    assert (
        "state = 'rejected' and result_kind = 'rejected_binding_revoked'"
        in matrix
    )
    assert "state = 'rejected' and result_kind = 'rejected_nonterminal'" in matrix
    assert "observed_run_status in ('created', 'queued', 'running', 'waiting')" in matrix


def test_team_migration_uses_monotonic_message_sequence_for_pagination() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    messages = migration.split("CREATE TABLE agentos_team_messages", 1)[1].split(
        "CREATE TABLE agentos_team_deliveries",
        1,
    )[0]
    compact = " ".join(messages.lower().split()).replace(", ", ",")

    assert "message_sequence bigint generated always as identity" in compact
    assert "unique (message_sequence)" in compact
    assert (
        "on agentos_team_messages (tenant_id,team_id,message_sequence)"
        in compact
    )


def test_team_migration_extends_accepted_input_for_internal_start() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    up = " ".join(
        migration.split("-- migrate:up", 1)[1]
        .split("-- migrate:down", 1)[0]
        .lower()
        .split()
    )

    assert "alter table agentos_distributed_submissions" in up
    assert "source_kind text" in up
    assert "source_payload_json text" in up
    assert "team_delivery_id text" in up
    assert "input_kind text generated always as" in up
    assert "alter table agentos_distributed_accepted_inputs" in up
    assert "when 'team_message' then 'internal_start'" in up
    assert "source_kind in ('submission', 'command', 'team_message')" in up
    assert "octet_length(payload_json) <= 4096" in up


def test_team_migration_persists_trusted_run_input_provenance() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    up, down = migration.split("-- migrate:down", 1)
    compact = " ".join(up.lower().split()).replace(", ", ",")
    down_compact = " ".join(down.lower().split())

    commands = compact.split(
        "alter table agentos_distributed_commands",
        1,
    )[1].split("alter table agentos_distributed_accepted_inputs", 1)[0]
    accepted = compact.split(
        "alter table agentos_distributed_accepted_inputs",
        1,
    )[1].split("alter table agentos_distributed_outbox", 1)[0]

    assert "add column team_delivery_id text" in commands
    assert "foreign key (tenant_id,team_delivery_id,session_id)" in commands
    assert "team_delivery_id is null or kind = 'wakeup'" in commands
    assert "add column team_delivery_id text" in accepted
    assert "foreign key (tenant_id,team_delivery_id,session_id)" in accepted
    assert "source_kind = 'submission'" in accepted
    assert "team_delivery_id is null" in accepted
    assert "continuation_kind = 'wakeup'" in accepted
    assert "source_kind = 'team_message'" in accepted
    assert "team_delivery_id is not null" in accepted
    assert (
        "create unique index agentos_distributed_accepted_inputs_team_delivery"
        in compact
    )
    assert "where team_delivery_id is not null" in compact
    assert down_compact.count("drop column team_delivery_id") == 4


def test_team_migration_down_preserves_team_bound_input_truth() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    down = migration.split("-- migrate:down", 1)[1].strip().lower()
    compact = " ".join(down.split())

    assert compact.startswith("lock table agentos_distributed_commands")
    lock = (
        "lock table agentos_distributed_commands, "
        "agentos_distributed_submissions, agentos_distributed_accepted_inputs "
        "in access exclusive mode;"
    )
    assert lock in compact
    guard = compact.split("do $$", 1)[1].split("$$;", 1)[0]
    assert (
        "from agentos_distributed_accepted_inputs "
        "where team_delivery_id is not null"
    ) in guard
    assert (
        "from agentos_distributed_commands where team_delivery_id is not null"
    ) in guard
    assert (
        "from agentos_distributed_submissions where team_delivery_id is not null"
    ) in guard
    assert "errcode = 'dependent_objects_still_exist'" in guard
    assert compact.index(lock) < compact.index("do $$")
    assert compact.index("$$;") < compact.index(
        "delete from agentos_distributed_outbox"
    )


def test_v1_names_accepted_input_constraints_for_v2_evolution() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V1_MIGRATION
    ).read_text(encoding="utf-8")
    up = " ".join(
        migration.split("-- migrate:up", 1)[1]
        .split("-- migrate:down", 1)[0]
        .lower()
        .split()
    )

    accepted = up.split(
        "create table if not exists agentos_distributed_accepted_inputs",
        1,
    )[1].split(
        "create unique index if not exists agentos_distributed_one_pending_input",
        1,
    )[0]
    assert "agentos_distributed_accepted_inputs_claim_check" in accepted
    assert "agentos_distributed_accepted_inputs_shape_check" in accepted

    push_deliveries = up.split(
        "create table if not exists agentos_distributed_a2a_push_deliveries",
        1,
    )[1].split(
        "create index if not exists agentos_distributed_a2a_push_order",
        1,
    )[0]
    assert "agentos_distributed_a2a_push_deliveries_claim_check" in push_deliveries
    assert "agentos_distributed_a2a_push_deliveries_terminal_check" in push_deliveries
    assert "agentos_distributed_accepted_inputs_shape_check" not in push_deliveries


def test_team_migration_allows_team_outbox_before_run_exists() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    up = " ".join(
        migration.split("-- migrate:up", 1)[1]
        .split("-- migrate:down", 1)[0]
        .lower()
        .split()
    )

    assert "alter table agentos_distributed_outbox" in up
    assert "alter column run_id drop not null" in up
    assert "team_delivery_id text" in up
    assert "foreign key (tenant_id, team_delivery_id, session_id)" in up
    assert "run_id is null and team_delivery_id is not null" in up


def test_team_migration_enforces_complete_delivery_scope() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    up = " ".join(
        migration.split("-- migrate:up", 1)[1]
        .split("-- migrate:down", 1)[0]
        .lower()
        .split()
    )
    compact = up.replace("( ", "(").replace(" )", ")").replace(", ", ",")

    assert "unique (tenant_id,team_id,message_id)" in compact
    assert (
        "unique (tenant_id,team_id,recipient_agent_id,target_session_id)"
        in compact
    )
    assert "unique (tenant_id,delivery_id,target_session_id)" in compact
    assert (
        "foreign key (tenant_id,team_id,message_id) references "
        "agentos_team_messages(tenant_id,team_id,message_id)"
    ) in compact
    assert (
        "foreign key (tenant_id,team_id,recipient_agent_id,target_session_id) "
        "references agentos_team_members(tenant_id,team_id,recipient_agent_id,"
        "target_session_id)"
    ) in compact
    assert compact.count(
        "foreign key (tenant_id,team_delivery_id,session_id) references "
        "agentos_team_deliveries(tenant_id,delivery_id,target_session_id)"
    ) == 4


def test_team_delivery_records_complete_observed_run_evidence() -> None:
    migration = (
        _ROOT / "src" / "agentos" / "migrations" / _V2_MIGRATION
    ).read_text(encoding="utf-8")
    up = " ".join(
        migration.split("-- migrate:up", 1)[1]
        .split("-- migrate:down", 1)[0]
        .lower()
        .split()
    )

    assert "observed_run_id text" in up
    assert "observed_aggregate_version bigint" in up
    assert "observed_run_status text" in up
    assert (
        "observed_run_status in ( 'created', 'queued', 'running', 'waiting', "
        "'completed', 'failed', 'cancelled' )"
    ) in up
    assert "state = 'applied'" in up
