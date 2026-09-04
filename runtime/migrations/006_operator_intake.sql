-- A person is a trigger source.
--
-- 005 enumerated the two ways work could arrive: a signed webhook and the company's own
-- clock. Both are machines. There was no way for the operator to say "do this" other than
-- hand-authoring a workflow file and calling create-run, so the planner -- the thing that
-- turns a sentence into a workflow -- was reachable only by something that was not a person.
--
-- 'operator' is a third source with the same shape and the same governance: it queues a goal,
-- intake plans it, and every outward step still parks at a human gate. Keeping it a distinct
-- value rather than reusing 'schedule' is the point -- `source` exists to say where work came
-- from, and the audit trail is worth less if a person's request is recorded as a cron tick.
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt. Rows are copied first and
-- the swap happens inside the migration's transaction: either the new table is in place with
-- every row, or nothing changed.

CREATE TABLE trigger_intake_new (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  source TEXT NOT NULL CHECK (source IN ('webhook','schedule','operator')),
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

INSERT INTO trigger_intake_new
  (id, org_id, source, source_ref, goal, status, run_id, attempts, last_error,
   created_at, updated_at)
SELECT id, org_id, source, source_ref, goal, status, run_id, attempts, last_error,
       created_at, updated_at
FROM trigger_intake;

DROP TABLE trigger_intake;
ALTER TABLE trigger_intake_new RENAME TO trigger_intake;
CREATE INDEX idx_intake_queued ON trigger_intake(org_id, status, created_at);
