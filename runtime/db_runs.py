#!/usr/bin/env python3
"""Runs, their event chain, and the approvals that gate them."""
from __future__ import annotations

import json
import sqlite3

from runtime.db_core import Conflict, NotFound, canonical, digest, utc_now


class RunsMixin:
    """Runs, their event chain, and the approvals that gate them."""

    def _append_event(self, connection: sqlite3.Connection, org_id: str, run_id: str, event_type: str,
                      actor_id: str, request_id: str, payload: dict) -> dict:
        previous = connection.execute(
            "SELECT seq,event_hash FROM events WHERE org_id=? AND run_id=? ORDER BY seq DESC LIMIT 1", (org_id, run_id)
        ).fetchone()
        seq = int(previous["seq"]) + 1 if previous else 1
        previous_hash = previous["event_hash"] if previous else None
        created_at = utc_now()
        event = {
            "org_id": org_id, "run_id": run_id, "seq": seq, "event_type": event_type,
            "actor_id": actor_id, "request_id": request_id, "payload": payload,
            "previous_hash": previous_hash, "created_at": created_at,
        }
        event_hash = digest(event)
        connection.execute(
            "INSERT INTO events(org_id,run_id,seq,event_type,actor_id,request_id,payload_json,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (org_id, run_id, seq, event_type, actor_id, request_id, canonical(payload), previous_hash, event_hash, created_at),
        )
        event["event_hash"] = event_hash
        return event

    def create_run(self, org_id: str, run_id: str, workflow_id: str, workflow_revision: str, goal: str,
                   data_class: str, actor_id: str, request_id: str) -> tuple[dict, bool]:
        request_body = {"run_id": run_id, "workflow_id": workflow_id, "workflow_revision": workflow_revision,
                        "goal": goal, "data_class": data_class}
        request_hash = digest(request_body)
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = connection.execute(
                "SELECT operation,request_hash,resource_id FROM idempotency_requests WHERE org_id=? AND request_id=?",
                (org_id, request_id),
            ).fetchone()
            if prior:
                if prior["operation"] != "run.create" or prior["request_hash"] != request_hash:
                    raise Conflict("idempotency key reused with a different request")
                return self._run(connection, org_id, prior["resource_id"]), False
            connection.execute(
                "INSERT INTO runs(id,org_id,workflow_id,workflow_revision,goal,data_class,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, org_id, workflow_id, workflow_revision, goal, data_class, "active", actor_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO idempotency_requests(org_id,request_id,operation,request_hash,resource_id,created_at) VALUES(?,?,?,?,?,?)",
                (org_id, request_id, "run.create", request_hash, run_id, timestamp),
            )
            self._append_event(connection, org_id, run_id, "run.created", actor_id, request_id, request_body)
            return self._run(connection, org_id, run_id), True

    def _run(self, connection: sqlite3.Connection, org_id: str, run_id: str) -> dict:
        row = connection.execute("SELECT * FROM runs WHERE org_id=? AND id=?", (org_id, run_id)).fetchone()
        if not row:
            raise NotFound("run not found")
        return dict(row)

    def run(self, org_id: str, run_id: str) -> dict:
        with self.reading() as connection:
            return self._run(connection, org_id, run_id)

    def runs(self, org_id: str, limit: int = 100) -> list[dict]:
        """The organization's runs, most recently changed first -- the read model the
        Control Center lists and stops runs from."""
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,workflow_id,goal,status,runtime_status,cycle_count,max_cycles,"
                "created_by,created_at,updated_at FROM runs WHERE org_id=? "
                "ORDER BY updated_at DESC, id LIMIT ?", (org_id, limit))]

    def run_events(self, org_id: str, run_id: str) -> list[dict]:
        with self.reading() as connection:
            self._run(connection, org_id, run_id)
            rows = connection.execute(
                "SELECT seq,event_type,actor_id,request_id,payload_json,previous_hash,event_hash,created_at FROM events WHERE org_id=? AND run_id=? ORDER BY seq",
                (org_id, run_id),
            )
            return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def create_approval(self, org_id: str, approval_id: str, run_id: str, action: str, action_hash: str,
                        target_ref: str, payload_ref: str, payload_sha256: str, requested_by: str,
                        expires_at: str, request_id: str) -> dict:
        with self.transaction() as connection:
            self._run(connection, org_id, run_id)
            timestamp = utc_now()
            connection.execute(
                "INSERT INTO approvals(id,org_id,run_id,action,action_hash,target_ref,payload_ref,payload_sha256,requested_by,requested_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (approval_id, org_id, run_id, action, action_hash, target_ref, payload_ref, payload_sha256,
                 requested_by, timestamp, expires_at, "pending"),
            )
            self._append_event(connection, org_id, run_id, "approval.requested", requested_by, request_id,
                               {"approval_id": approval_id, "action": action, "action_hash": action_hash, "expires_at": expires_at})
            return self._approval(connection, org_id, approval_id)

    def _approval(self, connection: sqlite3.Connection, org_id: str, approval_id: str) -> dict:
        row = connection.execute("SELECT * FROM approvals WHERE org_id=? AND id=?", (org_id, approval_id)).fetchone()
        if not row:
            raise NotFound("approval not found")
        return dict(row)

    def approval(self, org_id: str, approval_id: str) -> dict:
        with self.reading() as connection:
            return self._approval(connection, org_id, approval_id)

    def decide_approval(self, org_id: str, approval_id: str, actor_id: str, action_hash: str,
                        decision: str, request_id: str) -> dict:
        with self.transaction() as connection:
            approval = self._approval(connection, org_id, approval_id)
            if approval["status"] != "pending":
                raise Conflict("approval is not pending")
            if approval["requested_by"] == actor_id:
                raise Conflict("requester cannot approve its own action")
            if approval["action_hash"] != action_hash:
                raise Conflict("approval does not match the exact action hash")
            if approval["expires_at"] <= utc_now():
                connection.execute("UPDATE approvals SET status='expired' WHERE org_id=? AND id=?", (org_id, approval_id))
                raise Conflict("approval expired")
            status = "approved" if decision == "approve" else "rejected"
            timestamp = utc_now()
            connection.execute(
                "UPDATE approvals SET status=?,decided_by=?,decided_at=? WHERE org_id=? AND id=?",
                (status, actor_id, timestamp, org_id, approval_id),
            )
            self._append_event(connection, org_id, approval["run_id"], f"approval.{status}", actor_id, request_id,
                               {"approval_id": approval_id, "action_hash": action_hash})
            return self._approval(connection, org_id, approval_id)

    def consume_approval(self, org_id: str, approval_id: str, actor_id: str, action_hash: str, request_id: str) -> dict:
        with self.transaction() as connection:
            approval = self._approval(connection, org_id, approval_id)
            if approval["status"] != "approved":
                raise Conflict("approval is not approved or was already consumed")
            if approval["action_hash"] != action_hash:
                raise Conflict("approval does not match the exact action hash")
            if approval["expires_at"] <= utc_now():
                connection.execute("UPDATE approvals SET status='expired' WHERE org_id=? AND id=?", (org_id, approval_id))
                raise Conflict("approval expired")
            timestamp = utc_now()
            connection.execute(
                "UPDATE approvals SET status='consumed',consumed_by=?,consumed_at=? WHERE org_id=? AND id=? AND status='approved'",
                (actor_id, timestamp, org_id, approval_id),
            )
            self._append_event(connection, org_id, approval["run_id"], "approval.consumed", actor_id, request_id,
                               {"approval_id": approval_id, "action_hash": action_hash})
            return self._approval(connection, org_id, approval_id)
