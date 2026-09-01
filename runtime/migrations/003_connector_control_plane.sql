CREATE TABLE connector_authorizations (
  org_id TEXT NOT NULL,
  connector_id TEXT NOT NULL,
  provider_account_ref TEXT NOT NULL,
  scopes_json TEXT NOT NULL,
  token_secret_ref TEXT NOT NULL,
  refresh_secret_ref TEXT,
  status TEXT NOT NULL CHECK (status IN ('authorized','revoked','expired')),
  authorized_by TEXT NOT NULL,
  authorized_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  PRIMARY KEY (org_id, connector_id),
  FOREIGN KEY (org_id, connector_id) REFERENCES connector_registrations(org_id, id) ON DELETE CASCADE,
  FOREIGN KEY (org_id, authorized_by) REFERENCES actors(org_id, id)
);

CREATE TABLE connector_reconciliations (
  org_id TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  provider_status TEXT NOT NULL CHECK (provider_status IN ('confirmed','rejected','pending')),
  details_sha256 TEXT NOT NULL,
  checked_by TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  PRIMARY KEY (org_id, receipt_id),
  FOREIGN KEY (org_id, receipt_id) REFERENCES connector_receipts(org_id, id) ON DELETE CASCADE,
  FOREIGN KEY (org_id, checked_by) REFERENCES actors(org_id, id)
);

CREATE INDEX idx_connector_authorization_expiry ON connector_authorizations(expires_at);
CREATE INDEX idx_connector_reconciliation_status ON connector_reconciliations(org_id, provider_status);
