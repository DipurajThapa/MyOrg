CREATE TABLE identity_bindings (
  issuer TEXT NOT NULL,
  subject TEXT NOT NULL,
  org_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (issuer, subject),
  FOREIGN KEY (org_id, actor_id) REFERENCES actors(org_id, id) ON DELETE CASCADE
);

CREATE TABLE gateway_nonces (
  issuer TEXT NOT NULL,
  nonce TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (issuer, nonce)
);

CREATE TABLE ui_states (
  org_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  active_view TEXT NOT NULL,
  time_range TEXT NOT NULL,
  filters_json TEXT NOT NULL,
  sort_json TEXT NOT NULL,
  scroll_position INTEGER NOT NULL,
  current_project_id TEXT,
  revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, actor_id),
  FOREIGN KEY (org_id, actor_id) REFERENCES actors(org_id, id) ON DELETE CASCADE
);

CREATE TABLE project_intakes (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL,
  title TEXT NOT NULL,
  sponsor TEXT NOT NULL,
  decision_owner TEXT NOT NULL,
  affected_user TEXT NOT NULL,
  desired_outcome TEXT NOT NULL,
  documents_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('draft','ready')),
  revision INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, id),
  FOREIGN KEY (org_id, created_by) REFERENCES actors(org_id, id),
  FOREIGN KEY (org_id, updated_by) REFERENCES actors(org_id, id)
);

CREATE TABLE operational_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  category TEXT NOT NULL,
  action TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  previous_hash TEXT,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (org_id, seq),
  UNIQUE (org_id, request_id),
  FOREIGN KEY (org_id, actor_id) REFERENCES actors(org_id, id)
);

CREATE INDEX idx_identity_actor ON identity_bindings(org_id, actor_id);
CREATE INDEX idx_gateway_nonce_expiry ON gateway_nonces(expires_at);
CREATE INDEX idx_project_intakes_updated ON project_intakes(org_id, updated_at);
CREATE INDEX idx_operational_events_org ON operational_events(org_id, seq);
