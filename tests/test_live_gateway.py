"""Touching a real external system, and telling the truth about what happened.

TOOL-03/04 in the REV2 audit: the connector control plane was complete -- admission,
authorization, exact approval, receipts, reconciliation -- but the only adapter behind it
was a fixture, so the company still had no hands.

The hard part of a live adapter is not the HTTP call. It is the third outcome. A fixture
either works or raises; a real provider can take your bytes and never answer, and a system
that records that as "failed" will retry and charge the customer twice. Every test below
exists to pin one of those unknowns to a state a human can act on.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from runtime import audit, live_gateway
from runtime.connectors import ConnectorError, action_digest, validate_manifest
from runtime.db import Conflict, Store, digest
from runtime.live_gateway import ACCEPTED, FAILED, IN_FLIGHT, GatewayUnavailable, LiveConnectorGateway

PAYLOAD = {"amount": 100, "note": "invoice reminder"}
PAYLOAD_BYTES = b'{"amount":100,"note":"invoice reminder"}'
PAYLOAD_SHA = live_gateway.sha256_bytes(PAYLOAD_BYTES)
SECRET_REF = "MYORG_TEST_PROVIDER_TOKEN"
SECRET_VALUE = "a-secret-long-enough-to-pass"


class FakeTransport:
    """Stands in for the provider. Records every send, so "did it go out?" is answerable."""

    def __init__(self, *results):
        self.results = list(results)
        self.sends: list[dict] = []

    def send(self, base_url, action, body, secret, timeout_seconds, max_response_bytes):
        self.sends.append({"base_url": base_url, "action": action, "body": body,
                           "secret": secret, "timeout": timeout_seconds})
        outcome = self.results.pop(0) if self.results else {"status": 200, "body": b"{}", "truncated": False}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class LiveGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self._previous = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", SECRET_REF)}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self.temporary.name
        self.audit_log = Path(self.temporary.name) / "_audit-log.jsonl"
        os.environ["MYORG_AUDIT_LOG"] = str(self.audit_log)
        os.environ[SECRET_REF] = SECRET_VALUE

        self.store = Store(Path(self.temporary.name) / "myorg.db")
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.store.upsert_actor("acme", "maker", "human", "Maker", ["maker"])
        self.store.upsert_actor("acme", "chief", "human", "Chief", ["decision-owner"])
        self.store.upsert_actor("acme", "gateway", "service", "Gateway", ["connector-gateway"])
        self.store.create_run("acme", "run-1", "billing-flow", "0" * 64, "Chase an invoice",
                              "internal", "maker", "create-run-1")
        self.manifest = validate_manifest({
            "id": "billing", "kind": "http", "mode": "propose_write",
            "base_url": "https://api.example.com/v2", "allowed_hosts": ["api.example.com"],
            "allowed_actions": ["send_email"], "secret_ref": SECRET_REF,
            "timeout_seconds": 5, "max_response_bytes": 4096, "enabled": False,
        })
        self.store.register_connector("acme", self.manifest)
        self.store.authorize_connector("acme", "billing", "acct-1", ["write"], SECRET_REF, None,
                                       "2027-01-01T00:00:00Z", "chief", "auth-request-1", "trace-1")
        self.store.set_connector_enabled("acme", "billing", True, "chief", "enable-request-1", "trace-1")

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def approval(self, target: str = "customer/42") -> tuple[str, str]:
        """A real, human-decided approval for the exact action about to be taken."""
        exact = action_digest("billing", "send_email", target, "payload/1", PAYLOAD_SHA)
        approval_id = f"approval-{digest({'t': target})[:12]}"
        self.store.create_approval("acme", approval_id, "run-1", "send_email", exact, target,
                                   "payload/1", PAYLOAD_SHA, "maker", "2099-01-01T00:00:00Z",
                                   f"req-{approval_id}")
        self.store.decide_approval("acme", approval_id, "chief", exact, "approve", f"dec-{approval_id}")
        return approval_id, target

    def execute(self, transport, approval_id, target, key="idem-key-1", payload=None):
        gateway = LiveConnectorGateway(self.store, transport)
        return gateway.execute("acme", "billing", "send_email", target, "payload/1", PAYLOAD_SHA,
                               approval_id, "gateway", key, PAYLOAD if payload is None else payload)

    # --- the provider answered ----------------------------------------------------------

    def test_an_accepted_call_settles_accepted_and_consumes_the_approval(self) -> None:
        approval_id, target = self.approval()
        transport = FakeTransport({"status": 202, "body": b'{"id":"m1"}', "truncated": False})
        receipt, created = self.execute(transport, approval_id, target)
        self.assertTrue(created)
        self.assertEqual(receipt["status"], ACCEPTED)
        self.assertTrue(receipt["settled_at"])
        self.assertEqual(len(transport.sends), 1)
        self.assertEqual(self.store.approval("acme", approval_id)["status"], "consumed")

    def test_a_rejected_call_settles_failed_and_says_why(self) -> None:
        approval_id, target = self.approval()
        receipt, _ = self.execute(FakeTransport({"status": 422, "body": b"{}", "truncated": False}),
                                  approval_id, target)
        self.assertEqual(receipt["status"], FAILED)
        self.assertIn("422", receipt["outcome_note"])

    def test_the_secret_is_sent_but_never_stored(self) -> None:
        approval_id, target = self.approval()
        transport = FakeTransport()
        receipt, _ = self.execute(transport, approval_id, target)
        self.assertEqual(transport.sends[0]["secret"], SECRET_VALUE)
        self.assertNotIn(SECRET_VALUE, str(dict(receipt)))
        self.assertNotIn(SECRET_VALUE, self.audit_log.read_text(encoding="utf-8"))

    # --- the provider did not answer ----------------------------------------------------

    def test_a_server_error_stays_unresolved_rather_than_claiming_failure(self) -> None:
        """A 500 means the request definitely arrived. Whether it took effect is unknown,
        and calling that 'failed' is what makes a retry send it twice."""
        approval_id, target = self.approval()
        receipt, _ = self.execute(FakeTransport({"status": 503, "body": b"", "truncated": False}),
                                  approval_id, target)
        self.assertEqual(receipt["status"], IN_FLIGHT)
        self.assertIsNone(receipt["settled_at"])
        self.assertEqual([r["id"] for r in self.store.in_flight_connector_receipts("acme")], [receipt["id"]])

    def test_no_response_read_stays_unresolved(self) -> None:
        approval_id, target = self.approval()
        receipt, _ = self.execute(
            FakeTransport({"status": None, "body": b"", "truncated": False, "note": "timeout"}),
            approval_id, target)
        self.assertEqual(receipt["status"], IN_FLIGHT)

    def test_retrying_an_unresolved_call_refuses_instead_of_sending_again(self) -> None:
        """The whole point. A machine may not decide that an unknown outcome was a failure."""
        approval_id, target = self.approval()
        self.execute(FakeTransport({"status": 500, "body": b"", "truncated": False}), approval_id, target)
        second = FakeTransport({"status": 200, "body": b"{}", "truncated": False})
        with self.assertRaises(Conflict) as caught:
            self.execute(second, approval_id, target)
        self.assertIn("reconcile", str(caught.exception))
        self.assertEqual(second.sends, [], "an unresolved call must not be sent a second time")

    def test_a_call_that_never_left_settles_failed_and_is_safe(self) -> None:
        approval_id, target = self.approval()
        with self.assertRaises(GatewayUnavailable):
            self.execute(FakeTransport(GatewayUnavailable("connection refused")), approval_id, target)
        receipt = self.store.connector_receipt("acme", "billing", "idem-key-1")
        self.assertEqual(receipt["status"], FAILED)
        self.assertEqual(self.store.in_flight_connector_receipts("acme"), [])

    # --- idempotency --------------------------------------------------------------------

    def test_replaying_a_settled_call_returns_the_receipt_without_sending(self) -> None:
        approval_id, target = self.approval()
        first, _ = self.execute(FakeTransport(), approval_id, target)
        second_transport = FakeTransport()
        again, created = self.execute(second_transport, approval_id, target)
        self.assertFalse(created)
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(second_transport.sends, [])

    def test_the_same_key_with_a_different_request_is_refused(self) -> None:
        approval_id, target = self.approval()
        self.execute(FakeTransport(), approval_id, target)
        other_id, other_target = self.approval("customer/99")
        with self.assertRaises(Conflict):
            self.execute(FakeTransport(), other_id, other_target, key="idem-key-1")

    def test_a_receipt_cannot_be_settled_twice(self) -> None:
        approval_id, target = self.approval()
        receipt, _ = self.execute(FakeTransport(), approval_id, target)
        with self.assertRaises(Conflict):
            self.store.settle_connector_receipt("acme", receipt["id"], ACCEPTED, "x", "y", "z", "gateway")

    # --- admission ----------------------------------------------------------------------

    def test_a_disabled_connector_cannot_be_called(self) -> None:
        approval_id, target = self.approval()
        self.store.set_connector_enabled("acme", "billing", False, "chief", "disable-1", "trace-1")
        transport = FakeTransport()
        with self.assertRaises(ConnectorError):
            self.execute(transport, approval_id, target)
        self.assertEqual(transport.sends, [])

    def test_a_revoked_authorization_stops_the_call(self) -> None:
        approval_id, target = self.approval()
        self.store.revoke_connector_authorization("acme", "billing", "chief", "revoke-1", "trace-1")
        with self.assertRaises(ConnectorError):
            self.execute(FakeTransport(), approval_id, target)

    def test_an_action_outside_the_allowlist_is_refused(self) -> None:
        approval_id, target = self.approval()
        gateway = LiveConnectorGateway(self.store, FakeTransport())
        with self.assertRaises(ConnectorError):
            gateway.execute("acme", "billing", "delete_account", target, "payload/1", PAYLOAD_SHA,
                            approval_id, "gateway", "idem-key-2", PAYLOAD)

    def test_a_payload_that_does_not_match_its_approved_hash_is_refused(self) -> None:
        approval_id, target = self.approval()
        transport = FakeTransport()
        with self.assertRaises(ConnectorError):
            self.execute(transport, approval_id, target, payload={"amount": 999})
        self.assertEqual(transport.sends, [])

    def test_a_missing_secret_stops_the_call_before_the_approval_is_spent(self) -> None:
        approval_id, target = self.approval()
        os.environ.pop(SECRET_REF)
        with self.assertRaises(ConnectorError):
            self.execute(FakeTransport(), approval_id, target)
        self.assertEqual(self.store.approval("acme", approval_id)["status"], "approved")

    def test_a_fixture_connector_is_not_reachable_through_the_live_gateway(self) -> None:
        self.store.register_connector("acme", validate_manifest({
            "id": "demo", "kind": "fixture", "mode": "propose_write",
            "base_url": "https://fixture.invalid", "allowed_hosts": ["fixture.invalid"],
            "allowed_actions": ["send_email"], "timeout_seconds": 2,
            "max_response_bytes": 1024, "enabled": True}))
        gateway = LiveConnectorGateway(self.store, FakeTransport())
        with self.assertRaises(ConnectorError):
            gateway.execute("acme", "demo", "send_email", "t", "payload/1", PAYLOAD_SHA,
                            "approval-x", "gateway", "idem-key-3", PAYLOAD)

    # --- the audit record ---------------------------------------------------------------

    def test_every_live_call_leaves_a_chained_audit_trail(self) -> None:
        approval_id, target = self.approval()
        self.execute(FakeTransport({"status": 200, "body": b"{}", "truncated": False}), approval_id, target)
        entries = [line for line in self.audit_log.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(entries), 2, "one line when it leaves, one when it settles")
        self.assertEqual(audit.verify(), [])
        self.assertIn('"outcome":"attempted"', entries[0])
        self.assertIn('"outcome":"executed"', entries[1])

    def test_an_unresolved_call_is_audited_as_unresolved_not_as_done(self) -> None:
        approval_id, target = self.approval()
        self.execute(FakeTransport({"status": 500, "body": b"", "truncated": False}), approval_id, target)
        entries = self.audit_log.read_text(encoding="utf-8").splitlines()
        self.assertIn('"outcome":"unresolved"', entries[-1])


class AddressCheckTest(unittest.TestCase):
    """Admission checked the *name* was public. By call time a name can point anywhere."""

    def test_a_name_that_resolves_inside_the_network_is_refused(self) -> None:
        with self.assertRaises(ConnectorError) as caught:
            live_gateway.resolve_global_address("localhost", 443)
        self.assertIn("non-public", str(caught.exception))

    def test_a_name_that_does_not_resolve_is_unavailable_not_a_policy_failure(self) -> None:
        with self.assertRaises(GatewayUnavailable):
            live_gateway.resolve_global_address("no-such-host.invalid", 443)


class ClassificationTest(unittest.TestCase):
    def test_outcomes_map_to_the_honest_state(self) -> None:
        for status, expected in ((200, ACCEPTED), (204, ACCEPTED), (400, FAILED), (404, FAILED),
                                 (409, FAILED), (429, IN_FLIGHT), (500, IN_FLIGHT), (504, IN_FLIGHT)):
            self.assertEqual(live_gateway.classify({"status": status})[0], expected, f"status {status}")
        self.assertEqual(live_gateway.classify({"status": None})[0], IN_FLIGHT)


if __name__ == "__main__":
    unittest.main()
