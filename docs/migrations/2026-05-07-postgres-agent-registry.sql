-- migrate:up
CREATE TABLE agentos_agent_registry (
  agent_id TEXT PRIMARY KEY,
  card JSONB NOT NULL,
  heartbeat_at DOUBLE PRECISION NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agentos_agent_session_affinity (
  session_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  expires_at DOUBLE PRECISION NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE IF EXISTS agentos_agent_session_affinity;
DROP TABLE IF EXISTS agentos_agent_registry;
