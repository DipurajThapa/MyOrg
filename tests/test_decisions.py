"""The human decision queue, end to end: run state -> service -> HTTP -> audit log.

HITL-04 in the REV2 audit: the runtime parks yellow and red steps correctly, and the local
console can decide them, but the web application had no way to see or answer them. A
decision that only exists on the operator's own machine is not an operating surface.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from runtime.auth import TokenService
from runtime.db import Store
from runtime.service import Forbidden, MyOrgService, ServiceError

ROOT = Path(__file__).resolve().parents[1]
SECRET = "0123456789abcdef0123456789abcdef"


class DecisionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self._runs = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)

        self._previous = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        self.audit_log = Path(self._runs.name) / "_audit-log.jsonl"
        os.environ["MYORG_AUDIT_LOG"] = str(self.audit_log)
        self.addCleanup(self._restore)

        from runtime import company_runtime, executor, approvals
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.approvals = importlib.reload(approvals)
        for module in (company_runtime, executor, approvals):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.addCleanup(self.clear_evidence)

        self.path = Path(self.temporary.name) / "myorg.db"
        self.store = Store(self.path)
        self.store.migrate()
        for org in ("acme", "other"):
            self.store.bootstrap_organization(org, org.title())
        self.store.upsert_actor("acme", "chief", "human", "Chief Operator", ["decision-owner"])
        self.store.upsert_actor("acme", "watcher", "human", "Watcher", ["viewer"])
        self.store.upsert_actor("acme", "robot", "agent", "Robot", ["decision-owner"])
        self.store.upsert_actor("other", "chief", "human", "Other Chief", ["decision-owner"])
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("dec-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def principal(self, org: str = "acme", actor: str = "chief"):
        return self.tokens.verify(self.tokens.issue(org, actor))

    def ns(self, **fields):
        return argparse.Namespace(**fields)

    def park_step(self, run_id: str, org: str = "acme", action: str = "publish") -> None:
        """Drive a run to its gate the way the real driver does."""
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": f"decision probe {run_id}",
                    "max_cycles": 12,
                    "steps": [{"id": "s1", "owner": "cmo-marketing", "action": action,
                               "depends_on": [], "max_attempts": 2}]}
        path = Path(self._runs.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.core.create_run(self.ns(workflow=str(path), run_id=run_id,
                                     actor="chief-of-staff", request_id=f"create-{run_id}",
                                     org=org))
        self.core.request_step(self.ns(run_id=run_id, step="s1", actor="cmo-marketing",
                                       holder="driver-a", request_id=f"req-{run_id}"))

    def step_of(self, run_id: str) -> dict:
        return self.core.read_events(run_id)[-1]["steps"]["s1"]

    def audit_entries(self) -> list[dict]:
        if not self.audit_log.is_file():
            return []
        return [json.loads(line) for line in
                self.audit_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- seeing what needs a person ------------------------------------------------

    def test_a_parked_step_appears_in_the_decision_queue(self) -> None:
        self.park_step("dec-queue")
        queue = self.service.pending_decisions(self.principal())
        self.assertEqual(len(queue), 1)
        entry = queue[0]
        self.assertEqual((entry["run_id"], entry["step"], entry["status"]),
                         ("dec-queue", "s1", "awaiting_approval"))
        self.assertEqual(entry["risk"], "yellow")
        self.assertTrue(entry["actionable"])
        self.assertIn("goal", entry)

    def test_a_red_step_is_shown_but_cannot_be_decided(self) -> None:
        self.park_step("dec-red", action="move_money")
        entry = self.service.pending_decisions(self.principal())[0]
        self.assertEqual(entry["risk"], "red")
        self.assertFalse(entry["actionable"])
        with self.assertRaises(ServiceError):
            self.service.decide_step(self.principal(), "dec-red", "s1",
                                     {"decision": "approve", "reason": "looks fine"}, "req-red")

    def test_another_organizations_decisions_are_not_visible(self) -> None:
        self.park_step("dec-mine", org="acme")
        self.park_step("dec-theirs", org="other")
        mine = [d["run_id"] for d in self.service.pending_decisions(self.principal())]
        self.assertEqual(mine, ["dec-mine"])
        theirs = [d["run_id"] for d in
                  self.service.pending_decisions(self.principal("other"))]
        self.assertEqual(theirs, ["dec-theirs"])

    # --- who may decide -------------------------------------------------------------

    def test_a_viewer_cannot_decide(self) -> None:
        self.park_step("dec-viewer")
        with self.assertRaises(Forbidden):
            self.service.decide_step(self.principal(actor="watcher"), "dec-viewer", "s1",
                                     {"decision": "approve", "reason": "ok"}, "req-viewer")
        self.assertEqual(self.step_of("dec-viewer")["status"], "awaiting_approval")

    def test_an_agent_identity_cannot_decide(self) -> None:
        self.park_step("dec-agent")
        with self.assertRaises(Forbidden):
            self.service.decide_step(self.principal(actor="robot"), "dec-agent", "s1",
                                     {"decision": "approve", "reason": "ok"}, "req-agent")

    def test_a_decision_on_another_organizations_run_is_refused(self) -> None:
        self.park_step("dec-crossorg", org="other")
        with self.assertRaises(ServiceError):
            self.service.decide_step(self.principal("acme"), "dec-crossorg", "s1",
                                     {"decision": "approve", "reason": "ok"}, "req-cross")
        self.assertEqual(self.step_of("dec-crossorg")["status"], "awaiting_approval")

    # --- deciding -------------------------------------------------------------------

    def test_approving_moves_the_step_forward(self) -> None:
        self.park_step("dec-approve")
        result = self.service.decide_step(
            self.principal(), "dec-approve", "s1",
            {"decision": "approve", "reason": "checked the copy, safe to publish"},
            "req-approve")
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(self.step_of("dec-approve")["approver"], "Chief Operator")

    def test_rejecting_stops_the_run(self) -> None:
        self.park_step("dec-reject")
        result = self.service.decide_step(
            self.principal(), "dec-reject", "s1",
            {"decision": "reject", "reason": "wrong audience"}, "req-reject")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.core.read_events("dec-reject")[-1]["run_status"], "rejected")

    def test_a_decision_needs_a_reason(self) -> None:
        self.park_step("dec-noreason")
        with self.assertRaises(ServiceError):
            self.service.decide_step(self.principal(), "dec-noreason", "s1",
                                     {"decision": "approve", "reason": "   "}, "req-noreason")
        self.assertEqual(self.step_of("dec-noreason")["status"], "awaiting_approval")

    def test_the_same_step_cannot_be_decided_twice(self) -> None:
        self.park_step("dec-twice")
        self.service.decide_step(self.principal(), "dec-twice", "s1",
                                 {"decision": "approve", "reason": "first and only"},
                                 "req-twice-one")
        with self.assertRaises(ServiceError):
            self.service.decide_step(self.principal(), "dec-twice", "s1",
                                     {"decision": "reject", "reason": "changed my mind"},
                                     "req-twice-two")

    def test_the_decision_reaches_the_audit_log_with_the_person_named(self) -> None:
        self.park_step("dec-audited")
        self.service.decide_step(self.principal(), "dec-audited", "s1",
                                 {"decision": "approve", "reason": "signed off in the review"},
                                 "req-audited")
        granted = [e for e in self.audit_entries() if e["approval"] == "granted"]
        self.assertEqual(len(granted), 1)
        self.assertEqual(granted[0]["actor"], "Chief Operator")
        self.assertIn("signed off in the review", granted[0]["note"])

    def test_the_decision_is_recorded_in_the_database_too(self) -> None:
        """The operator read model must show the same answer as the run log."""
        self.park_step("dec-recorded")
        self.service.decide_step(self.principal(), "dec-recorded", "s1",
                                 {"decision": "approve", "reason": "fine to send"},
                                 "req-recorded")
        with closing(sqlite3.connect(self.path)) as connection:
            actions = [row[0] for row in connection.execute(
                "SELECT action FROM operational_events WHERE org_id='acme'")]
        self.assertIn("step.decision", actions)


class DecisionsOverHttpTest(DecisionsTest):
    """The same queue, through the real server the web application talks to."""

    def setUp(self) -> None:
        super().setUp()
        import threading
        from runtime.api import create_server
        self.server = create_server("127.0.0.1", 0, self.store.path, SECRET,
                                    {"https://control.example"})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", actor="chief", org="acme", body=None,
                request_id="decision-request-1"):
        import urllib.error
        import urllib.request
        headers = {"Authorization": "Bearer " + self.tokens.issue(org, actor)}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            # Every write on this API carries a caller-supplied request id; the web
            # application must send one too.
            headers["X-Request-Id"] = request_id
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method)
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        payload = json.loads(response.read()) if response.length != 0 else None
        return response.status, payload

    def test_the_queue_is_served_over_http(self) -> None:
        self.park_step("dec-http")
        status, payload = self.request("/v1/decisions")
        self.assertEqual(status, 200)
        self.assertEqual([d["run_id"] for d in payload], ["dec-http"])

    def test_a_decision_can_be_taken_over_http_and_moves_the_run(self) -> None:
        self.park_step("dec-http-decide")
        status, payload = self.request(
            "/v1/decisions/dec-http-decide/s1", method="POST",
            body={"decision": "approve", "reason": "approved from the console"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "in_progress")
        self.assertEqual(self.step_of("dec-http-decide")["status"], "in_progress")

    def test_an_unauthenticated_caller_gets_nothing(self) -> None:
        import urllib.error
        import urllib.request
        self.park_step("dec-http-anon")
        req = urllib.request.Request(self.base + "/v1/decisions", method="GET")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.status, 401)

    def test_a_viewer_is_refused_over_http(self) -> None:
        self.park_step("dec-http-viewer")
        status, _ = self.request("/v1/decisions/dec-http-viewer/s1", method="POST",
                                 actor="watcher",
                                 body={"decision": "approve", "reason": "let me in"})
        self.assertEqual(status, 403)
        self.assertEqual(self.step_of("dec-http-viewer")["status"], "awaiting_approval")

    def test_a_malformed_decision_is_rejected_with_a_reason(self) -> None:
        self.park_step("dec-http-bad")
        status, payload = self.request("/v1/decisions/dec-http-bad/s1", method="POST",
                                       body={"decision": "maybe", "reason": "unsure"})
        self.assertEqual(status, 400)
        self.assertIn("approve", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
