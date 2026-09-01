-- A live connector call has three outcomes, not two: it succeeded, it failed, or we do not
-- know. The unknown case is the one that matters -- a timeout after the bytes left means a
-- retry could charge a customer twice. So a receipt is written *before* the call as
-- 'in_flight' and settled afterwards; anything left in flight is a person's problem, never
-- a machine's retry.
ALTER TABLE connector_receipts ADD COLUMN settled_at TEXT;
ALTER TABLE connector_receipts ADD COLUMN response_sha256 TEXT;
ALTER TABLE connector_receipts ADD COLUMN outcome_note TEXT;

CREATE INDEX idx_receipts_status ON connector_receipts(org_id, status, created_at);

-- What the world is allowed to start. A verified webhook does not get to say "run anything":
-- it selects one pre-registered trigger, and the goal text comes from this table, never from
-- the payload. Inbound content stays data.
CREATE TABLE webhook_triggers (
  org_id TEXT NOT NULL REFERENCES organizations(id),
  connector_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  goal TEXT NOT NULL,
  enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, connector_id, event_type),
  FOREIGN KEY (org_id, connector_id) REFERENCES connector_registrations(org_id, id) ON DELETE CASCADE
);

-- The clock. next_fire_at is the fence: a schedule fires when the clock passes it, and the
-- firing advances it in the same transaction, so two sweepers cannot both fire one schedule.
CREATE TABLE schedules (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  kind TEXT NOT NULL CHECK (kind IN ('interval','daily')),
  interval_seconds INTEGER,
  daily_at TEXT,
  goal TEXT NOT NULL,
  enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
  next_fire_at TEXT NOT NULL,
  last_fire_at TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id),
  CHECK ((kind = 'interval' AND interval_seconds IS NOT NULL AND daily_at IS NULL)
      OR (kind = 'daily' AND daily_at IS NOT NULL AND interval_seconds IS NULL))
);

CREATE INDEX idx_schedules_due ON schedules(enabled, next_fire_at);

-- Fired triggers waiting to become runs. The HTTP path must answer in milliseconds and must
-- never call a model, so it only enqueues here; the scheduler does the planning. The id is
-- derived from the source and its reference, so a replayed webhook or a double sweep lands
-- on the same row instead of starting the same work twice.
CREATE TABLE trigger_intake (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  source TEXT NOT NULL CHECK (source IN ('webhook','schedule')),
  source_ref TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','started','failed')),
  run_id TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id)
);

CREATE INDEX idx_intake_queued ON trigger_intake(org_id, status, created_at);
