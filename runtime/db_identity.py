#!/usr/bin/env python3
"""Organizations, actors, identity bindings, tokens, and the operational event chain."""
from __future__ import annotations

import sqlite3

from datetime import datetime, timezone
from runtime.db_core import Conflict, NotFound, StoreError, canonical, digest, utc_now


class IdentityMixin:
    """Organizations, actors, identity bindings, tokens, and the operational event chain."""

    def bootstrap_organization(self, org_id: str, name: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO organizations(id,name,status,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (org_id, name, "active", utc_now()),
            )

    def upsert_actor(self, org_id: str, actor_id: str, actor_type: str, display_name: str, roles: list[str],
                     require_active: bool = True) -> None:
        """`require_active=False` is for the runtime's own service actors (the projector):
        they must exist while an organization is suspended, or the read model goes dark
        exactly when a person most needs it. Tokens are still refused for them -- `actor()`
        joins on an active organization regardless of how the row got there."""
        timestamp = utc_now()
        with self.transaction() as connection:
            status_filter = " AND status='active'" if require_active else ""
            if not connection.execute(f"SELECT 1 FROM organizations WHERE id=?{status_filter}", (org_id,)).fetchone():
                raise NotFound("active organization not found" if require_active else "organization not found")
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

    def organization_status(self, org_id: str) -> str:
        """'active', 'suspended', or 'missing'. Suspended already denies every token
        (`actor()` joins on an active organization); intake and webhooks read this too."""
        with self.reading() as connection:
            row = connection.execute("SELECT status FROM organizations WHERE id=?", (org_id,)).fetchone()
            return row["status"] if row else "missing"

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

    def revoke_token(self, org_id: str, jti: str, expires_at: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO revoked_tokens(org_id,jti,expires_at,revoked_at) VALUES(?,?,?,?)",
                (org_id, jti, expires_at, utc_now()),
            )

    def token_revoked(self, org_id: str, jti: str) -> bool:
        with self.reading() as connection:
            return bool(connection.execute("SELECT 1 FROM revoked_tokens WHERE org_id=? AND jti=?", (org_id, jti)).fetchone())
