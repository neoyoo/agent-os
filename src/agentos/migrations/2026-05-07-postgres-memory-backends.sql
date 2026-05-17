-- migrate:up
CREATE TABLE agentos_sessions (
  session_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  next_turn_number INTEGER NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agentos_messages (
  session_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, message_id)
);

CREATE TABLE agentos_active_refs (
  session_id TEXT PRIMARY KEY,
  refs JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agentos_compressed_segments (
  session_id TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  package JSONB NOT NULL,
  source_refs JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, segment_id)
);

-- migrate:down
DROP TABLE IF EXISTS agentos_compressed_segments;
DROP TABLE IF EXISTS agentos_active_refs;
DROP TABLE IF EXISTS agentos_messages;
DROP TABLE IF EXISTS agentos_sessions;
