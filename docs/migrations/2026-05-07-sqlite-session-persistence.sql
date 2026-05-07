-- migrate:up
CREATE TABLE snapshots (
  session_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE event_records (
  session_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (session_id, sequence)
);

-- migrate:down
DROP TABLE IF EXISTS event_records;
DROP TABLE IF EXISTS snapshots;
