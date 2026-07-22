-- migrate:up

CREATE TABLE agentos_teams (
    tenant_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'deleted')),
    leader_agent_id TEXT NOT NULL,
    workspace TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, team_id),
    CHECK (
        (status = 'active' AND deleted_at IS NULL)
        OR (status = 'deleted' AND deleted_at IS NOT NULL)
    )
);

CREATE TABLE agentos_team_members (
    tenant_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('leader', 'worker')),
    status TEXT NOT NULL CHECK (status IN ('active', 'deleted')),
    target_session_id TEXT NOT NULL,
    capabilities_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    deleted_at TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, team_id, recipient_agent_id),
    UNIQUE (tenant_id, team_id, recipient_agent_id, target_session_id),
    FOREIGN KEY (tenant_id, team_id)
        REFERENCES agentos_teams(tenant_id, team_id),
    FOREIGN KEY (tenant_id, target_session_id)
        REFERENCES agentos_distributed_sessions(tenant_id, session_id),
    CHECK (
        (status = 'active' AND deleted_at IS NULL)
        OR (status = 'deleted' AND deleted_at IS NOT NULL)
    ),
    CHECK (jsonb_typeof(capabilities_json) = 'array')
);

CREATE UNIQUE INDEX agentos_team_members_active_session
ON agentos_team_members (tenant_id, target_session_id)
WHERE status = 'active';

CREATE TABLE agentos_team_messages (
    tenant_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_agent_id TEXT NOT NULL,
    message_kind TEXT NOT NULL,
    content TEXT NOT NULL,
    correlation_id TEXT,
    addressing_kind TEXT NOT NULL CHECK (
        addressing_kind IN ('direct', 'broadcast')
    ),
    addressed_agent_id TEXT,
    recipient_snapshot_json JSONB NOT NULL,
    request_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, message_id),
    UNIQUE (tenant_id, team_id, message_id),
    FOREIGN KEY (tenant_id, team_id)
        REFERENCES agentos_teams(tenant_id, team_id),
    FOREIGN KEY (tenant_id, team_id, sender_agent_id)
        REFERENCES agentos_team_members(
            tenant_id, team_id, recipient_agent_id
        ),
    CHECK (
        (addressing_kind = 'direct' AND addressed_agent_id IS NOT NULL)
        OR (addressing_kind = 'broadcast' AND addressed_agent_id IS NULL)
    ),
    CHECK (jsonb_typeof(recipient_snapshot_json) = 'array')
);

CREATE INDEX agentos_team_messages_order
ON agentos_team_messages (tenant_id, team_id, created_at, message_id);

CREATE TABLE agentos_team_deliveries (
    tenant_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    target_session_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'claimed', 'applied', 'rejected')
    ),
    claim_id TEXT,
    fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
    claim_expires_at TIMESTAMPTZ,
    source_sha256 CHAR(64) NOT NULL,
    result_kind TEXT,
    observed_run_id TEXT,
    observed_aggregate_version BIGINT CHECK (
        observed_aggregate_version >= 0
    ),
    observed_run_status TEXT CHECK (
        observed_run_status IN (
            'created', 'queued', 'running', 'waiting',
            'completed', 'failed', 'cancelled'
        )
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, delivery_id),
    UNIQUE (tenant_id, message_id, recipient_agent_id),
    UNIQUE (tenant_id, delivery_id, target_session_id),
    FOREIGN KEY (tenant_id, team_id, message_id)
        REFERENCES agentos_team_messages(tenant_id, team_id, message_id),
    FOREIGN KEY (
        tenant_id, team_id, recipient_agent_id, target_session_id
    ) REFERENCES agentos_team_members(
        tenant_id, team_id, recipient_agent_id, target_session_id
    ),
    FOREIGN KEY (tenant_id, target_session_id)
        REFERENCES agentos_distributed_sessions(tenant_id, session_id),
    FOREIGN KEY (tenant_id, target_session_id, observed_run_id)
        REFERENCES agentos_distributed_runs(tenant_id, session_id, run_id),
    CHECK (
        (state = 'claimed' AND claim_id IS NOT NULL
            AND claim_expires_at IS NOT NULL)
        OR (state <> 'claimed' AND claim_id IS NULL
            AND claim_expires_at IS NULL)
    ),
    CHECK (
        (state IN ('pending', 'claimed')
            AND result_kind IS NULL
            AND observed_run_id IS NULL
            AND observed_aggregate_version IS NULL
            AND observed_run_status IS NULL)
        OR
        (state = 'applied'
            AND result_kind IS NOT NULL
            AND observed_run_id IS NOT NULL
            AND observed_aggregate_version IS NOT NULL
            AND observed_run_status IS NOT NULL)
        OR
        (state = 'rejected'
            AND result_kind IS NOT NULL
            AND (
                (observed_run_id IS NULL
                    AND observed_aggregate_version IS NULL
                    AND observed_run_status IS NULL)
                OR
                (observed_run_id IS NOT NULL
                    AND observed_aggregate_version IS NOT NULL
                    AND observed_run_status IS NOT NULL)
            ))
    )
);

CREATE INDEX agentos_team_deliveries_pending
ON agentos_team_deliveries (state, claim_expires_at, created_at, delivery_id)
WHERE state IN ('pending', 'claimed');

CREATE TABLE agentos_team_events (
    tenant_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    event_sequence BIGINT GENERATED ALWAYS AS IDENTITY,
    event_kind TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, team_id, event_sequence),
    UNIQUE (event_sequence),
    FOREIGN KEY (tenant_id, team_id)
        REFERENCES agentos_teams(tenant_id, team_id),
    CHECK (jsonb_typeof(payload_json) = 'object')
);

ALTER TABLE agentos_distributed_submissions
    ALTER COLUMN content DROP NOT NULL,
    ALTER COLUMN artifact_handles DROP NOT NULL,
    ALTER COLUMN user_message_id DROP NOT NULL,
    ADD COLUMN source_kind TEXT
        CONSTRAINT agentos_distributed_submissions_source_kind_check
        CHECK (source_kind = 'team_message'),
    ADD COLUMN source_payload_json TEXT,
    ADD COLUMN team_delivery_id TEXT,
    ADD CONSTRAINT agentos_distributed_submissions_team_delivery_fk
        FOREIGN KEY (tenant_id, team_delivery_id, session_id)
        REFERENCES agentos_team_deliveries(
            tenant_id, delivery_id, target_session_id
        );

ALTER TABLE agentos_distributed_submissions
    ADD COLUMN input_kind TEXT GENERATED ALWAYS AS (
        CASE
            WHEN source_kind IS NULL THEN 'start'
            WHEN source_kind = 'team_message' THEN 'internal_start'
        END
    ) STORED,
    ADD CONSTRAINT agentos_distributed_submissions_shape_check CHECK (
        (input_kind = 'start'
            AND content IS NOT NULL
            AND artifact_handles IS NOT NULL
            AND user_message_id IS NOT NULL
            AND source_kind IS NULL
            AND source_payload_json IS NULL
            AND team_delivery_id IS NULL)
        OR
        (input_kind = 'internal_start'
            AND content IS NULL
            AND artifact_handles IS NULL
            AND user_message_id IS NULL
            AND source_kind = 'team_message'
            AND source_payload_json IS NOT NULL
            AND octet_length(source_payload_json) <= 4096
            AND team_delivery_id IS NOT NULL)
    );

ALTER TABLE agentos_distributed_accepted_inputs
    DROP CONSTRAINT agentos_distributed_accepted_inputs_source_kind_check,
    DROP CONSTRAINT agentos_distributed_accepted_inputs_shape_check,
    ADD COLUMN input_kind TEXT GENERATED ALWAYS AS (
        CASE source_kind
            WHEN 'submission' THEN 'start'
            WHEN 'command' THEN 'continuation'
            WHEN 'team_message' THEN 'internal_start'
        END
    ) STORED,
    ADD CONSTRAINT agentos_distributed_accepted_inputs_source_kind_check
        CHECK (source_kind IN ('submission', 'command', 'team_message')),
    ADD CONSTRAINT agentos_distributed_accepted_inputs_shape_check CHECK (
        (source_kind = 'submission' AND content IS NOT NULL
            AND artifact_handles IS NOT NULL AND user_message_id IS NOT NULL
            AND continuation_kind IS NULL AND payload_json IS NULL)
        OR
        (source_kind = 'command' AND content IS NULL
            AND artifact_handles IS NULL AND user_message_id IS NULL
            AND continuation_kind IS NOT NULL AND payload_json IS NOT NULL)
        OR
        (source_kind = 'team_message' AND content IS NULL
            AND artifact_handles IS NULL AND user_message_id IS NULL
            AND continuation_kind IS NULL AND payload_json IS NOT NULL
            AND octet_length(payload_json) <= 4096)
    );

ALTER TABLE agentos_distributed_outbox
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN team_delivery_id TEXT,
    ADD CONSTRAINT agentos_distributed_outbox_team_delivery_fk
        FOREIGN KEY (tenant_id, team_delivery_id, session_id)
        REFERENCES agentos_team_deliveries(
            tenant_id, delivery_id, target_session_id
        ),
    ADD CONSTRAINT agentos_distributed_outbox_owner_check CHECK (
        (run_id IS NOT NULL AND team_delivery_id IS NULL)
        OR (run_id IS NULL AND team_delivery_id IS NOT NULL)
    );

-- migrate:down

DELETE FROM agentos_distributed_outbox
WHERE team_delivery_id IS NOT NULL;

DELETE FROM agentos_distributed_accepted_inputs
WHERE source_kind = 'team_message';

DELETE FROM agentos_distributed_submissions
WHERE source_kind = 'team_message';

ALTER TABLE agentos_distributed_outbox
    DROP CONSTRAINT agentos_distributed_outbox_owner_check,
    DROP CONSTRAINT agentos_distributed_outbox_team_delivery_fk,
    DROP COLUMN team_delivery_id,
    ALTER COLUMN run_id SET NOT NULL;

ALTER TABLE agentos_distributed_accepted_inputs
    DROP CONSTRAINT agentos_distributed_accepted_inputs_source_kind_check,
    DROP CONSTRAINT agentos_distributed_accepted_inputs_shape_check,
    DROP COLUMN input_kind,
    ADD CONSTRAINT agentos_distributed_accepted_inputs_source_kind_check
        CHECK (source_kind IN ('submission', 'command')),
    ADD CONSTRAINT agentos_distributed_accepted_inputs_shape_check CHECK (
        (source_kind = 'submission' AND content IS NOT NULL
            AND artifact_handles IS NOT NULL AND user_message_id IS NOT NULL
            AND continuation_kind IS NULL AND payload_json IS NULL)
        OR
        (source_kind = 'command' AND content IS NULL
            AND artifact_handles IS NULL AND user_message_id IS NULL
            AND continuation_kind IS NOT NULL AND payload_json IS NOT NULL)
    );

ALTER TABLE agentos_distributed_submissions
    DROP CONSTRAINT agentos_distributed_submissions_shape_check,
    DROP CONSTRAINT agentos_distributed_submissions_team_delivery_fk,
    DROP COLUMN input_kind,
    DROP COLUMN team_delivery_id,
    DROP COLUMN source_payload_json,
    DROP COLUMN source_kind,
    ALTER COLUMN content SET NOT NULL,
    ALTER COLUMN artifact_handles SET NOT NULL,
    ALTER COLUMN user_message_id SET NOT NULL;

DROP TABLE IF EXISTS agentos_team_events;
DROP INDEX IF EXISTS agentos_team_deliveries_pending;
DROP TABLE IF EXISTS agentos_team_deliveries;
DROP INDEX IF EXISTS agentos_team_messages_order;
DROP TABLE IF EXISTS agentos_team_messages;
DROP INDEX IF EXISTS agentos_team_members_active_session;
DROP TABLE IF EXISTS agentos_team_members;
DROP TABLE IF EXISTS agentos_teams;
