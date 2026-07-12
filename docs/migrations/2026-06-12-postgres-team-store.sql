-- migrate:up
CREATE TABLE IF NOT EXISTS agentos_team_records (
  team_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  leader_agent_id TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS agentos_team_records_status_idx
  ON agentos_team_records (status, leader_agent_id, created_at);

CREATE TABLE IF NOT EXISTS agentos_team_members (
  team_id TEXT NOT NULL REFERENCES agentos_team_records(team_id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  session_id TEXT,
  created_at DOUBLE PRECISION NOT NULL,
  payload JSONB NOT NULL,
  PRIMARY KEY (team_id, agent_id)
);

CREATE INDEX IF NOT EXISTS agentos_team_members_agent_idx
  ON agentos_team_members (agent_id, status, created_at);

CREATE INDEX IF NOT EXISTS agentos_team_members_team_order_idx
  ON agentos_team_members (team_id, created_at, agent_id);

CREATE TABLE IF NOT EXISTS agentos_team_messages (
  message_id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL REFERENCES agentos_team_records(team_id) ON DELETE CASCADE,
  from_agent_id TEXT NOT NULL,
  to_agent_id TEXT,
  kind TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  correlation_id TEXT,
  payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS agentos_team_messages_order_idx
  ON agentos_team_messages (team_id, created_at, message_id);

CREATE INDEX IF NOT EXISTS agentos_team_messages_recipient_idx
  ON agentos_team_messages (team_id, to_agent_id, created_at);

CREATE INDEX IF NOT EXISTS agentos_team_messages_correlation_idx
  ON agentos_team_messages (correlation_id);

-- migrate:down
DROP TABLE IF EXISTS agentos_team_messages;
DROP TABLE IF EXISTS agentos_team_members;
DROP TABLE IF EXISTS agentos_team_records;
