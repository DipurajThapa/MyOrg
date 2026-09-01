-- Converge the two state systems.
--
-- The JSONL event log stays the system of record for execution: it is append-only and
-- hash-chained, and nothing here may write back into it. This adds the read model the
-- API and the operator UI need, so both halves finally describe the same company.
--
-- `runs.status` keeps its original four-value contract for existing callers; the exact
-- runtime status (blocked_review_limit, rejected_by_checker, ...) lands in
-- runtime_status alongside it.

ALTER TABLE runs ADD COLUMN runtime_status TEXT;
ALTER TABLE runs ADD COLUMN cycle_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN max_cycles INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN projected_at TEXT;

CREATE TABLE run_steps (
  org_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  owner TEXT NOT NULL,
  checker TEXT,
  action TEXT NOT NULL,
  risk TEXT NOT NULL CHECK (risk IN ('green','yellow','red')),
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 0,
  review_cycles INTEGER NOT NULL DEFAULT 0,
  max_review_cycles INTEGER NOT NULL DEFAULT 0,
  depends_on TEXT NOT NULL DEFAULT '[]',
  evidence TEXT,
  evidence_sha256 TEXT,
  approver TEXT,
  approval_ref TEXT,
  checked_by TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (org_id, run_id, step_id),
  FOREIGN KEY (org_id, run_id) REFERENCES runs(org_id, id)
);

CREATE INDEX run_steps_by_status ON run_steps(org_id, status);
CREATE INDEX run_steps_waiting ON run_steps(org_id, status, risk);
