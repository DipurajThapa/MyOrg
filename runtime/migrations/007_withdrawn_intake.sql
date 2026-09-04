-- A person can take their own request out of the line.
--
-- 006 made an operator a trigger source. It gave them no way back out: once a goal was
-- queued the only exits were the planner spending real money on it three times and giving
-- up, or it becoming a run. A mistyped goal had to be paid for.
--
-- The serial queue made that worse than untidy. A failure that looks temporary deliberately
-- spends none of a request's three attempts, so a planner that stays unreachable leaves the
-- front of the line occupied for ever -- and nothing behind it starts. Withdrawing the head
-- is the only thing that frees the queue, so it has to exist.
--
-- 'withdrawn' is its own status rather than a reuse of 'failed'. `failed` means the company
-- tried and could not, and `escalate_ideas` raises a notice for every failed row; recording
-- a person's own withdrawal there would have told them their request "could not be planned".
-- The same status list keeps it out of `unfinished_triggers`, so a withdrawn request leaves
-- the operator's screen instead of sitting there looking unfinished.
--
-- SQLite cannot alter a CHECK constraint, so the table is rebuilt exactly as 006 did it.
-- Rows are copied first and the swap happens inside the migration's transaction: either the
-- new table is in place with every row, or nothing changed.

CREATE TABLE trigger_intake_new (
  id TEXT NOT NULL,
  org_id TEXT NOT NULL REFERENCES organizations(id),
  source TEXT NOT NULL CHECK (source IN ('webhook','schedule','operator')),
  source_ref TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','started','failed','withdrawn')),
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
