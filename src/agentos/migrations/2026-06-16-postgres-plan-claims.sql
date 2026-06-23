-- migrate:up

CREATE TABLE IF NOT EXISTS agentos_plan_claims (
  plan_id TEXT PRIMARY KEY,
  owner_agent_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  claimed_at DOUBLE PRECISION NOT NULL,
  lease_expires_at DOUBLE PRECISION NOT NULL,
  generation INTEGER NOT NULL,
  payload JSONB NOT NULL,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agentos_plan_claims_owner_expiry_idx
  ON agentos_plan_claims (owner_agent_id, lease_expires_at, plan_id);

CREATE INDEX IF NOT EXISTS agentos_plan_claims_worker_expiry_idx
  ON agentos_plan_claims (worker_id, lease_expires_at, plan_id);

-- migrate:down

DROP INDEX IF EXISTS agentos_plan_claims_worker_expiry_idx;
DROP INDEX IF EXISTS agentos_plan_claims_owner_expiry_idx;
DROP TABLE IF EXISTS agentos_plan_claims;
