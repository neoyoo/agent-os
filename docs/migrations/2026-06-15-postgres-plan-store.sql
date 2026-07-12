-- migrate:up

CREATE TABLE IF NOT EXISTS agentos_plans (
  plan_id TEXT PRIMARY KEY,
  owner_agent_id TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  payload JSONB NOT NULL,
  revision BIGINT NOT NULL DEFAULT 0,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  row_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE agentos_plans
  ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS agentos_plans_owner_idx
  ON agentos_plans (owner_agent_id, updated_at, plan_id);

CREATE INDEX IF NOT EXISTS agentos_plans_status_idx
  ON agentos_plans (status, updated_at, plan_id);

-- migrate:down

DROP INDEX IF EXISTS agentos_plans_status_idx;
DROP INDEX IF EXISTS agentos_plans_owner_idx;
DROP TABLE IF EXISTS agentos_plans;
