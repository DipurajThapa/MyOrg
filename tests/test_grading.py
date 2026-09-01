"""A quality gate that cannot run must not report a pass.

Before VAL-07 a grader outage was logged and ignored: the step completed unscored and the
run finished normally, so an ungraded deliverable was indistinguishable from a graded one.
These tests hold the gate closed.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CountingBackend:
    """Stub work, with a grader that can be told to break."""

    def __init__(self, grade_failures: int = 0, verdict: str = "MEETS") -> None:
        from runtime.backends import StubBackend
        self.inner = StubBackend()
        self.grade_failures = grade_failures
        self.verdict = verdict
        self.dispatched = 0
        self.graded = 0

    def __call__(self, request):
        from runtime.backends import ExecutorError
        if request.kind == "grade":
            self.graded += 1
            if self.graded <= self.grade_failures:
                raise ExecutorError("grader unavailable (simulated outage)")
            return f"VERDICT: {self.verdict}\nbecause the stub said so"
        if request.kind == "work":
            self.dispatched += 1
        return self.inner(request)


class GradingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._runs = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self._previous = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._runs.name) / "_audit-log.jsonl")
        self.addCleanup(self._restore)

        from runtime import company_runtime, executor
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.addCleanup(lambda: importlib.reload(company_runtime))
        self.addCleanup(lambda: importlib.reload(executor))
        self.executor.GRADE_BACKOFF_SECONDS = 0  # no real waiting in tests
        self.addCleanup(self.clear_evidence)
        self.logs: list[str] = []

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("grade-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        import argparse
        return argparse.Namespace(**fields)

    def make_run(self, run_id: str, acceptance=("Must name an owner",)) -> None:
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": f"grading probe {run_id}",
                    "max_cycles": 20,
                    "steps": [{"id": "s1", "owner": "cto-engineering", "action": "draft",
                               "depends_on": [], "max_attempts": 3,
                               "acceptance": list(acceptance)}]}
        path = Path(self._runs.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.core.create_run(self.ns(workflow=str(path), run_id=run_id,
                                     actor="chief-of-staff",
                                     request_id=f"create-{run_id}", org="default"))

    def drive(self, run_id: str, backend):
        return self.executor.advance(run_id, backend, log=self.logs.append)

    def step_of(self, run_id: str) -> dict:
        return self.core.read_events(run_id)[-1]["steps"]["s1"]

    def audit_entries(self) -> list[dict]:
        path = Path(os.environ["MYORG_AUDIT_LOG"])
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- the gate must not pass work it could not grade ---------------------------

    def test_a_grader_outage_does_not_pass_the_work(self) -> None:
        self.make_run("grade-outage")
        self.drive("grade-outage", CountingBackend(grade_failures=99))
        self.assertNotEqual(self.step_of("grade-outage")["status"], "completed")

    def test_a_grader_outage_parks_the_work_for_a_human(self) -> None:
        self.make_run("grade-park")
        self.drive("grade-park", CountingBackend(grade_failures=99))
        self.assertEqual(self.step_of("grade-park")["status"], "awaiting_approval")

    def test_the_ungraded_work_is_kept_so_a_human_can_read_it(self) -> None:
        self.make_run("grade-kept")
        self.drive("grade-kept", CountingBackend(grade_failures=99))
        held = self.step_of("grade-kept").get("held_evidence")
        self.assertTrue(held, "the work a human is asked to judge must be on disk")
        self.assertTrue((ROOT / held).is_file())
        self.assertIn("cto-engineering", (ROOT / held).read_text(encoding="utf-8"))

    def test_the_reason_the_gate_could_not_run_is_recorded(self) -> None:
        self.make_run("grade-reason")
        self.drive("grade-reason", CountingBackend(grade_failures=99))
        self.assertIn("grader unavailable", self.step_of("grade-reason").get("held_reason", ""))

    def test_holding_a_step_is_recorded_in_the_audit_log(self) -> None:
        self.make_run("grade-audit")
        self.drive("grade-audit", CountingBackend(grade_failures=99))
        held = [e for e in self.audit_entries() if e["outcome"] == "awaiting-approval"]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["approval"], "pending")

    # --- but a blip must not cost a human's attention ------------------------------

    def test_a_transient_grader_failure_is_retried_and_the_work_still_passes(self) -> None:
        self.make_run("grade-blip")
        backend = CountingBackend(grade_failures=1)
        self.drive("grade-blip", backend)
        self.assertEqual(self.step_of("grade-blip")["status"], "completed")
        self.assertEqual(backend.dispatched, 1, "a grader blip must not redo the work")

    # --- once a human decides, the work already done is used -----------------------

    def test_approving_held_work_finishes_it_without_re_running_the_agent(self) -> None:
        self.make_run("grade-approved")
        backend = CountingBackend(grade_failures=99)
        self.drive("grade-approved", backend)
        self.assertEqual(backend.dispatched, 1)
        self.core.approve(self.ns(run_id="grade-approved", step="s1", approver="Dipuraj",
                                  approval_ref="ungraded-ok-1", request_id="app-grade"))
        self.drive("grade-approved", backend)
        self.assertEqual(self.step_of("grade-approved")["status"], "completed")
        self.assertEqual(backend.dispatched, 1,
                         "approved work must be used, not produced again")

    # --- the human must be told why they are being asked -------------------------

    def test_the_console_says_why_the_work_is_parked(self) -> None:
        """A green step waiting on a person with no visible reason invites rubber-stamping."""
        self.make_run("grade-console")
        self.drive("grade-console", CountingBackend(grade_failures=99))
        from runtime import approvals, approval_server
        import importlib as il
        waiting = il.reload(approvals).pending("grade-console")
        self.assertEqual(len(waiting), 1)
        self.assertIn("could not run", waiting[0].reason)
        page = il.reload(approval_server).card(waiting[0], 1, 1)
        self.assertIn("could not run", page)

    # --- and a real failed grade must behave exactly as it did before ---------------

    def test_work_that_fails_its_criteria_is_still_retried_not_parked(self) -> None:
        self.make_run("grade-failed")
        backend = CountingBackend(grade_failures=0, verdict="FAILS")
        self.drive("grade-failed", backend)
        step = self.step_of("grade-failed")
        self.assertEqual(step["status"], "blocked_retry_limit")
        self.assertIn("did not meet acceptance criteria", step.get("last_failure", ""))
        self.assertGreater(backend.dispatched, 1, "a bad deliverable is reworked")


class ApiGradingTest(unittest.TestCase):
    """Work submitted from outside faces the same closed gate."""

    def setUp(self) -> None:
        self._runs = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self._previous = {k: os.environ.get(k) for k in
                          ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", "MYORG_AGENT_API_TOKEN")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._runs.name) / "_audit-log.jsonl")
        os.environ["MYORG_AGENT_API_TOKEN"] = "x" * 40
        self.addCleanup(self._restore)

        from runtime import company_runtime, executor, agent_api
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.api = importlib.reload(agent_api)
        for module in (company_runtime, executor, agent_api):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.addCleanup(self.clear_evidence)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("api-grade.*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        import argparse
        return argparse.Namespace(**fields)

    def start_claimed_run(self) -> None:
        workflow = {"version": 1, "id": "wf-api-grade", "goal": "api grading probe",
                    "max_cycles": 20,
                    "steps": [{"id": "s1", "owner": "cto-engineering", "action": "draft",
                               "depends_on": [], "max_attempts": 3,
                               "acceptance": ["Must name an owner"]}]}
        path = Path(self._runs.name) / "api-grade.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.core.create_run(self.ns(workflow=str(path), run_id="api-grade",
                                     actor="chief-of-staff", request_id="create-api-grade",
                                     org="default"))
        self.api.claim({"run_id": "api-grade", "step": "s1", "agent": "cto-engineering"})

    def break_the_grader(self) -> None:
        """Replace the name `submit` actually calls -- no model is reached."""
        def broken(*_args, **_kwargs):
            raise self.executor.GraderUnavailable("grader unavailable (simulated outage)")
        self.api.graded_failure = broken

    # A deliverable that would pass its criteria, so only the broken gate can stop it.
    GOOD_WORK = ("Owner: the CTO owns this deliverable end to end.\n"
                 "It sets out the plan, the risks and the checks in full sentences, "
                 "with enough detail that a reader who was not in the room can act on it. ") * 8

    def test_work_submitted_over_the_api_is_not_accepted_when_the_grader_is_down(self) -> None:
        self.start_claimed_run()
        self.break_the_grader()
        with self.assertRaises(self.api.ApiError) as caught:
            self.api.submit({"run_id": "api-grade", "step": "s1",
                             "agent": "cto-engineering", "output": self.GOOD_WORK})
        self.assertEqual(caught.exception.status, 503)
        step = self.core.read_events("api-grade")[-1]["steps"]["s1"]
        self.assertEqual(step["status"], "awaiting_approval")

    def test_the_outside_workers_output_is_kept_when_the_gate_cannot_run(self) -> None:
        self.start_claimed_run()
        self.break_the_grader()
        with self.assertRaises(self.api.ApiError):
            self.api.submit({"run_id": "api-grade", "step": "s1",
                             "agent": "cto-engineering", "output": self.GOOD_WORK})
        held = self.core.read_events("api-grade")[-1]["steps"]["s1"].get("held_evidence")
        self.assertTrue(held, "the worker's output must not be thrown away")
        self.assertIn("the CTO owns this deliverable", (ROOT / held).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
