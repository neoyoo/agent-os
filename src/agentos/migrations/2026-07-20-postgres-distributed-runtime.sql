-- migrate:up

CREATE TABLE IF NOT EXISTS agentos_distributed_schema (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS agentos_distributed_sessions (
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    next_turn_number BIGINT NOT NULL CHECK (next_turn_number > 0),
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    active_claim_id TEXT,
    active_claim_owner_id TEXT,
    active_claim_run_id TEXT,
    active_claim_expires_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, session_id),
    CHECK (
        (active_claim_id IS NULL
            AND active_claim_owner_id IS NULL
            AND active_claim_run_id IS NULL
            AND active_claim_expires_at IS NULL)
        OR
        (active_claim_id IS NOT NULL
            AND active_claim_owner_id IS NOT NULL
            AND active_claim_run_id IS NOT NULL
            AND active_claim_expires_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS agentos_distributed_runs (
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('created', 'queued', 'running', 'waiting',
                   'completed', 'failed', 'cancelled')
    ),
    wait_kind TEXT,
    wait_handle TEXT,
    wait_detail TEXT,
    wait_not_before TIMESTAMPTZ,
    aggregate_version BIGINT NOT NULL CHECK (aggregate_version >= 0),
    result_content TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, session_id, run_id),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES agentos_distributed_sessions(tenant_id, session_id),
    CHECK (
        (status = 'waiting' AND wait_kind IS NOT NULL AND wait_handle IS NOT NULL)
        OR
        (status <> 'waiting' AND wait_kind IS NULL AND wait_handle IS NULL
            AND wait_detail IS NULL AND wait_not_before IS NULL)
    ),
    CHECK (
        (status = 'completed' AND result_content IS NOT NULL)
        OR
        (status <> 'completed' AND result_content IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS agentos_distributed_one_active_run
ON agentos_distributed_runs (tenant_id, session_id)
WHERE status IN ('created', 'queued', 'running', 'waiting');

CREATE TABLE IF NOT EXISTS agentos_distributed_a2a_tasks (
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, task_id),
    UNIQUE (tenant_id, run_id),
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id)
);

CREATE TABLE IF NOT EXISTS agentos_distributed_a2a_push_configs (
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    url TEXT NOT NULL,
    authentication_scheme TEXT,
    secret_token TEXT,
    secret_digest TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, task_id, config_id),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES agentos_distributed_a2a_tasks(tenant_id, task_id),
    CHECK ((secret_token IS NULL) = (secret_digest IS NULL))
);

CREATE TABLE IF NOT EXISTS agentos_distributed_a2a_push_operations (
    tenant_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK (operation_kind IN ('create', 'delete')),
    task_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    result_url TEXT,
    result_authentication_scheme TEXT,
    result_secret_token TEXT,
    result_secret_digest TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, operation_id),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES agentos_distributed_a2a_tasks(tenant_id, task_id),
    CHECK (
        (operation_kind = 'create' AND result_url IS NOT NULL)
        OR
        (operation_kind = 'delete' AND result_url IS NULL
            AND result_authentication_scheme IS NULL
            AND result_secret_token IS NULL
            AND result_secret_digest IS NULL)
    ),
    CHECK ((result_secret_token IS NULL) = (result_secret_digest IS NULL))
);

CREATE TABLE IF NOT EXISTS agentos_distributed_artifacts (
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    upload_id TEXT NOT NULL,
    filename TEXT,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    content_digest TEXT NOT NULL,
    blob_key TEXT NOT NULL,
    lifecycle TEXT NOT NULL CHECK (
        lifecycle IN ('staging', 'active', 'tombstoned', 'deleted')
    ),
    deletion_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, session_id, artifact_id),
    UNIQUE (tenant_id, upload_id),
    UNIQUE (blob_key),
    FOREIGN KEY (tenant_id, session_id)
        REFERENCES agentos_distributed_sessions(tenant_id, session_id)
);

CREATE INDEX IF NOT EXISTS agentos_distributed_artifact_page
ON agentos_distributed_artifacts (
    tenant_id, session_id, lifecycle, created_at DESC, artifact_id DESC
);

CREATE TABLE IF NOT EXISTS agentos_distributed_artifact_deletions (
    tenant_id TEXT NOT NULL,
    deletion_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, deletion_id),
    FOREIGN KEY (tenant_id, session_id, artifact_id)
        REFERENCES agentos_distributed_artifacts(
            tenant_id, session_id, artifact_id
        )
);

CREATE TABLE IF NOT EXISTS agentos_distributed_submissions (
    tenant_id TEXT NOT NULL,
    submission_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    content TEXT NOT NULL,
    artifact_handles JSONB NOT NULL,
    turn_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    aggregate_version BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, submission_id),
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id)
);

CREATE TABLE IF NOT EXISTS agentos_distributed_commands (
    tenant_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    turn_id TEXT,
    aggregate_version BIGINT NOT NULL,
    fencing_token BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, command_id),
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id)
);

CREATE TABLE IF NOT EXISTS agentos_distributed_accepted_inputs (
    tenant_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('submission', 'command')),
    source_id TEXT NOT NULL,
    continuation_kind TEXT,
    payload_json TEXT,
    content TEXT,
    artifact_handles JSONB,
    user_message_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'claimed', 'committed')),
    claim_id TEXT,
    fencing_token BIGINT,
    checkpoint_id TEXT,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, session_id, run_id, turn_id),
    UNIQUE (tenant_id, source_kind, source_id),
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id),
    CHECK (
        (status = 'claimed' AND claim_id IS NOT NULL AND fencing_token IS NOT NULL)
        OR
        (status <> 'claimed' AND claim_id IS NULL AND fencing_token IS NULL)
    ),
    CHECK (
        (source_kind = 'submission' AND content IS NOT NULL
            AND artifact_handles IS NOT NULL AND user_message_id IS NOT NULL
            AND continuation_kind IS NULL AND payload_json IS NULL)
        OR
        (source_kind = 'command' AND content IS NULL
            AND artifact_handles IS NULL AND user_message_id IS NULL
            AND continuation_kind IS NOT NULL AND payload_json IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS agentos_distributed_one_pending_input
ON agentos_distributed_accepted_inputs (tenant_id, session_id, run_id)
WHERE status IN ('accepted', 'claimed');

CREATE TABLE IF NOT EXISTS agentos_distributed_checkpoints (
    checkpoint_sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    tenant_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    aggregate_version BIGINT NOT NULL,
    snapshot_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    fencing_token BIGINT NOT NULL,
    PRIMARY KEY (tenant_id, checkpoint_id),
    UNIQUE (tenant_id, session_id, run_id, aggregate_version),
    UNIQUE (tenant_id, session_id, run_id, checkpoint_id),
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id)
);

CREATE INDEX IF NOT EXISTS agentos_distributed_checkpoint_latest
ON agentos_distributed_checkpoints (
    tenant_id, session_id, checkpoint_sequence DESC
)
WHERE snapshot_json IS NOT NULL;

CREATE TABLE IF NOT EXISTS agentos_distributed_execution_cursors (
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, session_id, run_id),
    FOREIGN KEY (tenant_id, checkpoint_id)
        REFERENCES agentos_distributed_checkpoints(tenant_id, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS agentos_distributed_reconciliation_sources (
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    waiting_aggregate_version BIGINT NOT NULL CHECK (
        waiting_aggregate_version > 0
    ),
    source_checkpoint_id TEXT NOT NULL,
    waiting_checkpoint_id TEXT NOT NULL,
    cursor_payload_json TEXT NOT NULL,
    resolution_command_id TEXT,
    consumed_checkpoint_id TEXT,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        tenant_id, session_id, run_id, waiting_aggregate_version
    ),
    UNIQUE (tenant_id, resolution_command_id),
    FOREIGN KEY (tenant_id, session_id, run_id, source_checkpoint_id)
        REFERENCES agentos_distributed_checkpoints(
            tenant_id, session_id, run_id, checkpoint_id
        ),
    FOREIGN KEY (tenant_id, session_id, run_id, waiting_checkpoint_id)
        REFERENCES agentos_distributed_checkpoints(
            tenant_id, session_id, run_id, checkpoint_id
        ),
    FOREIGN KEY (tenant_id, resolution_command_id)
        REFERENCES agentos_distributed_commands(tenant_id, command_id),
    FOREIGN KEY (tenant_id, session_id, run_id, consumed_checkpoint_id)
        REFERENCES agentos_distributed_checkpoints(
            tenant_id, session_id, run_id, checkpoint_id
        ),
    CHECK (source_checkpoint_id <> waiting_checkpoint_id),
    CHECK (
        (consumed_checkpoint_id IS NULL AND consumed_at IS NULL)
        OR
        (consumed_checkpoint_id IS NOT NULL AND consumed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS agentos_distributed_outbox (
    outbox_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    last_publish_attempt_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    queue_entry_id TEXT,
    claim_owner_id TEXT,
    claim_id TEXT,
    claim_expires_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id),
    CHECK (
        (claim_owner_id IS NULL AND claim_id IS NULL AND claim_expires_at IS NULL)
        OR
        (claim_owner_id IS NOT NULL AND claim_id IS NOT NULL
            AND claim_expires_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS agentos_distributed_a2a_push_deliveries (
    tenant_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    outbox_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    context_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    url TEXT NOT NULL,
    authentication_scheme TEXT,
    secret_token TEXT,
    secret_digest TEXT,
    event_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL CHECK (protocol_version = '1.0'),
    status_sequence BIGINT NOT NULL CHECK (status_sequence > 0),
    task_state TEXT NOT NULL CHECK (
        task_state IN ('TASK_STATE_SUBMITTED', 'TASK_STATE_WORKING',
                       'TASK_STATE_COMPLETED', 'TASK_STATE_FAILED',
                       'TASK_STATE_CANCELED', 'TASK_STATE_INPUT_REQUIRED')
    ),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (
        failure_count BETWEEN 0 AND 8
    ),
    next_attempt_at TIMESTAMPTZ,
    last_failure_category TEXT CHECK (
        last_failure_category IS NULL OR last_failure_category IN (
            'http_rejected', 'network', 'security', 'response_too_large',
            'secret_unavailable', 'protocol_encode'
        )
    ),
    attempt_id TEXT,
    attempt_owner_id TEXT,
    attempt_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    delivered_at TIMESTAMPTZ,
    suppressed_at TIMESTAMPTZ,
    abandoned_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, delivery_id),
    UNIQUE (outbox_id),
    UNIQUE (tenant_id, task_id, config_id, event_id),
    FOREIGN KEY (tenant_id, task_id)
        REFERENCES agentos_distributed_a2a_tasks(tenant_id, task_id),
    FOREIGN KEY (outbox_id)
        REFERENCES agentos_distributed_outbox(outbox_id),
    CHECK ((secret_token IS NULL) = (secret_digest IS NULL)),
    CHECK (
        (attempt_id IS NULL AND attempt_owner_id IS NULL
            AND attempt_expires_at IS NULL)
        OR
        (attempt_id IS NOT NULL AND attempt_owner_id IS NOT NULL
            AND attempt_expires_at IS NOT NULL)
    ),
    CHECK (
        (delivered_at IS NOT NULL)::INTEGER
        + (suppressed_at IS NOT NULL)::INTEGER
        + (abandoned_at IS NOT NULL)::INTEGER <= 1
    ),
    CHECK (
        (delivered_at IS NULL AND suppressed_at IS NULL
            AND abandoned_at IS NULL)
        OR
        (attempt_id IS NULL AND attempt_owner_id IS NULL
            AND attempt_expires_at IS NULL AND next_attempt_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS agentos_distributed_a2a_push_order
ON agentos_distributed_a2a_push_deliveries
    (tenant_id, task_id, config_id, status_sequence, created_at, delivery_id)
WHERE delivered_at IS NULL AND suppressed_at IS NULL
  AND abandoned_at IS NULL;

CREATE INDEX IF NOT EXISTS agentos_distributed_outbox_pending
ON agentos_distributed_outbox (created_at, outbox_id)
WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS agentos_distributed_side_effects (
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, session_id, operation_id, attempt),
    FOREIGN KEY (tenant_id, session_id, run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id)
);

INSERT INTO agentos_distributed_schema (version)
SELECT 1
WHERE NOT EXISTS (SELECT 1 FROM agentos_distributed_schema);

-- migrate:down

DROP TABLE IF EXISTS agentos_distributed_side_effects;
DROP TABLE IF EXISTS agentos_distributed_a2a_push_deliveries;
DROP INDEX IF EXISTS agentos_distributed_outbox_pending;
DROP TABLE IF EXISTS agentos_distributed_outbox;
DROP TABLE IF EXISTS agentos_distributed_reconciliation_sources;
DROP TABLE IF EXISTS agentos_distributed_execution_cursors;
DROP INDEX IF EXISTS agentos_distributed_checkpoint_latest;
DROP TABLE IF EXISTS agentos_distributed_checkpoints;
DROP INDEX IF EXISTS agentos_distributed_one_pending_input;
DROP TABLE IF EXISTS agentos_distributed_accepted_inputs;
DROP TABLE IF EXISTS agentos_distributed_commands;
DROP TABLE IF EXISTS agentos_distributed_submissions;
DROP TABLE IF EXISTS agentos_distributed_artifact_deletions;
DROP INDEX IF EXISTS agentos_distributed_artifact_page;
DROP TABLE IF EXISTS agentos_distributed_artifacts;
DROP TABLE IF EXISTS agentos_distributed_a2a_push_operations;
DROP TABLE IF EXISTS agentos_distributed_a2a_push_configs;
DROP INDEX IF EXISTS agentos_distributed_one_active_run;
DROP TABLE IF EXISTS agentos_distributed_a2a_tasks;
DROP TABLE IF EXISTS agentos_distributed_runs;
DROP TABLE IF EXISTS agentos_distributed_sessions;
DROP TABLE IF EXISTS agentos_distributed_schema;
