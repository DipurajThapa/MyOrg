#!/usr/bin/env python3
"""Connector registration, authorization, receipts, and the metrics aggregates."""
from __future__ import annotations

import json
import sqlite3

from runtime.db_core import Conflict, NotFound, StoreError, canonical, digest, utc_now


class ConnectorsMixin:
    """Connector registration, authorization, receipts, and the metrics aggregates."""

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

    # --- aggregates for the metrics endpoint ---------------------------------------
    # Deliberately not org-scoped and deliberately not labelled by org: these are scraped
    # every few seconds, and an unbounded label is how a metrics endpoint becomes the
    # thing that falls over. Per-organization detail belongs in the operator API.

    def trigger_queue_summary(self) -> tuple[int, str | None]:
        """How much triggered work is waiting, and how long the oldest has waited."""
        with self.reading() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS depth, MIN(created_at) AS oldest FROM trigger_intake "
                "WHERE status='queued'").fetchone()
            return int(row["depth"]), row["oldest"]

    def soonest_authorization_expiry(self) -> str | None:
        """When the first enabled connector loses its authorization, across every org.

        Only enabled ones: an expiry on a connector nobody uses is not an operational
        event, and alerting on it teaches people to ignore the alert.
        """
        with self.reading() as connection:
            row = connection.execute(
                "SELECT MIN(a.expires_at) AS soonest FROM connector_authorizations a "
                "JOIN connector_registrations r ON r.org_id=a.org_id AND r.id=a.connector_id "
                "WHERE a.status='authorized' AND r.enabled=1").fetchone()
            return row["soonest"]

    def in_flight_receipt_summary(self) -> tuple[int, str | None]:
        """Calls that left this host and were never resolved, and the age of the oldest."""
        with self.reading() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, MIN(created_at) AS oldest FROM connector_receipts "
                "WHERE status='in_flight'").fetchone()
            return int(row["count"]), row["oldest"]

    def in_flight_connector_receipts(self, org_id: str) -> list[dict]:
        """Calls that left this host and were never resolved. Nothing may retry these."""
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,connector_id,idempotency_key,outcome_note,created_at FROM connector_receipts "
                "WHERE org_id=? AND status='in_flight' ORDER BY created_at", (org_id,))]

    def all_in_flight_receipts(self, limit: int = 100) -> list[dict]:
        """Every organization's unresolved calls, for the escalation sweep.

        A call that left and never settled is the sharpest thing in this company that can
        need a person: nothing may retry it, because nobody knows whether it happened.
        """
        with self.reading() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id,org_id,connector_id,idempotency_key,outcome_note,created_at "
                "FROM connector_receipts WHERE status='in_flight' ORDER BY created_at "
                "LIMIT ?", (int(limit),))]

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
