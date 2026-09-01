#!/usr/bin/env python3
"""SQLite persistence, audit integrity, backup, and restore for MyOrg."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "runtime" / "migrations"


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    pass


class NotFound(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def digest(value: object) -> str:
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """Read scope that actually closes. sqlite3's own context manager commits but
        never closes the connection, which leaks the handle and locks the file on Windows."""
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            existing = {row["version"]: row for row in connection.execute("SELECT * FROM schema_migrations")}
            for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name.split("_", 1)[0])
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                if version in existing:
                    if existing[version]["checksum"] != checksum:
                        raise StoreError(f"migration checksum changed: {path.name}")
                    continue
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                    (version, path.name, checksum, utc_now()),
                )
                applied.append(version)
        os.chmod(self.path, 0o600)
        return applied

    def bootstrap_organization(self, org_id: str, name: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO organizations(id,name,status,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (org_id, name, "active", utc_now()),
            )

    def upsert_actor(self, org_id: str, actor_id: str, actor_type: str, display_name: str, roles: list[str]) -> None:
        timestamp = utc_now()
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM organizations WHERE id=? AND status='active'", (org_id,)).fetchone():
                raise NotFound("active organization not found")
            connection.execute(
                "INSERT INTO actors(id,org_id,actor_type,display_name,status,created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(org_id,id) DO UPDATE SET actor_type=excluded.actor_type,display_name=excluded.display_name,status='active'",
                (actor_id, org_id, actor_type, display_name, "active", timestamp),
            )
            connection.execute("DELETE FROM role_bindings WHERE org_id=? AND actor_id=?", (org_id, actor_id))
            for role in sorted(set(roles)):
                connection.execute(
                    "INSERT INTO role_bindings(org_id,actor_id,role,created_at) VALUES(?,?,?,?)",
                    (org_id, actor_id, role, timestamp),
                )

    def actor(self, org_id: str, actor_id: str) -> dict:
        with self.reading() as connection:
            row = connection.execute(
                "SELECT a.id,a.org_id,a.actor_type,a.display_name,a.status FROM actors a "
                "JOIN organizations o ON o.id=a.org_id AND o.status='active' WHERE a.org_id=? AND a.id=?",
                (org_id, actor_id),
            ).fetchone()
            if not row:
                raise NotFound("actor not found")
            result = dict(row)
            result["roles"] = [item["role"] for item in connection.execute(
                "SELECT role FROM role_bindings WHERE org_id=? AND actor_id=? ORDER BY role", (org_id, actor_id)
            )]
            return result

    def set_actor_status(self, org_id: str, actor_id: str, status: str) -> dict:
        if status not in {"active", "disabled"}:
            raise StoreError("invalid actor status")
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE actors SET status=? WHERE org_id=? AND id=?", (status, org_id, actor_id)
            )
            if updated.rowcount != 1:
                raise NotFound("actor not found")
        return self.actor(org_id, actor_id)

    def set_organization_status(self, org_id: str, status: str) -> None:
        if status not in {"active", "suspended"}:
            raise StoreError("invalid organization status")
        with self.transaction() as connection:
            updated = connection.execute("UPDATE organizations SET status=? WHERE id=?", (status, org_id))
            if updated.rowcount != 1:
                raise NotFound("organization not found")

    def bind_identity(self, issuer: str, subject: str, org_id: str, actor_id: str) -> dict:
        subject = subject.strip().lower()
        self.actor(org_id, actor_id)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO identity_bindings(issuer,subject,org_id,actor_id,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(issuer,subject) DO UPDATE SET org_id=excluded.org_id,actor_id=excluded.actor_id",
                (issuer, subject, org_id, actor_id, utc_now()),
            )
        return self.identity(issuer, subject)

    def identity(self, issuer: str, subject: str) -> dict:
        with self.reading() as connection:
            row = connection.execute(
                "SELECT issuer,subject,org_id,actor_id,created_at FROM identity_bindings WHERE issuer=? AND subject=?",
                (issuer, subject.strip().lower()),
            ).fetchone()
            if not row:
                raise NotFound("identity binding not found")
            return dict(row)

    def record_step_decision(self, org_id: str, actor_id: str, run_id: str, step_id: str,
                             decision: str, request_id: str, trace_id: str = "") -> None:
        """Mirror a workflow-step decision into the operator read model.

        The run log stays the system of record; this is what the console and any later
        report read, so both halves say the same thing about who decided what."""
        with self.transaction() as connection:
            self._append_operational_event(
                connection, org_id, actor_id, "runtime", "step.decision", "run_step",
                f"{run_id}/{step_id}", request_id, trace_id, {"decision": decision})

    def record_gateway_nonce(self, issuer: str, nonce: str, expires_epoch: int) -> None:
        expires = datetime.fromtimestamp(expires_epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO gateway_nonces(issuer,nonce,observed_at,expires_at) VALUES(?,?,?,?)",
                    (issuer, nonce, utc_now(), expires),
                )
            except sqlite3.IntegrityError as error:
                raise Conflict("gateway request replay detected") from error

    def _append_operational_event(self, connection: sqlite3.Connection, org_id: str, actor_id: str,
                                  category: str, action: str, resource_type: str, resource_id: str,
                                  request_id: str, trace_id: str, metadata: dict) -> None:
        previous = connection.execute(
            "SELECT seq,event_hash FROM operational_events WHERE org_id=? ORDER BY seq DESC LIMIT 1", (org_id,)
        ).fetchone()
        seq = int(previous["seq"]) + 1 if previous else 1
        previous_hash = previous["event_hash"] if previous else None
        created_at = utc_now()
        value = {"org_id": org_id, "seq": seq, "category": category, "action": action,
                 "actor_id": actor_id, "resource_type": resource_type, "resource_id": resource_id,
                 "request_id": request_id, "trace_id": trace_id, "metadata": metadata,
                 "previous_hash": previous_hash, "created_at": created_at}
        event_hash = digest(value)
        try:
            connection.execute(
                "INSERT INTO operational_events(org_id,seq,category,action,actor_id,resource_type,resource_id,request_id,trace_id,metadata_json,previous_hash,event_hash,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (org_id, seq, category, action, actor_id, resource_type, resource_id, request_id, trace_id,
                 canonical(metadata), previous_hash, event_hash, created_at),
            )
        except sqlite3.IntegrityError as error:
            raise Conflict("operational request id was already used") from error

    @staticmethod
    def _default_ui_state(org_id: str, actor_id: str) -> dict:
        return {"org_id": org_id, "actor_id": actor_id, "schema_version": 1, "active_view": "overview",
                "time_range": "30d", "filters": {"queue": "all", "flow": "future"},
                "sort": {"queue": "updated_desc"}, "scroll_position": 0,
                "current_project_id": None, "revision": 0, "updated_at": None}

    def ui_state(self, org_id: str, actor_id: str) -> dict:
        self.actor(org_id, actor_id)
        with self.reading() as connection:
            row = connection.execute("SELECT * FROM ui_states WHERE org_id=? AND actor_id=?", (org_id, actor_id)).fetchone()
        if not row:
            return self._default_ui_state(org_id, actor_id)
        result = dict(row)
        result["filters"] = json.loads(result.pop("filters_json"))
        result["sort"] = json.loads(result.pop("sort_json"))
        return result

    def save_ui_state(self, org_id: str, actor_id: str, state: dict, expected_revision: int,
                      request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = connection.execute("SELECT revision FROM ui_states WHERE org_id=? AND actor_id=?", (org_id, actor_id)).fetchone()
            actual = int(prior["revision"]) if prior else 0
            if actual != expected_revision:
                raise Conflict("UI state revision is stale")
            revision = actual + 1
            connection.execute(
                "INSERT INTO ui_states(org_id,actor_id,schema_version,active_view,time_range,filters_json,sort_json,scroll_position,current_project_id,revision,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(org_id,actor_id) DO UPDATE SET schema_version=excluded.schema_version,active_view=excluded.active_view,time_range=excluded.time_range,filters_json=excluded.filters_json,sort_json=excluded.sort_json,scroll_position=excluded.scroll_position,current_project_id=excluded.current_project_id,revision=excluded.revision,updated_at=excluded.updated_at",
                (org_id, actor_id, 1, state["active_view"], state["time_range"], canonical(state["filters"]),
                 canonical(state["sort"]), state["scroll_position"], state.get("current_project_id"), revision, timestamp),
            )
            self._append_operational_event(connection, org_id, actor_id, "ui", "state.saved", "ui_state",
                                           actor_id, request_id, trace_id, {"revision": revision})
        return self.ui_state(org_id, actor_id)

    def reset_ui_state(self, org_id: str, actor_id: str, request_id: str, trace_id: str) -> dict:
        with self.transaction() as connection:
            connection.execute("DELETE FROM ui_states WHERE org_id=? AND actor_id=?", (org_id, actor_id))
            self._append_operational_event(connection, org_id, actor_id, "ui", "state.reset", "ui_state",
                                           actor_id, request_id, trace_id, {})
        return self._default_ui_state(org_id, actor_id)

    def create_project_intake(self, org_id: str, actor_id: str, project_id: str, body: dict,
                              request_id: str, trace_id: str) -> tuple[dict, bool]:
        request_hash = digest(body)
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = connection.execute(
                "SELECT operation,request_hash,resource_id FROM idempotency_requests WHERE org_id=? AND request_id=?",
                (org_id, request_id),
            ).fetchone()
            if prior:
                if prior["operation"] != "project.create" or prior["request_hash"] != request_hash:
                    raise Conflict("idempotency key reused with a different request")
                return self._project(connection, org_id, prior["resource_id"]), False
            connection.execute(
                "INSERT INTO project_intakes(id,org_id,title,sponsor,decision_owner,affected_user,desired_outcome,documents_json,status,revision,created_by,updated_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, org_id, body["title"], body["sponsor"], body["decision_owner"], body["affected_user"],
                 body["desired_outcome"], canonical(body["documents"]), body["status"], 1, actor_id, actor_id, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO idempotency_requests(org_id,request_id,operation,request_hash,resource_id,created_at) VALUES(?,?,?,?,?,?)",
                (org_id, request_id, "project.create", request_hash, project_id, timestamp),
            )
            self._append_operational_event(connection, org_id, actor_id, "project", "intake.created", "project",
                                           project_id, f"project-event-{request_id}", trace_id, {"status": body["status"]})
            return self._project(connection, org_id, project_id), True

    def _project(self, connection: sqlite3.Connection, org_id: str, project_id: str) -> dict:
        row = connection.execute("SELECT * FROM project_intakes WHERE org_id=? AND id=?", (org_id, project_id)).fetchone()
        if not row:
            raise NotFound("project intake not found")
        result = dict(row)
        result["documents"] = json.loads(result.pop("documents_json"))
        return result

    def project_intake(self, org_id: str, project_id: str) -> dict:
        with self.reading() as connection:
            return self._project(connection, org_id, project_id)

    def update_project_intake(self, org_id: str, actor_id: str, project_id: str, body: dict,
                              expected_revision: int, request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            prior = self._project(connection, org_id, project_id)
            if int(prior["revision"]) != expected_revision:
                raise Conflict("project intake revision is stale")
            revision = expected_revision + 1
            updated = connection.execute(
                "UPDATE project_intakes SET title=?,sponsor=?,decision_owner=?,affected_user=?,desired_outcome=?,documents_json=?,status=?,revision=?,updated_by=?,updated_at=? WHERE org_id=? AND id=? AND revision=?",
                (body["title"], body["sponsor"], body["decision_owner"], body["affected_user"], body["desired_outcome"],
                 canonical(body["documents"]), body["status"], revision, actor_id, timestamp, org_id, project_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise Conflict("project intake revision is stale")
            self._append_operational_event(connection, org_id, actor_id, "project", "intake.updated", "project",
                                           project_id, request_id, trace_id, {"revision": revision, "status": body["status"]})
            return self._project(connection, org_id, project_id)

    def purge_transient(self, now: str | None = None, idempotency_days: int = 30) -> dict:
        current = now or utc_now()
        parsed = datetime.fromisoformat(current.replace("Z", "+00:00"))
        cutoff = (parsed - timedelta(days=idempotency_days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        with self.transaction() as connection:
            gateway = connection.execute("DELETE FROM gateway_nonces WHERE expires_at<=?", (current,)).rowcount
            webhook = connection.execute("DELETE FROM webhook_nonces WHERE expires_at<=?", (current,)).rowcount
            revoked = connection.execute("DELETE FROM revoked_tokens WHERE expires_at<=?", (current,)).rowcount
            idempotency = connection.execute("DELETE FROM idempotency_requests WHERE created_at<?", (cutoff,)).rowcount
        return {"gateway_nonces": gateway, "webhook_nonces": webhook, "revoked_tokens": revoked,
                "idempotency_requests": idempotency}

    def revoke_token(self, org_id: str, jti: str, expires_at: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO revoked_tokens(org_id,jti,expires_at,revoked_at) VALUES(?,?,?,?)",
                (org_id, jti, expires_at, utc_now()),
            )

    def token_revoked(self, org_id: str, jti: str) -> bool:
        with self.reading() as connection:
            return bool(connection.execute("SELECT 1 FROM revoked_tokens WHERE org_id=? AND jti=?", (org_id, jti)).fetchone())

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

    def register_connector(self, org_id: str, manifest: dict) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            if manifest["kind"] != "fixture" and manifest["enabled"]:
                authorization = connection.execute(
                    "SELECT status,expires_at FROM connector_authorizations WHERE org_id=? AND connector_id=?",
                    (org_id, manifest["id"]),
                ).fetchone()
                if not authorization or authorization["status"] != "authorized" or authorization["expires_at"] <= timestamp:
                    raise Conflict("live connector cannot be enabled without a current human authorization")
            connection.execute(
                "INSERT INTO connector_registrations(id,org_id,kind,mode,base_url,allowed_hosts_json,allowed_actions_json,secret_ref,timeout_seconds,max_response_bytes,enabled,config_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(org_id,id) DO UPDATE SET kind=excluded.kind,mode=excluded.mode,base_url=excluded.base_url,allowed_hosts_json=excluded.allowed_hosts_json,allowed_actions_json=excluded.allowed_actions_json,secret_ref=excluded.secret_ref,timeout_seconds=excluded.timeout_seconds,max_response_bytes=excluded.max_response_bytes,enabled=excluded.enabled,config_revision=excluded.config_revision,updated_at=excluded.updated_at",
                (manifest["id"], org_id, manifest["kind"], manifest["mode"], manifest["base_url"],
                 canonical(manifest["allowed_hosts"]), canonical(manifest["allowed_actions"]), manifest.get("secret_ref"),
                 manifest["timeout_seconds"], manifest["max_response_bytes"], int(manifest["enabled"]),
                 digest(manifest), timestamp, timestamp),
            )
            return self._connector(connection, org_id, manifest["id"])

    def connector_authorization(self, org_id: str, connector_id: str) -> dict | None:
        with self.reading() as connection:
            row = connection.execute(
                "SELECT provider_account_ref,scopes_json,status,authorized_by,authorized_at,expires_at,revoked_at "
                "FROM connector_authorizations WHERE org_id=? AND connector_id=?", (org_id, connector_id)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["scopes"] = json.loads(result.pop("scopes_json"))
        if result["status"] == "authorized" and result["expires_at"] <= utc_now():
            result["status"] = "expired"
        return result

    def authorize_connector(self, org_id: str, connector_id: str, provider_account_ref: str,
                            scopes: list[str], token_secret_ref: str, refresh_secret_ref: str | None,
                            expires_at: str, actor_id: str, request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        if expires_at <= timestamp:
            raise Conflict("connector authorization is already expired")
        with self.transaction() as connection:
            connector = self._connector(connection, org_id, connector_id)
            if connector["kind"] == "fixture":
                raise Conflict("fixture connectors do not accept OAuth authorization records")
            if connector["secret_ref"] != token_secret_ref:
                raise Conflict("authorization token reference must match the admitted connector manifest")
            connection.execute(
                "INSERT INTO connector_authorizations(org_id,connector_id,provider_account_ref,scopes_json,token_secret_ref,refresh_secret_ref,status,authorized_by,authorized_at,expires_at,revoked_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(org_id,connector_id) DO UPDATE SET provider_account_ref=excluded.provider_account_ref,scopes_json=excluded.scopes_json,token_secret_ref=excluded.token_secret_ref,refresh_secret_ref=excluded.refresh_secret_ref,status='authorized',authorized_by=excluded.authorized_by,authorized_at=excluded.authorized_at,expires_at=excluded.expires_at,revoked_at=NULL",
                (org_id, connector_id, provider_account_ref, canonical(scopes), token_secret_ref, refresh_secret_ref,
                 "authorized", actor_id, timestamp, expires_at),
            )
            self._append_operational_event(connection, org_id, actor_id, "connector", "authorization.recorded",
                                           "connector", connector_id, request_id, trace_id,
                                           {"scope_count": len(scopes), "expires_at": expires_at})
        result = self.connector_authorization(org_id, connector_id)
        assert result is not None
        return result

    def set_connector_enabled(self, org_id: str, connector_id: str, enabled: bool, actor_id: str,
                              request_id: str, trace_id: str) -> dict:
        with self.transaction() as connection:
            connector = self._connector(connection, org_id, connector_id)
            if enabled and connector["kind"] != "fixture":
                authorization = connection.execute(
                    "SELECT status,expires_at FROM connector_authorizations WHERE org_id=? AND connector_id=?",
                    (org_id, connector_id),
                ).fetchone()
                if not authorization or authorization["status"] != "authorized" or authorization["expires_at"] <= utc_now():
                    raise Conflict("live connector cannot be enabled without a current human authorization")
            connection.execute("UPDATE connector_registrations SET enabled=?,updated_at=? WHERE org_id=? AND id=?",
                               (int(enabled), utc_now(), org_id, connector_id))
            self._append_operational_event(connection, org_id, actor_id, "connector",
                                           "enabled" if enabled else "disabled", "connector", connector_id,
                                           request_id, trace_id, {})
            return self._connector(connection, org_id, connector_id)

    def revoke_connector_authorization(self, org_id: str, connector_id: str, actor_id: str,
                                       request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE connector_authorizations SET status='revoked',revoked_at=? "
                "WHERE org_id=? AND connector_id=? AND status='authorized'", (timestamp, org_id, connector_id)
            )
            if updated.rowcount != 1:
                raise NotFound("active connector authorization not found")
            connection.execute("UPDATE connector_registrations SET enabled=0,updated_at=? WHERE org_id=? AND id=?",
                               (timestamp, org_id, connector_id))
            self._append_operational_event(connection, org_id, actor_id, "connector", "authorization.revoked",
                                           "connector", connector_id, request_id, trace_id, {})
        result = self.connector_authorization(org_id, connector_id)
        assert result is not None
        return result

    def _connector(self, connection: sqlite3.Connection, org_id: str, connector_id: str) -> dict:
        row = connection.execute("SELECT * FROM connector_registrations WHERE org_id=? AND id=?", (org_id, connector_id)).fetchone()
        if not row:
            raise NotFound("connector not found")
        result = dict(row)
        result["allowed_hosts"] = json.loads(result.pop("allowed_hosts_json"))
        result["allowed_actions"] = json.loads(result.pop("allowed_actions_json"))
        result["enabled"] = bool(result["enabled"])
        return result

    def connector(self, org_id: str, connector_id: str) -> dict:
        with self.reading() as connection:
            return self._connector(connection, org_id, connector_id)

    def connectors(self, org_id: str) -> list[dict]:
        with self.reading() as connection:
            ids = [row["id"] for row in connection.execute("SELECT id FROM connector_registrations WHERE org_id=? ORDER BY id", (org_id,))]
            return [self._connector(connection, org_id, item) for item in ids]

    def connector_receipt(self, org_id: str, connector_id: str, idempotency_key: str) -> dict | None:
        with self.reading() as connection:
            row = connection.execute(
                "SELECT * FROM connector_receipts WHERE org_id=? AND connector_id=? AND idempotency_key=?",
                (org_id, connector_id, idempotency_key),
            ).fetchone()
            return dict(row) if row else None

    def reconcile_connector_receipt(self, org_id: str, receipt_id: str, provider_status: str,
                                    details_sha256: str, actor_id: str, request_id: str, trace_id: str) -> dict:
        timestamp = utc_now()
        with self.transaction() as connection:
            if not connection.execute("SELECT 1 FROM connector_receipts WHERE org_id=? AND id=?",
                                      (org_id, receipt_id)).fetchone():
                raise NotFound("connector receipt not found")
            connection.execute(
                "INSERT INTO connector_reconciliations(org_id,receipt_id,provider_status,details_sha256,checked_by,checked_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(org_id,receipt_id) DO UPDATE SET provider_status=excluded.provider_status,details_sha256=excluded.details_sha256,checked_by=excluded.checked_by,checked_at=excluded.checked_at",
                (org_id, receipt_id, provider_status, details_sha256, actor_id, timestamp),
            )
            self._append_operational_event(connection, org_id, actor_id, "connector", "receipt.reconciled",
                                           "connector_receipt", receipt_id, request_id, trace_id,
                                           {"provider_status": provider_status, "details_sha256": details_sha256})
            row = connection.execute("SELECT * FROM connector_reconciliations WHERE org_id=? AND receipt_id=?",
                                     (org_id, receipt_id)).fetchone()
            return dict(row)

    def settle_connector_receipt(self, org_id: str, receipt_id: str, status: str,
                                 provider_receipt: str, response_sha256: str, note: str,
                                 actor_id: str) -> dict:
        """Close out a live call. Only an in-flight receipt may be settled, and only once --
        a second settlement would let a retry overwrite what actually happened."""
        if status not in {"accepted", "failed", "in_flight"}:
            raise StoreError("connector receipt status is not a settlement outcome")
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM connector_receipts WHERE org_id=? AND id=?",
                                     (org_id, receipt_id)).fetchone()
            if not row:
                raise NotFound("connector receipt not found")
            if row["status"] != "in_flight":
                raise Conflict("connector receipt was already settled")
            settled_at = utc_now() if status != "in_flight" else None
            connection.execute(
                "UPDATE connector_receipts SET status=?,provider_receipt=?,response_sha256=?,"
                "outcome_note=?,settled_at=? WHERE org_id=? AND id=?",
                (status, provider_receipt, response_sha256, note[:512], settled_at, org_id, receipt_id))
            self._append_operational_event(connection, org_id, actor_id, "connector", f"receipt.{status}",
                                           "connector_receipt", receipt_id, f"settle-{receipt_id}",
                                           f"settle-{receipt_id}",
                                           {"provider_receipt": provider_receipt, "note": note[:256]})
            return dict(connection.execute("SELECT * FROM connector_receipts WHERE org_id=? AND id=?",
                                           (org_id, receipt_id)).fetchone())

    def in_flight_connector_receipts(self, org_id: str) -> list[dict]:
        """Calls that left this host and were never resolved. Nothing may retry these."""
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,connector_id,idempotency_key,outcome_note,created_at FROM connector_receipts "
                "WHERE org_id=? AND status='in_flight' ORDER BY created_at", (org_id,))]

    def unreconciled_connector_receipts(self, org_id: str) -> list[dict]:
        with self.reading() as connection:
            rows = connection.execute(
                "SELECT r.id,r.connector_id,r.provider_receipt,r.status,r.created_at FROM connector_receipts r "
                "LEFT JOIN connector_reconciliations c ON c.org_id=r.org_id AND c.receipt_id=r.id "
                "WHERE r.org_id=? AND c.receipt_id IS NULL ORDER BY r.created_at", (org_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def record_connector_receipt(self, org_id: str, receipt: dict) -> dict:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO connector_receipts(id,org_id,connector_id,idempotency_key,request_hash,provider_receipt,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (receipt["id"], org_id, receipt["connector_id"], receipt["idempotency_key"], receipt["request_hash"],
                 receipt["provider_receipt"], receipt["status"], receipt["created_at"]),
            )
            return receipt

    def consume_approval_and_record_receipt(self, org_id: str, approval_id: str, actor_id: str,
                                            action_hash: str, event_request_id: str, receipt: dict) -> dict:
        """Atomically consume exact approval and persist the external-effect receipt."""
        with self.transaction() as connection:
            prior = connection.execute(
                "SELECT * FROM connector_receipts WHERE org_id=? AND connector_id=? AND idempotency_key=?",
                (org_id, receipt["connector_id"], receipt["idempotency_key"]),
            ).fetchone()
            if prior:
                if prior["request_hash"] != receipt["request_hash"]:
                    raise Conflict("connector idempotency key reused with a different request")
                return dict(prior)
            approval = self._approval(connection, org_id, approval_id)
            if approval["status"] != "approved":
                raise Conflict("approval is not approved or was already consumed")
            if approval["action_hash"] != action_hash:
                raise Conflict("approval does not match the exact action hash")
            if approval["expires_at"] <= utc_now():
                connection.execute("UPDATE approvals SET status='expired' WHERE org_id=? AND id=?", (org_id, approval_id))
                raise Conflict("approval expired")
            timestamp = utc_now()
            updated = connection.execute(
                "UPDATE approvals SET status='consumed',consumed_by=?,consumed_at=? "
                "WHERE org_id=? AND id=? AND status='approved'",
                (actor_id, timestamp, org_id, approval_id),
            )
            if updated.rowcount != 1:
                raise Conflict("approval could not be consumed")
            connection.execute(
                "INSERT INTO connector_receipts(id,org_id,connector_id,idempotency_key,request_hash,provider_receipt,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (receipt["id"], org_id, receipt["connector_id"], receipt["idempotency_key"], receipt["request_hash"],
                 receipt["provider_receipt"], receipt["status"], receipt["created_at"]),
            )
            self._append_event(connection, org_id, approval["run_id"], "approval.consumed", actor_id, event_request_id,
                               {"approval_id": approval_id, "action_hash": action_hash,
                                "connector_id": receipt["connector_id"], "receipt_id": receipt["id"]})
            return receipt

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
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM trigger_intake WHERE org_id=? AND status='queued' ORDER BY created_at LIMIT ?",
                (org_id, int(limit)))]

    def settle_trigger(self, org_id: str, intake_id: str, status: str,
                       run_id: str | None, error: str | None) -> dict:
        if status not in {"queued", "started", "failed"}:
            raise StoreError("trigger status is not a settlement outcome")
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE trigger_intake SET status=?,run_id=?,last_error=?,attempts=attempts+1,updated_at=? "
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

    def verify(self) -> dict:
        with self.reading() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise StoreError(f"database integrity failed: {integrity}")
            migrations = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            events = connection.execute("SELECT * FROM events ORDER BY org_id,run_id,seq").fetchall()
            operational = connection.execute("SELECT * FROM operational_events ORDER BY org_id,seq").fetchall()
        previous: dict[tuple[str, str], str | None] = {}
        for row in events:
            key = (row["org_id"], row["run_id"])
            expected_previous = previous.get(key)
            if row["previous_hash"] != expected_previous:
                raise StoreError("event chain previous hash mismatch")
            event = {
                "org_id": row["org_id"], "run_id": row["run_id"], "seq": row["seq"],
                "event_type": row["event_type"], "actor_id": row["actor_id"], "request_id": row["request_id"],
                "payload": json.loads(row["payload_json"]), "previous_hash": row["previous_hash"], "created_at": row["created_at"],
            }
            if digest(event) != row["event_hash"]:
                raise StoreError("event hash mismatch")
            previous[key] = row["event_hash"]
        operational_previous: dict[str, str | None] = {}
        for row in operational:
            expected_previous = operational_previous.get(row["org_id"])
            if row["previous_hash"] != expected_previous:
                raise StoreError("operational event chain previous hash mismatch")
            event = {
                "org_id": row["org_id"], "seq": row["seq"], "category": row["category"],
                "action": row["action"], "actor_id": row["actor_id"],
                "resource_type": row["resource_type"], "resource_id": row["resource_id"],
                "request_id": row["request_id"], "trace_id": row["trace_id"],
                "metadata": json.loads(row["metadata_json"]), "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            if digest(event) != row["event_hash"]:
                raise StoreError("operational event hash mismatch")
            operational_previous[row["org_id"]] = row["event_hash"]
        return {"integrity": "ok", "migrations": migrations, "events": len(events),
                "operational_events": len(operational)}

    def backup(self, destination: str | Path) -> dict:
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        if temporary.exists():
            temporary.unlink()
        source = self.connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(temporary, 0o600)
        check = Store(temporary).verify()
        os.replace(temporary, destination)
        checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest = {"version": 1, "database": destination.name, "sha256": checksum, "created_at": utc_now(), "verification": check}
        manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
        manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o600)
        return manifest


def restore_backup(backup_path: str | Path, target_path: str | Path) -> dict:
    backup = Path(backup_path).resolve()
    target = Path(target_path).resolve()
    manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
    if not backup.is_file() or not manifest_path.is_file():
        raise StoreError("backup or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("sha256") != hashlib.sha256(backup.read_bytes()).hexdigest():
        raise StoreError("backup checksum mismatch")
    Store(backup).verify()
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_restore = None
    if target.exists():
        pre_restore = target.with_name(f"{target.stem}.pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{target.suffix}")
        Store(target).backup(pre_restore)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".restore", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(backup, temporary)
        Store(temporary).verify()
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"restored": str(target), "source_sha256": manifest["sha256"], "pre_restore": str(pre_restore) if pre_restore else None}
