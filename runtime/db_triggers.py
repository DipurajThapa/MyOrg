#!/usr/bin/env python3
"""What the world is allowed to start, and what it started."""
from __future__ import annotations

import sqlite3

from runtime.db_core import Conflict, NotFound, StoreError, utc_now


class TriggersMixin:
    """What the world is allowed to start, and what it started."""

    # --- triggers: what the world is allowed to start, and what it started ---------------

    def register_webhook_trigger(self, org_id: str, connector_id: str, event_type: str, goal: str,
                                 enabled: bool, actor_id: str, request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            self._connector(connection, org_id, connector_id)
            connection.execute(
                "INSERT INTO webhook_triggers(org_id,connector_id,event_type,goal,enabled,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(org_id,connector_id,event_type) DO UPDATE SET "
                "goal=excluded.goal,enabled=excluded.enabled,updated_at=excluded.updated_at",
                (org_id, connector_id, event_type, goal, int(enabled), actor_id, timestamp, timestamp))
            self._append_operational_event(connection, org_id, actor_id, "trigger", "webhook.registered",
                                           "webhook_trigger", f"{connector_id}:{event_type}",
                                           request_id, trace_id, {"enabled": enabled})
            return dict(connection.execute(
                "SELECT * FROM webhook_triggers WHERE org_id=? AND connector_id=? AND event_type=?",
                (org_id, connector_id, event_type)).fetchone())

    def webhook_trigger(self, org_id: str, connector_id: str, event_type: str) -> dict | None:
        with self.reading() as connection:
            row = connection.execute(
                "SELECT * FROM webhook_triggers WHERE org_id=? AND connector_id=? AND event_type=? AND enabled=1",
                (org_id, connector_id, event_type)).fetchone()
            return dict(row) if row else None

    def create_schedule(self, org_id: str, schedule_id: str, kind: str, goal: str,
                        next_fire_at: str, actor_id: str, request_id: str, trace_id: str,
                        interval_seconds: int | None = None, daily_at: str | None = None) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO schedules(id,org_id,kind,interval_seconds,daily_at,goal,enabled,next_fire_at,"
                "last_fire_at,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,NULL,?,?,?) "
                "ON CONFLICT(org_id,id) DO UPDATE SET kind=excluded.kind,interval_seconds=excluded.interval_seconds,"
                "daily_at=excluded.daily_at,goal=excluded.goal,next_fire_at=excluded.next_fire_at,"
                "updated_at=excluded.updated_at",
                (schedule_id, org_id, kind, interval_seconds, daily_at, goal, next_fire_at,
                 actor_id, timestamp, timestamp))
            self._append_operational_event(connection, org_id, actor_id, "trigger", "schedule.created",
                                           "schedule", schedule_id, request_id, trace_id, {"kind": kind})
            return dict(connection.execute("SELECT * FROM schedules WHERE org_id=? AND id=?",
                                           (org_id, schedule_id)).fetchone())

    def set_schedule_enabled(self, org_id: str, schedule_id: str, enabled: bool, actor_id: str,
                             request_id: str, trace_id: str) -> dict:
        with self.transaction() as connection:
            updated = connection.execute("UPDATE schedules SET enabled=?,updated_at=? WHERE org_id=? AND id=?",
                                         (int(enabled), utc_now(), org_id, schedule_id))
            if updated.rowcount != 1:
                raise NotFound("schedule not found")
            self._append_operational_event(connection, org_id, actor_id, "trigger",
                                           "schedule.enabled" if enabled else "schedule.disabled",
                                           "schedule", schedule_id, request_id, trace_id, {})
            return dict(connection.execute("SELECT * FROM schedules WHERE org_id=? AND id=?",
                                           (org_id, schedule_id)).fetchone())

    def schedules(self, org_id: str) -> list[dict]:
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM schedules WHERE org_id=? ORDER BY id", (org_id,))]

    def claim_due_schedule(self, org_id: str, schedule_id: str, fired_at: str,
                           next_fire_at: str) -> bool:
        """Advance the fence, and report whether *this* caller was the one that moved it.

        The UPDATE is the claim: it only matches while next_fire_at is still the due time,
        so a second sweeper racing the same schedule updates nothing and fires nothing.
        """
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE schedules SET next_fire_at=?,last_fire_at=?,updated_at=? "
                "WHERE org_id=? AND id=? AND enabled=1 AND next_fire_at<=?",
                (next_fire_at, fired_at, utc_now(), org_id, schedule_id, fired_at))
            return updated.rowcount == 1

    def enqueue_trigger(self, org_id: str, intake_id: str, source: str, source_ref: str,
                        goal: str) -> tuple[dict, bool]:
        """Queue work the world asked for. Replaying the same trigger returns the same row."""
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = connection.execute("SELECT * FROM trigger_intake WHERE org_id=? AND id=?",
                                       (org_id, intake_id)).fetchone()
            if prior:
                return dict(prior), False
            connection.execute(
                "INSERT INTO trigger_intake(id,org_id,source,source_ref,goal,status,run_id,attempts,"
                "last_error,created_at,updated_at) VALUES(?,?,?,?,?,'queued',NULL,0,NULL,?,?)",
                (intake_id, org_id, source, source_ref, goal, timestamp, timestamp))
            return dict(connection.execute("SELECT * FROM trigger_intake WHERE org_id=? AND id=?",
                                           (org_id, intake_id)).fetchone()), True

    def queued_triggers(self, org_id: str, limit: int = 20) -> list[dict]:
        """The work queue, oldest first. First in, first out, and nothing overtakes.

        `created_at` is stored to the second, so several ideas typed in the same second
        share one, and ordering by it alone leaves their order to whatever plan SQLite
        picks. `rowid` is the insertion counter, so it breaks every tie the way the queue
        was actually filled -- the guarantee is then written down rather than inherited
        from an index the optimizer is free to stop using.

        A retry keeps its place: `settle_trigger` never touches `created_at`, so an idea
        the planner could not reach stays at the front instead of going to the back of the
        line for a fault that was not its own.
        """
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM trigger_intake WHERE org_id=? AND status='queued' "
                "ORDER BY created_at, rowid LIMIT ?",
                (org_id, int(limit)))]

    def unfinished_triggers(self, org_id: str, limit: int = 50) -> list[dict]:
        """Work that has been asked for and is not visible as a run.

        `queued_triggers` answers the planner's question -- what is left to plan. This answers
        the operator's: what did I ask for that I cannot see. Three states qualify, and the
        third is the one that matters most. `queued` and `started` differ from the run list
        for as long as a sweep takes, because intake marks a trigger `started` at once while
        the read model is mirrored only at the end of the pass. `failed` never becomes a run
        at all -- and leaving it out, as this first did, deleted the operator's request from
        every screen after the planner had spent real money failing at it.
        """
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT t.* FROM trigger_intake t WHERE t.org_id=? "
                "AND t.status IN ('queued','started','failed') "
                "AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.org_id=t.org_id AND r.id=t.run_id) "
                "ORDER BY t.created_at LIMIT ?", (org_id, int(limit)))]

    def failed_triggers(self, limit: int = 100) -> list[dict]:
        """Every organization's abandoned requests, for the escalation sweep."""
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM trigger_intake WHERE status='failed' ORDER BY updated_at DESC "
                "LIMIT ?", (int(limit),))]

    def stuck_triggers(self, before: str, limit: int = 100) -> list[dict]:
        """Requests still queued, already failing, and older than `before`.

        A transient failure spends no attempt, so these retry every sweep indefinitely --
        correct while the other end is briefly busy, and silence nobody wants during a long
        outage. `before` is an ISO timestamp; anything created earlier and still carrying an
        error has been failing long enough that a person should hear about it.
        """
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM trigger_intake WHERE status='queued' AND last_error IS NOT NULL "
                "AND created_at < ? ORDER BY created_at LIMIT ?", (before, int(limit)))]

    def settle_trigger(self, org_id: str, intake_id: str, status: str,
                       run_id: str | None, error: str | None,
                       count_attempt: bool = True) -> dict:
        """Record what happened to a queued trigger.

        `count_attempt=False` records the error without spending one of the trigger's three
        chances. It exists for failures that are the other end being busy: a 529 cost a real
        request its whole budget in one outage, and counting a server's bad minute against a
        person's idea is how work gets thrown away for a fault that fixes itself.
        """
        if status not in {"queued", "started", "failed", "withdrawn"}:
            raise StoreError("trigger status is not a settlement outcome")
        with self.transaction() as connection:
            updated = connection.execute(
                f"UPDATE trigger_intake SET status=?,run_id=?,last_error=?,"
                f"attempts=attempts+{1 if count_attempt else 0},updated_at=? "
                "WHERE org_id=? AND id=? AND status='queued'",
                (status, run_id, (error or "")[:512] or None, utc_now(), org_id, intake_id))
            if updated.rowcount != 1:
                raise Conflict("trigger was already settled")
            return dict(connection.execute("SELECT * FROM trigger_intake WHERE org_id=? AND id=?",
                                           (org_id, intake_id)).fetchone())

    def record_webhook_nonce(self, org_id: str, connector_id: str, nonce: str, expires_at: str) -> None:
        with self.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO webhook_nonces(org_id,connector_id,nonce,observed_at,expires_at) VALUES(?,?,?,?,?)",
                    (org_id, connector_id, nonce, utc_now(), expires_at),
                )
            except sqlite3.IntegrityError as error:
                raise Conflict("webhook replay detected") from error
