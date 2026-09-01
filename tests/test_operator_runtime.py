from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.api import create_server
from runtime.auth import AuthError, TokenService
from runtime.db import Conflict, MIGRATIONS, Store, StoreError, utc_now
from runtime.gateway_auth import GatewayAuthenticator
from runtime.service import MyOrgService, ServiceError

SECRET = "0123456789abcdef0123456789abcdef"
GATEWAY_SECRET = "gateway-0123456789abcdef0123456789abcdef"
DOCUMENTS = {
    "problem_statement": True, "charter": True, "sop": True,
    "control_plan": True, "uat": True, "release_checklist": True,
}


def state(revision: int, view: str = "intake") -> dict:
    return {"schema_version": 1, "active_view": view, "time_range": "90d",
            "filters": {"queue": "attention", "flow": "current"},
            "sort": {"queue": "updated_asc"}, "scroll_position": 412,
            "current_project_id": None, "revision": revision}


def project(status: str = "ready") -> dict:
    return {"title": "Customer renewal", "sponsor": "Sponsor", "decision_owner": "Owner",
            "affected_user": "Customer", "desired_outcome": "Reduce avoidable renewal wait time",
            "documents": dict(DOCUMENTS), "status": status}


class OperatorRuntime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "myorg.db"
        self.store = Store(self.path)
        self.assertEqual(self.store.migrate(), [1, 2, 3, 4])
        for org in ("acme", "other"):
            self.store.bootstrap_organization(org, org.title())
        self.store.upsert_actor("acme", "operator-one", "human", "Operator One", ["maker"])
        self.store.upsert_actor("acme", "operator-two", "human", "Operator Two", ["viewer"])
        self.store.upsert_actor("acme", "security-admin", "human", "Security Admin", ["system-admin", "auditor"])
        self.store.upsert_actor("other", "operator-one", "human", "Other Operator", ["maker"])
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def principal(self, org: str = "acme", actor: str = "operator-one"):
        return self.tokens.verify(self.tokens.issue(org, actor))

    def test_ui_state_survives_restart_is_isolated_and_resets(self):
        saved = self.service.save_ui_state(self.principal(), state(0), "ui-save-one", "trace-ui-one")
        self.assertEqual((saved["revision"], saved["active_view"]), (1, "intake"))
        restarted = Store(self.path)
        self.assertEqual(restarted.migrate(), [])
        self.assertEqual(restarted.ui_state("acme", "operator-one")["scroll_position"], 412)
        self.assertEqual(restarted.ui_state("acme", "operator-two")["revision"], 0)
        self.assertEqual(restarted.ui_state("other", "operator-one")["revision"], 0)
        with self.assertRaises(Conflict):
            self.service.save_ui_state(self.principal(), state(0, "queue"), "ui-stale-one", "trace-ui-two")
        reset = self.service.reset_ui_state(self.principal(), "ui-reset-one", "trace-ui-three")
        self.assertEqual((reset["revision"], reset["active_view"], reset["time_range"]), (0, "overview", "30d"))

    def test_ui_state_concurrent_updates_have_one_winner(self):
        self.service.save_ui_state(self.principal(), state(0), "ui-initial-one", "trace-concurrent")
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def update(view: str, request_id: str) -> None:
            try:
                barrier.wait()
                self.service.save_ui_state(self.principal(), state(1, view), request_id, request_id)
                outcomes.append("saved")
            except Conflict:
                outcomes.append("conflict")

        workers = [threading.Thread(target=update, args=("queue", "concurrent-one")),
                   threading.Thread(target=update, args=("flow", "concurrent-two"))]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertCountEqual(outcomes, ["saved", "conflict"])
        self.assertEqual(self.store.ui_state("acme", "operator-one")["revision"], 2)

    def test_project_intake_is_durable_idempotent_and_governed(self):
        principal = self.principal()
        created, was_created = self.service.create_project(
            principal, project(), "project-create-one", "trace-project-one")
        self.assertTrue(was_created)
        same, was_created = self.store.create_project_intake(
            "acme", "operator-one", "ignored-project", project(), "project-create-one", "trace-project-two")
        self.assertFalse(was_created)
        self.assertEqual(same["id"], created["id"])
        updated = self.service.update_project(
            principal, created["id"], {**project(), "title": "Renewal evidence", "revision": 1},
            "project-update-one", "trace-project-three")
        self.assertEqual((updated["revision"], updated["title"]), (2, "Renewal evidence"))
        with self.assertRaises(Conflict):
            self.service.update_project(principal, created["id"], {**project(), "revision": 1},
                                        "project-update-two", "trace-project-four")
        incomplete = project("ready")
        incomplete["documents"]["uat"] = False
        with self.assertRaises(ServiceError):
            self.service.create_project(principal, incomplete, "project-invalid-one", "trace-project-five")
        with closing(sqlite3.connect(self.path)) as connection:
            audit_metadata = " ".join(row[0] for row in connection.execute(
                "SELECT metadata_json FROM operational_events WHERE org_id='acme'"))
        self.assertNotIn("Reduce avoidable renewal wait time", audit_metadata)
        self.assertNotIn("Customer renewal", audit_metadata)

    def test_gateway_binding_signature_replay_and_role_refresh(self):
        self.store.bind_identity("chatgpt-sites", "operator@example.com", "acme", "operator-two")
        gateway = GatewayAuthenticator(self.store, GATEWAY_SECRET)
        timestamp = "2000000000"
        nonce = "nonce_abcdefghijklmnop"
        headers = {"X-MyOrg-Gateway-Issuer": "chatgpt-sites",
                   "X-MyOrg-Gateway-Subject": "operator@example.com",
                   "X-MyOrg-Gateway-Audience": "myorg-api", "X-MyOrg-Gateway-Timestamp": timestamp,
                   "X-MyOrg-Gateway-Nonce": nonce}
        message = gateway.message("GET", "/v1/me", timestamp, nonce, "chatgpt-sites",
                                  "operator@example.com", "myorg-api", b"")
        headers["X-MyOrg-Gateway-Signature"] = "v1=" + hmac.new(
            GATEWAY_SECRET.encode(), message, hashlib.sha256).hexdigest()
        principal = gateway.verify("GET", "/v1/me", b"", headers, now_epoch=2_000_000_000)
        self.assertEqual((principal.org_id, principal.roles), ("acme", ("viewer",)))
        with self.assertRaises(AuthError):
            gateway.verify("GET", "/v1/me", b"", headers, now_epoch=2_000_000_000)
        self.store.upsert_actor("acme", "operator-two", "human", "Operator Two", ["maker"])
        headers["X-MyOrg-Gateway-Nonce"] = "nonce_abcdefghijklmnop2"
        message = gateway.message("GET", "/v1/me", timestamp, headers["X-MyOrg-Gateway-Nonce"],
                                  "chatgpt-sites", "operator@example.com", "myorg-api", b"")
        headers["X-MyOrg-Gateway-Signature"] = "v1=" + hmac.new(
            GATEWAY_SECRET.encode(), message, hashlib.sha256).hexdigest()
        self.assertEqual(gateway.verify("GET", "/v1/me", b"", headers, 2_000_000_000).roles, ("maker",))

    def test_live_connector_requires_human_authorization_kill_switch_and_reconciliation(self):
        connector = {"id": "provider-one", "kind": "oauth", "mode": "propose_write",
                     "base_url": "https://api.provider.example", "allowed_hosts": ["api.provider.example"],
                     "allowed_actions": ["publish"], "secret_ref": "PROVIDER_ONE_TOKEN",
                     "timeout_seconds": 5, "max_response_bytes": 65536, "enabled": False}
        self.store.register_connector("acme", connector)
        admin = self.principal(actor="security-admin")
        with self.assertRaises(Conflict):
            self.service.set_connector_enabled(admin, "provider-one", {"enabled": True},
                                               "connector-enable-one", "trace-connector-one")
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
        authorization = self.service.authorize_connector(admin, "provider-one", {
            "provider_account_ref": "accounts/example", "scopes": ["records.read", "records.write"],
            "token_secret_ref": "PROVIDER_ONE_TOKEN", "refresh_secret_ref": "PROVIDER_ONE_REFRESH",
            "expires_at": expiry,
        }, "connector-auth-one", "trace-connector-two")
        self.assertEqual(authorization["status"], "authorized")
        self.assertNotIn("token_secret_ref", authorization)
        enabled = self.service.set_connector_enabled(admin, "provider-one", {"enabled": True},
                                                     "connector-enable-two", "trace-connector-three")
        self.assertTrue(enabled["enabled"])
        revoked = self.service.revoke_connector_authorization(
            admin, "provider-one", "connector-revoke-one", "trace-connector-four")
        self.assertEqual(revoked["status"], "revoked")
        self.assertFalse(self.store.connector("acme", "provider-one")["enabled"])

        fixture = {**connector, "id": "fixture-control", "kind": "fixture", "base_url": "https://fixture.invalid",
                   "allowed_hosts": ["fixture.invalid"], "secret_ref": None, "enabled": True}
        self.store.register_connector("acme", fixture)
        self.store.record_connector_receipt("acme", {"id": "receipt-control-one", "connector_id": "fixture-control",
                                            "idempotency_key": "receipt-idempotency-one", "request_hash": "a" * 64,
                                            "provider_receipt": "fixture:one", "status": "accepted", "created_at": utc_now()})
        self.assertEqual(len(self.store.unreconciled_connector_receipts("acme")), 1)
        reconciled = self.service.reconcile_connector_receipt(admin, "receipt-control-one",
                                                              {"provider_status": "confirmed", "details_sha256": "b" * 64},
                                                              "receipt-reconcile-one", "trace-connector-five")
        self.assertEqual(reconciled["provider_status"], "confirmed")
        self.assertEqual(self.store.unreconciled_connector_receipts("acme"), [])

    def test_operational_tamper_and_v1_upgrade_are_verified(self):
        self.service.save_ui_state(self.principal(), state(0), "tamper-save-one", "trace-tamper")
        self.assertEqual(self.store.verify()["operational_events"], 1)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE operational_events SET metadata_json='{}' WHERE org_id='acme'")
            connection.commit()
        with self.assertRaises(StoreError):
            self.store.verify()

        upgrade_path = Path(self.temporary.name) / "upgrade.db"
        migration = MIGRATIONS / "001_production_foundation.sql"
        with closing(sqlite3.connect(upgrade_path)) as connection:
            connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migrations VALUES(?,?,?,?)",
                               (1, migration.name, hashlib.sha256(migration.read_bytes()).hexdigest(), utc_now()))
            connection.commit()
        self.assertEqual(Store(upgrade_path).migrate(), [2, 3, 4])
        self.assertEqual(Store(upgrade_path).verify()["migrations"], 4)


class GatewayAPI(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "api.db"
        store = Store(self.path)
        store.migrate()
        store.bootstrap_organization("acme", "Acme")
        store.upsert_actor("acme", "operator", "human", "Operator", ["maker"])
        store.bind_identity("chatgpt-sites", "operator@example.com", "acme", "operator")
        self.server = create_server("127.0.0.1", 0, self.path, SECRET, gateway_secret=GATEWAY_SECRET,
                                    metrics_token="metrics-secret-value")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def signed_headers(self, method: str, path: str, body: bytes = b"") -> dict[str, str]:
        timestamp = str(int(__import__("time").time()))
        nonce = "nonce_" + hashlib.sha256(timestamp.encode() + path.encode()).hexdigest()[:24]
        message = GatewayAuthenticator.message(method, path, timestamp, nonce, "chatgpt-sites",
                                               "operator@example.com", "myorg-api", body)
        return {"X-MyOrg-Gateway-Issuer": "chatgpt-sites", "X-MyOrg-Gateway-Subject": "operator@example.com",
                "X-MyOrg-Gateway-Audience": "myorg-api", "X-MyOrg-Gateway-Timestamp": timestamp,
                "X-MyOrg-Gateway-Nonce": nonce,
                "X-MyOrg-Gateway-Signature": "v1=" + hmac.new(
                    GATEWAY_SECRET.encode(), message, hashlib.sha256).hexdigest()}

    def test_signed_api_identity_and_protected_metrics(self):
        request = urllib.request.Request(self.base + "/v1/me", headers=self.signed_headers("GET", "/v1/me"))
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
            self.assertEqual((response.status, payload["actor_id"], payload["roles"]), (200, "operator", ["maker"]))
            self.assertTrue(response.headers["X-Trace-Id"])
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(self.base + "/metrics", timeout=3)
        self.assertEqual(denied.exception.code, 404)
        request = urllib.request.Request(self.base + "/metrics",
                                         headers={"Authorization": "Bearer metrics-secret-value"})
        with urllib.request.urlopen(request, timeout=3) as response:
            metrics = response.read().decode()
        self.assertIn('myorg_http_requests_total{method="GET",status="200"}', metrics)
        self.assertNotIn("operator@example.com", metrics)


if __name__ == "__main__":
    unittest.main(verbosity=2)
