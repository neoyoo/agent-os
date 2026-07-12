-- migrate:up

CREATE TABLE IF NOT EXISTS agentos_a2a_push_notification_configs (
  task_id TEXT NOT NULL,
  config_id TEXT NOT NULL,
  url TEXT NOT NULL,
  authentication_schemes TEXT[] NOT NULL DEFAULT '{}',
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (task_id, config_id)
);

CREATE INDEX IF NOT EXISTS agentos_a2a_push_notification_configs_task_idx
  ON agentos_a2a_push_notification_configs (task_id, config_id);

CREATE TABLE IF NOT EXISTS agentos_a2a_push_notification_deliveries (
  delivery_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  config_id TEXT,
  status TEXT NOT NULL,
  next_run_at DOUBLE PRECISION NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  worker_id TEXT,
  lease_expires_at DOUBLE PRECISION,
  delivered_at DOUBLE PRECISION,
  dead_lettered_at DOUBLE PRECISION,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (delivery_id)
);

CREATE INDEX IF NOT EXISTS agentos_a2a_push_notification_deliveries_due_idx
  ON agentos_a2a_push_notification_deliveries
    (status, next_run_at, created_at, delivery_id)
  WHERE status IN ('queued', 'retry_scheduled', 'running');

CREATE INDEX IF NOT EXISTS agentos_a2a_push_notification_deliveries_task_idx
  ON agentos_a2a_push_notification_deliveries (task_id, created_at, delivery_id);

CREATE INDEX IF NOT EXISTS agentos_a2a_push_notification_deliveries_dead_idx
  ON agentos_a2a_push_notification_deliveries (dead_lettered_at, delivery_id)
  WHERE status = 'dead_letter';

-- migrate:down

DROP INDEX IF EXISTS agentos_a2a_push_notification_deliveries_dead_idx;
DROP INDEX IF EXISTS agentos_a2a_push_notification_deliveries_task_idx;
DROP INDEX IF EXISTS agentos_a2a_push_notification_deliveries_due_idx;
DROP TABLE IF EXISTS agentos_a2a_push_notification_deliveries;
DROP INDEX IF EXISTS agentos_a2a_push_notification_configs_task_idx;
DROP TABLE IF EXISTS agentos_a2a_push_notification_configs;
