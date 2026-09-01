from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"
TOKEN = "t" * 40
DELIVERABLE = "A real deliverable for this step. " * 12


class AgentApiTest(unittest.TestCase):
    """A worker outside this process doing a department's work over HTTP."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = {k: os.environ.get(k) for k in
                     ("MYORG_RUNS_DIR", "MYORG_AGENT_TOKEN", "MYORG_LEASE_SECONDS")}
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        os.environ["MYORG_AGENT_TOKEN"] = TOKEN
        os.environ["MYORG_LEASE_SECONDS"] = "600"
        self.addCleanup(self._restore)

        from runtime import agent_api, company_runtime, executor, health, leases
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        importlib.reload(health)
        self.leases = importlib.reload(leases)
        self.api = importlib.reload(agent_api)
        for module in (company_runtime, executor, health, leases, agent_api):
            self.addCleanup(lambda m=module: importlib.reload(m))

        self.logs: list[str] = []
        self.addCleanup(self.clear_evidence)

        self.server = self.api.ThreadingHTTPServer((self.api.HOST, 0), self.api.Handler)
        self.base = f"http://{self.api.HOST}:{self.server.server_address[1]}"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("api-*.evidence"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def create(self, run_id: str) -> None:
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(WORKFLOW), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}"))

    def call(self, path, body=None, token=TOKEN, method=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data,
            method=method or ("POST" if data else "GET"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    # --- the door is locked ---------------------------------------------------------

    def test_no_token_is_refused(self):
        self.assertEqual(self.call("/v1/work", token=None)[0], 401)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.call("/v1/work", token="x" * 40)[0], 401)

    def test_a_short_token_cannot_even_start_the_api(self):
        os.environ["MYORG_AGENT_TOKEN"] = "tooshort"
        with self.assertRaises(SystemExit):
            self.api.token()

    def test_unknown_routes_are_not_found(self):
        self.assertEqual(self.call("/v1/anything")[0], 404)
        self.assertEqual(self.call("/v1/nope", {})[0], 404)

    # --- finding and claiming work --------------------------------------------------

    def test_open_work_lists_only_what_can_be_picked_up(self):
        self.create("api-one")
        status, body = self.call("/v1/work")
        self.assertEqual(status, 200)
        self.assertEqual([w["step"] for w in body["work"]], ["frame-goal"])
        self.assertEqual(body["work"][0]["owner"], "chief-of-staff")

    def test_work_can_be_filtered_to_one_department(self):
        self.create("api-filter")
        self.assertTrue(self.call("/v1/work?agent=chief-of-staff")[1]["work"])
        self.assertEqual(self.call("/v1/work?agent=cfo-finance")[1]["work"], [])

    def test_claiming_returns_the_prompt_and_a_lease(self):
        self.create("api-claim")
        status, body = self.call("/v1/claim", {
            "run_id": "api-claim", "step": "frame-goal", "agent": "chief-of-staff"})
        self.assertEqual(status, 200)
        self.assertTrue(body["claimed"])
        self.assertIn("chief-of-staff", body["prompt"])
        self.assertTrue(body["lease_expires_at"])
        self.assertTrue(body["revision"])

    def test_a_department_cannot_claim_another_department_s_step(self):
        self.create("api-wrong")
        status, body = self.call("/v1/claim", {
            "run_id": "api-wrong", "step": "frame-goal", "agent": "cfo-finance"})
        self.assertEqual(status, 409)
        self.assertIn("owner", body["error"])

    def test_two_workers_cannot_hold_the_same_step(self):
        self.create("api-race")
        self.call("/v1/claim", {"run_id": "api-race", "step": "frame-goal",
                                "agent": "chief-of-staff"})
        status, body = self.call("/v1/claim", {
            "run_id": "api-race", "step": "frame-goal", "agent": "chief-of-staff"})
        self.assertEqual(status, 409)
        self.assertIn("already held", body["error"])

    def test_missing_fields_are_rejected(self):
        self.assertEqual(self.call("/v1/claim", {"run_id": "x"})[0], 400)
        self.assertEqual(self.call("/v1/submit", {})[0], 400)

    # --- a gated step is never handed to a worker ----------------------------------

    def test_a_yellow_step_is_never_given_out_to_be_worked(self):
        self.create("api-gate")
        self.executor.advance("api-gate", self.executor.StubBackend(),
                              log=self.logs.append)
        # release-output is yellow and now parked on a human.
        self.assertEqual(self.call("/v1/work")[1]["work"], [])
        status, body = self.call("/v1/claim", {
            "run_id": "api-gate", "step": "release-output", "agent": "chief-of-staff"})
        self.assertEqual(status, 409)

    def test_the_api_offers_no_way_to_approve_anything(self):
        self.assertNotIn("/v1/approve", self.api.ROUTES)
        self.assertNotIn("/v1/decide", self.api.ROUTES)

    # --- submitting -----------------------------------------------------------------

    def claim_first(self, run_id="api-submit"):
        self.create(run_id)
        self.call("/v1/claim", {"run_id": run_id, "step": "frame-goal",
                                "agent": "chief-of-staff"})
        return run_id

    def test_submitted_work_becomes_hashed_evidence(self):
        run_id = self.claim_first()
        status, body = self.call("/v1/submit", {
            "run_id": run_id, "step": "frame-goal", "agent": "chief-of-staff",
            "output": DELIVERABLE})
        self.assertEqual(status, 200)
        self.assertTrue(body["accepted"])

        step = self.executor.current_state(run_id)["steps"]["frame-goal"]
        self.assertEqual(step["status"], "completed")
        self.assertEqual(step["evidence_sha256"],
                         self.core.evidence_path(step["evidence"])[1])
        self.assertIsNone(self.leases.held_by(run_id, "frame-goal"))

    def test_a_refusal_sent_in_is_rejected_like_any_other(self):
        run_id = self.claim_first("api-junk")
        status, body = self.call("/v1/submit", {
            "run_id": run_id, "step": "frame-goal", "agent": "chief-of-staff",
            "output": "I need more information about this. " * 8})
        self.assertEqual(status, 422)
        self.assertIn("refuses", body["error"])
        self.assertNotEqual(
            self.executor.current_state(run_id)["steps"]["frame-goal"]["status"],
            "completed")

    def test_only_the_holder_may_submit(self):
        run_id = self.claim_first("api-thief")
        status, body = self.call("/v1/submit", {
            "run_id": run_id, "step": "frame-goal", "agent": "cfo-finance",
            "output": DELIVERABLE})
        self.assertEqual(status, 409)
        self.assertIn("does not hold", body["error"])

    def test_a_worker_can_give_the_work_back(self):
        run_id = self.claim_first("api-giveup")
        status, _ = self.call("/v1/fail", {
            "run_id": run_id, "step": "frame-goal", "agent": "chief-of-staff",
            "reason": "cannot reach the source system"})
        self.assertEqual(status, 200)
        self.assertEqual(
            self.executor.current_state(run_id)["steps"]["frame-goal"]["status"], "ready")
        self.assertIsNone(self.leases.held_by(run_id, "frame-goal"))

    # --- heartbeats and dead workers ------------------------------------------------

    def test_a_heartbeat_extends_the_lease(self):
        run_id = self.claim_first("api-beat")
        before = self.leases.held_by(run_id, "frame-goal").expires_at
        later = datetime.now(timezone.utc) + timedelta(seconds=60)
        renewed = self.leases.renew(run_id, "frame-goal", "chief-of-staff", now=later)
        self.assertGreater(renewed.expires_at, before)

    def test_only_the_holder_may_heartbeat(self):
        run_id = self.claim_first("api-beat2")
        status, body = self.call("/v1/heartbeat", {
            "run_id": run_id, "step": "frame-goal", "agent": "cfo-finance"})
        self.assertEqual(status, 409)
        self.assertIn("held by", body["error"])

    def test_a_heartbeat_on_nothing_is_refused(self):
        self.create("api-nolease")
        status, _ = self.call("/v1/heartbeat", {
            "run_id": "api-nolease", "step": "frame-goal", "agent": "chief-of-staff"})
        self.assertEqual(status, 409)

    def test_work_abandoned_by_a_dead_worker_is_given_back(self):
        run_id = self.claim_first("api-dead")
        gone = datetime.now(timezone.utc) + timedelta(seconds=3600)

        self.assertTrue(self.leases.abandoned(gone))
        recovered = self.leases.reclaim(now=gone, log=self.logs.append)
        self.assertEqual(recovered, [f"{run_id}/frame-goal"])
        # Back in the pool, and the runtime's retry budget counted the attempt.
        self.assertEqual(
            self.executor.current_state(run_id)["steps"]["frame-goal"]["status"], "ready")
        self.assertIsNone(self.leases.held_by(run_id, "frame-goal"))

    def test_a_live_lease_is_never_reclaimed(self):
        self.claim_first("api-alive")
        self.assertEqual(self.leases.abandoned(), [])
        self.assertEqual(self.leases.reclaim(log=self.logs.append), [])

    def test_health_is_readable_over_the_api(self):
        self.create("api-health")
        status, body = self.call("/v1/health")
        self.assertEqual(status, 200)
        self.assertIn("api-health", [run["run_id"] for run in body["runs"]])


if __name__ == "__main__":
    unittest.main()
