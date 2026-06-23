-- migrate:up
CREATE TABLE IF NOT EXISTS agentos_team_worker_retries (
  team_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  delivery_id TEXT NOT NULL,
  status TEXT NOT NULL,
  next_run_at DOUBLE PRECISION NOT NULL,
  attempts INTEGER NOT NULL,
  exhausted_at DOUBLE PRECISION,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, delivery_id)
);

CREATE INDEX IF NOT EXISTS agentos_team_worker_retries_due_idx
  ON agentos_team_worker_retries (status, next_run_at, team_id);

CREATE INDEX IF NOT EXISTS agentos_team_worker_retries_team_idx
  ON agentos_team_worker_retries (team_id, next_run_at, agent_id, delivery_id);

-- migrate:down
DROP TABLE IF EXISTS agentos_team_worker_retries;
