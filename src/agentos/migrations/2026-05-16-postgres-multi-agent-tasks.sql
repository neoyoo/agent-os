-- migrate:up
CREATE TABLE agentos_multi_agent_tasks (
  task_id TEXT PRIMARY KEY,
  parent_agent_id TEXT NOT NULL,
  target_agent_id TEXT NOT NULL,
  status TEXT NOT NULL,
  worker_id TEXT,
  lease_expires_at DOUBLE PRECISION,
  deadline_at DOUBLE PRECISION NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  payload JSONB NOT NULL,
  consumed_at DOUBLE PRECISION,
  result_notified_at DOUBLE PRECISION,
  updated_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX agentos_multi_agent_tasks_claim_idx
  ON agentos_multi_agent_tasks (
    status,
    target_agent_id,
    lease_expires_at,
    deadline_at
  );

CREATE TABLE agentos_multi_agent_task_outbox (
  outbox_id BIGSERIAL PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES agentos_multi_agent_tasks(task_id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  delivered_at DOUBLE PRECISION,
  created_at DOUBLE PRECISION NOT NULL
);

-- migrate:down
DROP TABLE IF EXISTS agentos_multi_agent_task_outbox;
DROP TABLE IF EXISTS agentos_multi_agent_tasks;
