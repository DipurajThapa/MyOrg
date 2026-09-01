CREATE TABLE organizations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','suspended')),
  created_at TEXT NOT NULL
);

CREATE TABLE actors (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  actor_type TEXT NOT NULL CHECK (actor_type IN ('human','agent','service')),
  display_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','disabled')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id)
);

CREATE TABLE role_bindings (
  org_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  role TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (org_id, actor_id, role),
  FOREIGN KEY (org_id, actor_id) REFERENCES actors(org_id, id) ON DELETE CASCADE
);

CREATE TABLE revoked_tokens (
  org_id TEXT NOT NULL,
  jti TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT NOT NULL,
  PRIMARY KEY (org_id, jti)
);

CREATE TABLE runs (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  workflow_id TEXT NOT NULL,
  workflow_revision TEXT NOT NULL,
  goal TEXT NOT NULL,
  data_class TEXT NOT NULL CHECK (data_class IN ('public','internal')),
  status TEXT NOT NULL CHECK (status IN ('active','completed','blocked','cancelled')),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id),
  FOREIGN KEY (org_id, created_by) REFERENCES actors(org_id, id)
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (org_id, run_id, seq),
  UNIQUE (org_id, request_id),
  FOREIGN KEY (org_id, run_id) REFERENCES runs(org_id, id),
  FOREIGN KEY (org_id, actor_id) REFERENCES actors(org_id, id)
);

CREATE TABLE idempotency_requests (
  org_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (org_id, request_id)
);

CREATE TABLE approvals (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  run_id TEXT NOT NULL,
  action TEXT NOT NULL,
  action_hash TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  payload_ref TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','consumed','expired')),
  decided_by TEXT,
  decided_at TEXT,
  consumed_by TEXT,
  consumed_at TEXT,
  PRIMARY KEY (org_id, id),
  FOREIGN KEY (org_id, run_id) REFERENCES runs(org_id, id),
  FOREIGN KEY (org_id, requested_by) REFERENCES actors(org_id, id),
  FOREIGN KEY (org_id, decided_by) REFERENCES actors(org_id, id),
  FOREIGN KEY (org_id, consumed_by) REFERENCES actors(org_id, id)
);

CREATE TABLE connector_registrations (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  kind TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('disabled','read_only','propose_write')),
  base_url TEXT NOT NULL,
  allowed_hosts_json TEXT NOT NULL,
  allowed_actions_json TEXT NOT NULL,
  secret_ref TEXT,
  timeout_seconds INTEGER NOT NULL,
  max_response_bytes INTEGER NOT NULL,
  enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
  config_revision TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id)
);

CREATE TABLE connector_receipts (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL,
  connector_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  provider_receipt TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id),
  UNIQUE (org_id, connector_id, idempotency_key),
  FOREIGN KEY (org_id, connector_id) REFERENCES connector_registrations(org_id, id)
);

CREATE TABLE webhook_nonces (
  org_id TEXT NOT NULL,
  connector_id TEXT NOT NULL,
  nonce TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (org_id, connector_id, nonce),
  FOREIGN KEY (org_id, connector_id) REFERENCES connector_registrations(org_id, id)
);

CREATE INDEX idx_events_run ON events(org_id, run_id, seq);
CREATE INDEX idx_approvals_status ON approvals(org_id, status, expires_at);
CREATE INDEX idx_receipts_connector ON connector_receipts(org_id, connector_id, created_at);
