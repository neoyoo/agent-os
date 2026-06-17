-- migrate:up
CREATE TABLE IF NOT EXISTS agentos_session_snapshots (
    session_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    lease_fence BIGINT NOT NULL DEFAULT 0,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE agentos_session_snapshots
    ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1;

ALTER TABLE agentos_session_snapshots
    ADD COLUMN IF NOT EXISTS lease_fence BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_agentos_session_snapshots_updated_at
    ON agentos_session_snapshots (updated_at);

-- migrate:down
DROP INDEX IF EXISTS idx_agentos_session_snapshots_updated_at;
DROP TABLE IF EXISTS agentos_session_snapshots;
