-- migrate:up

CREATE TABLE IF NOT EXISTS agentos_team_ui_event_sequences (
  team_id TEXT PRIMARY KEY,
  next_event_id BIGINT NOT NULL CHECK (next_event_id >= 1),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agentos_team_ui_events (
  team_id TEXT NOT NULL,
  event_id BIGINT NOT NULL,
  kind TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL,
  payload JSONB NOT NULL,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, event_id)
);

CREATE INDEX IF NOT EXISTS agentos_team_ui_events_kind_idx
  ON agentos_team_ui_events (team_id, kind, event_id);

CREATE INDEX IF NOT EXISTS agentos_team_ui_events_created_at_idx
  ON agentos_team_ui_events (team_id, created_at, event_id);

-- migrate:down

DROP INDEX IF EXISTS agentos_team_ui_events_created_at_idx;
DROP INDEX IF EXISTS agentos_team_ui_events_kind_idx;
DROP TABLE IF EXISTS agentos_team_ui_events;
DROP TABLE IF EXISTS agentos_team_ui_event_sequences;
