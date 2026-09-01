"""Who is allowed to decide what may wake the company up.

A trigger is standing permission: once registered, it starts work forever without anyone
approving each run. That makes registering one a governance act, not a convenience -- so it
carries the same bar as enabling a connector (a named human with system-admin), and these
tests exist to keep that bar from quietly slipping to "any authenticated caller".
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from runtime.auth import TokenService
from runtime.connectors import validate_manifest
from runtime.db import Store
from runtime.service import Forbidden, MyOrgService, ServiceError

SECRET = "0123456789abcdef0123456789abcdef"
SECRET_REF = "MYORG_TEST_ADMIN_TOKEN"


class TriggerAdminTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self._previous = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self.temporary.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self.temporary.name) / "_audit-log.jsonl")

        self.store = Store(Path(self.temporary.name) / "myorg.db")
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.store.bootstrap_organization("other", "Other")
        self.store.upsert_actor("acme", "admin", "human", "Admin", ["system-admin"])
        self.store.upsert_actor("acme", "viewer", "human", "Viewer", ["viewer"])
        self.store.upsert_actor("acme", "robot", "agent", "Robot", ["system-admin"])
        self.store.upsert_actor("other", "admin", "human", "Other Admin", ["system-admin"])
        self.store.register_connector("acme", validate_manifest({
            "id": "crm", "kind": "http", "mode": "read_only",
            "base_url": "https://api.example.com", "allowed_hosts": ["api.example.com"],
            "allowed_actions": ["lead_created"], "secret_ref": SECRET_REF,
            "timeout_seconds": 5, "max_response_bytes": 4096, "enabled": False}))
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def principal(self, actor="admin", org="acme"):
        return self.tokens.verify(self.tokens.issue(org, actor))

    def trigger_body(self, **overrides) -> dict:
        body = {"connector_id": "crm", "event_type": "lead.created",
                "goal": "Qualify the lead", "enabled": True}
        body.update(overrides)
        return body

    def schedule_body(self, **overrides) -> dict:
        body = {"id": "daily-brief", "kind": "daily", "interval_seconds": None,
                "daily_at": "07:30", "goal": "Write the daily brief"}
        body.update(overrides)
        return body

    # --- authority ----------------------------------------------------------------------

    def test_an_admin_can_register_a_trigger(self) -> None:
        result = self.service.register_webhook_trigger(
            self.principal(), self.trigger_body(), "req-trigger-1", "trace-1")
        self.assertEqual(result["goal"], "Qualify the lead")
        self.assertEqual(result["enabled"], 1)

    def test_a_viewer_cannot_register_a_trigger(self) -> None:
        with self.assertRaises(Forbidden):
            self.service.register_webhook_trigger(
                self.principal("viewer"), self.trigger_body(), "req-trigger-2", "trace-1")

    def test_an_agent_cannot_register_a_trigger_even_with_the_right_role(self) -> None:
        """Standing permission to act unattended must be granted by a person."""
        with self.assertRaises(Forbidden):
            self.service.register_webhook_trigger(
                self.principal("robot"), self.trigger_body(), "req-trigger-3", "trace-1")

    def test_an_agent_cannot_create_a_schedule(self) -> None:
        with self.assertRaises(Forbidden):
            self.service.create_schedule(
                self.principal("robot"), self.schedule_body(), "req-sched-1", "trace-1")

    def test_a_viewer_cannot_pause_a_schedule(self) -> None:
        self.service.create_schedule(self.principal(), self.schedule_body(), "req-sched-2", "trace-1")
        with self.assertRaises(Forbidden):
            self.service.set_schedule_enabled(self.principal("viewer"), "daily-brief",
                                              {"enabled": False}, "req-sched-3", "trace-1")

    # --- isolation ----------------------------------------------------------------------

    def test_one_organization_never_sees_another_organizations_schedules(self) -> None:
        self.service.create_schedule(self.principal(), self.schedule_body(), "req-sched-4", "trace-1")
        self.assertEqual(self.service.schedules(self.principal("admin", "other")), [])
        self.assertEqual(len(self.service.schedules(self.principal())), 1)

    # --- validation ---------------------------------------------------------------------

    def test_a_schedule_must_state_exactly_one_kind_of_timing(self) -> None:
        for body in (self.schedule_body(interval_seconds=3600),
                     self.schedule_body(kind="interval", daily_at="07:30", interval_seconds=None)):
            with self.assertRaises(ServiceError):
                self.service.create_schedule(self.principal(), body, "req-sched-5", "trace-1")

    def test_an_interval_below_a_minute_is_refused_at_the_boundary(self) -> None:
        with self.assertRaises(ServiceError):
            self.service.create_schedule(
                self.principal(), self.schedule_body(kind="interval", daily_at=None,
                                                     interval_seconds=5),
                "req-sched-6", "trace-1")

    def test_an_oversized_goal_is_refused(self) -> None:
        with self.assertRaises(ServiceError):
            self.service.register_webhook_trigger(
                self.principal(), self.trigger_body(goal="x" * 501), "req-trigger-4", "trace-1")

    def test_an_event_type_that_is_not_a_slug_is_refused(self) -> None:
        with self.assertRaises(ServiceError):
            self.service.register_webhook_trigger(
                self.principal(), self.trigger_body(event_type="../../etc/passwd"),
                "req-trigger-5", "trace-1")

    def test_registering_a_trigger_leaves_an_operational_event(self) -> None:
        self.service.register_webhook_trigger(
            self.principal(), self.trigger_body(), "req-trigger-6", "trace-1")
        with self.store.reading() as connection:
            actions = [row["action"] for row in connection.execute(
                "SELECT action FROM operational_events WHERE org_id='acme'")]
        self.assertIn("webhook.registered", actions)


class TriggerAdminOverHttpTest(TriggerAdminTest):
    """The same rules through the server the operator's browser talks to."""

    def setUp(self) -> None:
        super().setUp()
        from runtime.api import create_server
        self.server = create_server("127.0.0.1", 0, self.store.path, SECRET)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", actor="admin", org="acme", body=None,
                request_id="trigger-request-1"):
        headers = {"Authorization": "Bearer " + self.tokens.issue(org, actor)}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["X-Request-Id"] = request_id
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, json.loads(response.read() or b"null")

    def test_the_full_admin_round_trip_over_http(self) -> None:
        status, _ = self.request("/v1/triggers/webhook", "POST", body=self.trigger_body(),
                                 request_id="http-trigger-1")
        self.assertEqual(status, 201)
        status, created = self.request("/v1/schedules", "POST", body=self.schedule_body(),
                                       request_id="http-sched-1")
        self.assertEqual(status, 201)
        self.assertEqual(created["enabled"], 1)
        status, listed = self.request("/v1/schedules")
        self.assertEqual((status, len(listed)), (200, 1))
        status, paused = self.request("/v1/schedules/daily-brief/status", "PUT",
                                      body={"enabled": False}, request_id="http-sched-2")
        self.assertEqual((status, paused["enabled"]), (200, 0))

    def test_a_viewer_is_refused_over_http_too(self) -> None:
        status, _ = self.request("/v1/schedules", "POST", actor="viewer",
                                 body=self.schedule_body(), request_id="http-sched-3")
        self.assertEqual(status, 403)

    def test_an_unauthenticated_caller_cannot_list_schedules(self) -> None:
        request = urllib.request.Request(self.base + "/v1/schedules", method="GET")
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        self.assertEqual(response.status, 401)

    def test_in_flight_receipts_are_visible_to_an_operator(self) -> None:
        status, payload = self.request("/v1/connectors/in-flight")
        self.assertEqual((status, payload), (200, []))


if __name__ == "__main__":
    unittest.main()
