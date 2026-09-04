from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from runtime.api import create_server
from runtime.auth import AuthError, Principal, TokenService
from runtime.connectors import ConnectorError, FixtureConnectorGateway, WebhookVerifier, action_digest, validate_manifest
from runtime.db import Conflict, NotFound, Store, StoreError, restore_backup
from runtime.service import Forbidden, MyOrgService, ServiceError

ROOT = Path(__file__).resolve().parents[1]

SECRET = "0123456789abcdef0123456789abcdef"
REVISION = "a" * 64
PAYLOAD_HASH = hashlib.sha256(b"approved fixture content").hexdigest()


def manifest(**overrides):
    value = {
        "id": "fixture-outbound", "kind": "fixture", "mode": "propose_write",
        "base_url": "https://fixture.invalid", "allowed_hosts": ["fixture.invalid"],
        "allowed_actions": ["external_send"], "secret_ref": None,
        "timeout_seconds": 3, "max_response_bytes": 65536, "enabled": True,
    }
    value.update(overrides)
    return value


class Foundation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "myorg.db")
        # Every migration on disk, applied in order -- checked against the directory rather
        # than a hard-coded list, so adding one is a schema decision and not a test edit.
        expected = sorted(int(p.name.split("_", 1)[0])
                          for p in (ROOT / "runtime" / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
        self.assertEqual(self.store.migrate(), expected)
        self.store.bootstrap_organization("acme", "Acme")
        self.store.bootstrap_organization("other", "Other")
        self.store.upsert_actor("acme", "maker-agent", "agent", "Maker", ["maker", "chief-of-staff"])
        self.store.upsert_actor("acme", "human-owner", "human", "Owner", ["decision-owner", "auditor"])
        self.store.upsert_actor("acme", "gateway-agent", "service", "Gateway", ["connector-gateway"])
        self.store.upsert_actor("acme", "viewer", "human", "Viewer", ["viewer"])
        self.store.upsert_actor("other", "other-owner", "human", "Other", ["system-admin"])
        self.store.register_connector("acme", validate_manifest(manifest()))
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def principal(self, actor: str) -> Principal:
        return self.tokens.verify(self.tokens.issue("acme", actor))

    def create_test_run(self, run_id="run-one"):
        return self.service.create_run(self.principal("maker-agent"), {
            "id": run_id, "workflow_id": "maker-checker", "workflow_revision": REVISION,
            "goal": "Send an approved fixture message", "data_class": "internal",
        }, f"request-{run_id}")[0]

    def approval(self, run_id="run-one"):
        if run_id == "run-one":
            self.create_test_run(run_id)
        result = self.service.request_approval(self.principal("maker-agent"), {
            "run_id": run_id, "connector_id": "fixture-outbound", "action": "external_send",
            "target_ref": "customers/example", "payload_ref": "artifacts/message.md",
            "payload_sha256": PAYLOAD_HASH,
        }, f"approval-request-{run_id}")
        return result

    def test_a_proposed_outward_call_can_actually_be_found(self):
        """The gate was creatable and decidable with nothing in between.

        No route and no store query ever reported that an approval was waiting, so a person
        could only answer one by already knowing its id and its 64-character action hash.
        The strictest gate in the company was, in practice, unanswerable.
        """
        approval = self.approval()
        waiting = self.service.pending_connector_approvals(self.principal("human-owner"))
        self.assertEqual([item["approval_id"] for item in waiting], [approval["id"]])
        item = waiting[0]
        self.assertEqual(item["action_hash"], approval["action_hash"],
                         "the hash the decision is checked against must be readable")
        self.assertFalse(item["expired"])
        for field in ("action", "target_ref", "payload_ref", "requested_by", "expires_at"):
            self.assertTrue(item[field], f"{field} is what the decision is taken on")

        self.service.decide_approval(
            self.principal("human-owner"), approval["id"],
            {"decision": "approve", "action_hash": approval["action_hash"],
             "reason": "checked the payload"}, "seen-decision")
        self.assertEqual(self.service.pending_connector_approvals(self.principal("human-owner")),
                         [], "a decided call stops waiting")

    def test_a_person_has_longer_than_a_coffee_break_to_decide(self):
        """Fifteen minutes was a machine's deadline on a human's decision. The whole premise
        is that a person answers when they get to it, and there is a notification path
        precisely because nobody watches the screen. The approval binds one exact payload by
        hash, so a longer window lets nothing else through."""
        from datetime import datetime, timedelta, timezone
        from runtime.service_connectors import APPROVAL_WINDOW_HOURS
        approval = self.approval()
        expires = datetime.fromisoformat(approval["expires_at"].replace("Z", "+00:00"))
        self.assertGreater(expires, datetime.now(timezone.utc) + timedelta(hours=1),
                           "a person must be able to answer this after a meeting")
        self.assertEqual(APPROVAL_WINDOW_HOURS, 24)

    def test_the_gate_that_actually_sends_records_why(self):
        """Every other human decision here carries a reason. This was the one that did not,
        and it is the one that puts something outside the building."""
        approval = self.approval()
        for body in ({"decision": "approve", "action_hash": approval["action_hash"]},
                     {"decision": "approve", "action_hash": approval["action_hash"],
                      "reason": ""},
                     {"decision": "approve", "action_hash": approval["action_hash"],
                      "reason": "x" * 201}):
            with self.assertRaises(ServiceError, msg=str(body)):
                self.service.decide_approval(self.principal("human-owner"), approval["id"],
                                             body, "no-reason")
        self.service.decide_approval(
            self.principal("human-owner"), approval["id"],
            {"decision": "approve", "action_hash": approval["action_hash"],
             "reason": "the customer asked for this in writing"}, "with-reason")
        events = self.store.run_events("acme", approval["run_id"])
        decided = [e for e in events if e["event_type"] == "approval.approved"]
        self.assertEqual(len(decided), 1)
        self.assertIn("the customer asked for this in writing", json.dumps(decided[-1]))

    def test_roles_are_bound_to_database_not_token(self):
        token = self.tokens.issue("acme", "viewer", now_epoch=100)
        self.store.upsert_actor("acme", "viewer", "human", "Viewer", ["system-admin"])
        verified = self.tokens.verify(token, now_epoch=101)
        self.assertEqual(verified.roles, ("system-admin",))

    def test_token_tamper_expiry_revocation_and_secret_length(self):
        token = self.tokens.issue("acme", "viewer", ttl_seconds=10, now_epoch=100)
        with self.assertRaises(AuthError):
            self.tokens.verify(token[:-1] + ("a" if token[-1] != "a" else "b"), now_epoch=101)
        with self.assertRaises(AuthError):
            self.tokens.verify(token, now_epoch=111)
        current = self.tokens.issue("acme", "viewer")
        self.tokens.revoke(current)
        with self.assertRaises(AuthError):
            self.tokens.verify(current)
        with self.assertRaises(AuthError):
            TokenService(self.store, "too-short")

    def test_actor_disable_and_organization_suspension_invalidate_identity(self):
        actor_token = self.tokens.issue("acme", "viewer")
        self.store.set_actor_status("acme", "viewer", "disabled")
        with self.assertRaises(AuthError):
            self.tokens.verify(actor_token)
        self.store.set_actor_status("acme", "viewer", "active")
        organization_token = self.tokens.issue("acme", "viewer")
        self.store.set_organization_status("acme", "suspended")
        with self.assertRaises(AuthError):
            self.tokens.verify(organization_token)

    def test_tenant_isolation_and_run_idempotency(self):
        run = self.create_test_run()
        same, created = self.service.create_run(self.principal("maker-agent"), {
            "id": "run-one", "workflow_id": "maker-checker", "workflow_revision": REVISION,
            "goal": "Send an approved fixture message", "data_class": "internal",
        }, "request-run-one")
        self.assertFalse(created)
        self.assertEqual(run["id"], same["id"])
        with self.assertRaises(Conflict):
            self.service.create_run(self.principal("maker-agent"), {
                "id": "run-two", "workflow_id": "maker-checker", "workflow_revision": REVISION,
                "goal": "Different", "data_class": "internal",
            }, "request-run-one")
        with self.assertRaises(NotFound):
            self.store.run("other", "run-one")

    def test_raw_sensitive_data_and_unauthorized_role_are_denied(self):
        with self.assertRaises(ServiceError):
            self.service.create_run(self.principal("maker-agent"), {
                "id": "run-secret", "workflow_id": "maker-checker", "workflow_revision": REVISION,
                "goal": "secret", "data_class": "restricted",
            }, "request-secret")
        with self.assertRaises(Forbidden):
            self.service.create_run(self.principal("viewer"), {
                "id": "run-viewer", "workflow_id": "maker-checker", "workflow_revision": REVISION,
                "goal": "No authority", "data_class": "internal",
            }, "request-viewer")

    def test_maker_checker_exact_hash_human_distinct_and_single_use(self):
        approval = self.approval()
        exact = approval["action_hash"]
        with self.assertRaises(Forbidden):
            self.service.decide_approval(self.principal("maker-agent"), approval["id"],
                                         {"decision": "approve", "action_hash": exact, "reason": "checked the payload"}, "bad-agent-decision")
        self.store.upsert_actor("acme", "maker-human", "human", "Maker Human", ["maker", "decision-owner"])
        self.create_test_run("run-human")
        human_approval = self.service.request_approval(self.principal("maker-human"), {
            "run_id": "run-human", "connector_id": "fixture-outbound", "action": "external_send",
            "target_ref": "customers/example", "payload_ref": "artifacts/message.md", "payload_sha256": PAYLOAD_HASH,
        }, "approval-human")
        with self.assertRaises(Conflict):
            self.service.decide_approval(self.principal("maker-human"), human_approval["id"],
                                         {"decision": "approve", "action_hash": human_approval["action_hash"], "reason": "checked the payload"}, "self-decision")
        with self.assertRaises(Conflict):
            self.service.decide_approval(self.principal("human-owner"), approval["id"],
                                         {"decision": "approve", "action_hash": "b" * 64, "reason": "checked the payload"}, "wrong-hash")
        decided = self.service.decide_approval(self.principal("human-owner"), approval["id"],
                                               {"decision": "approve", "action_hash": exact, "reason": "checked the payload"}, "owner-decision")
        self.assertEqual(decided["status"], "approved")
        body = {"connector_id": "fixture-outbound", "action": "external_send",
                "target_ref": "customers/example", "payload_ref": "artifacts/message.md",
                "payload_sha256": PAYLOAD_HASH, "approval_id": approval["id"]}
        receipt, created = self.service.execute_fixture(self.principal("gateway-agent"), body, "effect-key-one")
        self.assertTrue(created)
        same, created = self.service.execute_fixture(self.principal("gateway-agent"), body, "effect-key-one")
        self.assertFalse(created)
        self.assertEqual(receipt["id"], same["id"])
        with self.assertRaises(Conflict):
            self.service.execute_fixture(self.principal("gateway-agent"), {**body, "payload_sha256": "b" * 64}, "effect-key-one")
        with self.assertRaises(Conflict):
            self.service.execute_fixture(self.principal("gateway-agent"), body, "effect-key-two")

    def test_red_action_and_disabled_connector_are_denied(self):
        self.create_test_run()
        with self.assertRaises(ServiceError):
            self.service.request_approval(self.principal("maker-agent"), {
                "run_id": "run-one", "connector_id": "fixture-outbound", "action": "move_money",
                "target_ref": "bank/example", "payload_ref": "artifacts/instruction", "payload_sha256": PAYLOAD_HASH,
            }, "red-request")
        self.store.register_connector("acme", validate_manifest(manifest(enabled=False)))
        with self.assertRaises(ServiceError):
            self.service.request_approval(self.principal("maker-agent"), {
                "run_id": "run-one", "connector_id": "fixture-outbound", "action": "external_send",
                "target_ref": "customers/example", "payload_ref": "artifacts/message", "payload_sha256": PAYLOAD_HASH,
            }, "disabled-request")

    def test_connector_manifest_ssrf_and_secret_controls(self):
        with self.assertRaises(ConnectorError):
            validate_manifest(manifest(base_url="http://fixture.invalid"))
        with self.assertRaises(ConnectorError):
            validate_manifest(manifest(base_url="https://127.0.0.1", allowed_hosts=["127.0.0.1"]))
        with self.assertRaises(ConnectorError):
            validate_manifest(manifest(secret_ref="actual-secret-value"))
        with self.assertRaises(ConnectorError):
            validate_manifest({**manifest(), "unknown": True})

    def test_webhook_tamper_staleness_and_replay(self):
        verifier = WebhookVerifier(self.store)
        secret = b"z" * 32
        timestamp = "2000000000"
        nonce = "nonce-abcdefghijklmnop"
        body = b'{"event":"accepted"}'
        message = timestamp.encode() + b"." + nonce.encode() + b"." + body
        signature = "v1=" + hmac.new(secret, message, hashlib.sha256).hexdigest()
        verifier.verify("acme", "fixture-outbound", secret, timestamp, nonce, body, signature, 2000000000)
        with self.assertRaises(Conflict):
            verifier.verify("acme", "fixture-outbound", secret, timestamp, nonce, body, signature, 2000000000)
        with self.assertRaises(ConnectorError):
            verifier.verify("acme", "fixture-outbound", secret, timestamp, "nonce-another-123456", body + b"x", signature, 2000000000)
        with self.assertRaises(ConnectorError):
            verifier.verify("acme", "fixture-outbound", secret, timestamp, "nonce-another-123456", body, signature, 2000000400)

    def test_event_tamper_is_detected(self):
        self.create_test_run()
        self.assertEqual(self.store.verify()["events"], 1)
        connection = sqlite3.connect(self.store.path)
        connection.execute("UPDATE events SET payload_json='{}' WHERE org_id='acme' AND run_id='run-one'")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError):
            self.store.verify()

    def test_backup_restore_and_checksum_rejection(self):
        self.create_test_run()
        backup = self.root / "backups" / "myorg.db"
        record = self.store.backup(backup)
        self.assertEqual(record["verification"]["integrity"], "ok")
        self.create_test_run("run-two")
        restored = restore_backup(backup, self.store.path)
        self.assertTrue(restored["pre_restore"])
        with self.assertRaises(NotFound):
            self.store.run("acme", "run-two")
        backup.write_bytes(backup.read_bytes() + b"tamper")
        with self.assertRaises(StoreError):
            restore_backup(backup, self.store.path)


class APIFoundation(Foundation):
    def setUp(self):
        super().setUp()
        self.server = create_server("127.0.0.1", 0, self.store.path, SECRET, {"https://control.example"})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, path, method="GET", actor=None, body=None, headers=None):
        final_headers = dict(headers or {})
        if actor:
            final_headers["Authorization"] = "Bearer " + self.tokens.issue("acme", actor)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            final_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=final_headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        payload = json.loads(response.read()) if response.length != 0 else None
        return response.status, dict(response.headers), payload

    def test_health_security_headers_and_auth(self):
        status, headers, body = self.request("/healthz")
        self.assertEqual((status, body["status"]), (200, "ok"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, _, body = self.request("/v1/me")
        self.assertEqual((status, body["error"]["code"]), (401, "unauthorized"))
        status, _, body = self.request("/v1/me", actor="viewer")
        self.assertEqual((status, body["actor_id"]), (200, "viewer"))

    def test_api_role_tenant_cors_and_idempotency(self):
        body = {"id": "api-run", "workflow_id": "maker-checker", "workflow_revision": REVISION,
                "goal": "API acceptance", "data_class": "internal"}
        status, headers, created = self.request("/v1/runs", "POST", "maker-agent", body,
                                                {"X-Request-Id": "api-request-one", "Origin": "https://control.example"})
        self.assertEqual(status, 201)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://control.example")
        status, _, same = self.request("/v1/runs", "POST", "maker-agent", body,
                                       {"X-Request-Id": "api-request-one"})
        self.assertEqual((status, same["id"]), (200, created["id"]))
        status, _, result = self.request("/v1/runs", "POST", "viewer", {**body, "id": "viewer-run"},
                                         {"X-Request-Id": "api-viewer-request"})
        self.assertEqual((status, result["error"]["code"]), (403, "forbidden"))
        other_token = self.tokens.issue("other", "other-owner")
        request = urllib.request.Request(self.base + "/v1/runs/api-run", headers={"Authorization": "Bearer " + other_token})
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 404)
            error.close()
        else:
            self.fail("cross-organization lookup unexpectedly succeeded")
        status, headers, _ = self.request("/healthz", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_api_input_limits_and_route_hiding(self):
        status, _, result = self.request("/v1/runs", "POST", "maker-agent", {}, {"X-Request-Id": "short"})
        self.assertEqual(status, 400)
        status, _, result = self.request("/v1/missing", actor="viewer")
        self.assertEqual((status, result["error"]["code"]), (404, "not_found"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
