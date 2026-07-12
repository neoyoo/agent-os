-- migrate:up
CREATE TABLE IF NOT EXISTS agentos_team_worker_cancellations (
  team_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  delivery_id TEXT,
  message_id TEXT,
  delivery_key TEXT GENERATED ALWAYS AS (
    CASE WHEN delivery_id IS NULL THEN 'null:' ELSE 'value:' || delivery_id END
  ) STORED,
  message_key TEXT GENERATED ALWAYS AS (
    CASE WHEN message_id IS NULL THEN 'null:' ELSE 'value:' || message_id END
  ) STORED,
  status TEXT NOT NULL,
  requested_at DOUBLE PRECISION NOT NULL,
  acknowledged_at DOUBLE PRECISION,
  cleared_at DOUBLE PRECISION,
  reason TEXT NOT NULL,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, session_id, delivery_key, message_key)
);

CREATE INDEX IF NOT EXISTS agentos_team_worker_cancellations_identity_idx
  ON agentos_team_worker_cancellations (
    team_id, agent_id, session_id, delivery_key, message_key
  );

CREATE INDEX IF NOT EXISTS agentos_team_worker_cancellations_active_idx
  ON agentos_team_worker_cancellations (
    status, team_id, agent_id, session_id, requested_at
  );

CREATE INDEX IF NOT EXISTS agentos_team_worker_cancellations_team_idx
  ON agentos_team_worker_cancellations (
    team_id, agent_id, session_id, requested_at, delivery_key, message_key
  );

-- migrate:down
DROP TABLE IF EXISTS agentos_team_worker_cancellations;
