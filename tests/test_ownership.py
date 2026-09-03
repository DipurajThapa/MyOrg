"""One step, one owner at a time -- enforced by the state machine, not by good manners.

Probe P5 in the REV2 audit: an outside worker claimed a step and held its lease, the
in-process driver dispatched the same step anyway, finished it, and the worker's finished
output was thrown away with "run is terminal". Duplicate model spend, discarded work, and
nothing recorded that it happened.

The fix follows Kleppmann's fencing-token argument: a claim mints a monotonically
increasing token, and the write path -- not the caller -- rejects any token that is not the
current one.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self._runs = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self._previous = {k: os.environ.get(k) for k in
                          ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", "MYORG_AGENT_TOKEN")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._runs.name) / "_audit-log.jsonl")
        os.environ["MYORG_AGENT_TOKEN"] = "x" * 40
        self.addCleanup(self._restore)

        from runtime import company_runtime, executor, agent_api
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.api = importlib.reload(agent_api)
        for module in (company_runtime, executor, agent_api):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.addCleanup(self.clear_evidence)
        self.logs: list[str] = []

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("own-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        return argparse.Namespace(**fields)

    def make_run(self, run_id: str, action: str = "draft") -> None:
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": f"ownership probe {run_id}",
                    "max_cycles": 20,
                    "steps": [{"id": "s1", "owner": "cto-engineering", "action": action,
                               "depends_on": [], "max_attempts": 3}]}
        path = Path(self._runs.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.core.create_run(self.ns(workflow=str(path), run_id=run_id,
                                     actor="chief-of-staff",
                                     request_id=f"create-{run_id}", org="default"))

    def step_of(self, run_id: str) -> dict:
        return self.core.read_events(run_id)[-1]["steps"]["s1"]

    def claim_as(self, run_id: str, holder: str) -> None:
        self.core.request_step(self.ns(run_id=run_id, step="s1", actor="cto-engineering",
                                       holder=holder, request_id=f"req-{run_id}-{holder}"))

    def evidence_for(self, run_id: str, text: str = "A complete deliverable. " * 20) -> str:
        return self.executor.write_evidence(run_id, "s1", text)

    # --- a claim mints a token, and the write path checks it -----------------------

    def test_claiming_a_step_records_who_holds_it(self) -> None:
        self.make_run("own-claim")
        self.claim_as("own-claim", "driver-a")
        step = self.step_of("own-claim")
        self.assertEqual(step["holder"], "driver-a")
        self.assertTrue(step["claim_token"])

    def test_a_second_holder_cannot_finish_work_it_does_not_hold(self) -> None:
        self.make_run("own-steal")
        self.claim_as("own-steal", "driver-a")
        revision = self.core.read_events("own-steal")[-1]["workflow_revision"]
        with self.assertRaises(SystemExit) as caught:
            self.core.complete(self.ns(
                run_id="own-steal", step="s1", actor="cto-engineering",
                evidence=self.evidence_for("own-steal"), revision=revision,
                claim_token="not-the-current-one", request_id="c-steal"))
        self.assertIn("claim", str(caught.exception).lower())
        self.assertEqual(self.step_of("own-steal")["status"], "in_progress")

    def test_a_second_holder_cannot_fail_work_it_does_not_hold(self) -> None:
        self.make_run("own-fail")
        self.claim_as("own-fail", "driver-a")
        with self.assertRaises(SystemExit):
            self.core.fail(self.ns(run_id="own-fail", step="s1", actor="cto-engineering",
                                   reason="not mine to fail", claim_token="stale",
                                   request_id="f-steal"))
        self.assertEqual(self.step_of("own-fail")["status"], "in_progress")

    def test_the_holder_can_finish_its_own_work(self) -> None:
        self.make_run("own-finish")
        self.claim_as("own-finish", "driver-a")
        step = self.step_of("own-finish")
        state = self.core.read_events("own-finish")[-1]
        self.core.complete(self.ns(
            run_id="own-finish", step="s1", actor="cto-engineering",
            evidence=self.evidence_for("own-finish"),
            revision=state["workflow_revision"], claim_token=step["claim_token"],
            request_id="c-finish"))
        self.assertEqual(self.step_of("own-finish")["status"], "completed")

    # --- the defect from probe P5, reproduced end to end ---------------------------

    def test_the_driver_leaves_alone_a_step_another_worker_is_doing(self) -> None:
        from tests.test_grading import CountingBackend
        self.make_run("own-p5")
        claimed = self.api.claim({"run_id": "own-p5", "step": "s1",
                                  "agent": "cto-engineering"})
        self.assertTrue(claimed["claimed"])
        backend = CountingBackend()
        self.executor.advance("own-p5", backend, log=self.logs.append)
        self.assertEqual(backend.dispatched, 0,
                         "the driver must not redo work someone else is holding")
        self.assertEqual(self.step_of("own-p5")["status"], "in_progress")

    def test_the_outside_worker_can_still_hand_its_work_in(self) -> None:
        from tests.test_grading import CountingBackend
        self.make_run("own-p5b")
        token = self.api.claim({"run_id": "own-p5b", "step": "s1",
                                "agent": "cto-engineering"})["claim_token"]
        self.executor.advance("own-p5b", CountingBackend(), log=self.logs.append)
        result = self.api.submit({"run_id": "own-p5b", "step": "s1",
                                  "agent": "cto-engineering", "claim_token": token,
                                  "output": "The outside worker's finished deliverable. " * 20})
        self.assertTrue(result["accepted"])
        self.assertEqual(self.step_of("own-p5b")["status"], "completed")

    # --- a dead holder must not block the step forever -----------------------------

    def test_an_abandoned_claim_can_be_taken_over(self) -> None:
        self.make_run("own-abandoned")
        self.claim_as("own-abandoned", "driver-gone")
        self.core.expire_claim(self.ns(run_id="own-abandoned", step="s1",
                                       request_id="exp-1"))
        self.core.take(self.ns(run_id="own-abandoned", step="s1",
                               actor="cto-engineering", holder="driver-b",
                               request_id="take-1"))
        self.assertEqual(self.step_of("own-abandoned")["holder"], "driver-b")

    def test_a_live_claim_cannot_be_taken_over(self) -> None:
        self.make_run("own-live")
        self.claim_as("own-live", "driver-a")
        with self.assertRaises(SystemExit):
            self.core.take(self.ns(run_id="own-live", step="s1", actor="cto-engineering",
                                   holder="driver-b", request_id="take-2"))
        self.assertEqual(self.step_of("own-live")["holder"], "driver-a")

    # --- regressions: the paths that already worked must keep working ---------------

    def test_a_single_driver_still_completes_a_run(self) -> None:
        from tests.test_grading import CountingBackend
        self.make_run("own-solo")
        backend = CountingBackend()
        self.executor.advance("own-solo", backend, log=self.logs.append)
        self.assertEqual(self.step_of("own-solo")["status"], "completed")
        self.assertEqual(backend.dispatched, 1)

    def test_work_a_human_approved_is_picked_up_by_the_driver(self) -> None:
        from tests.test_grading import CountingBackend
        self.make_run("own-approved", action="publish")
        backend = CountingBackend()
        self.executor.advance("own-approved", backend, log=self.logs.append)
        self.assertEqual(self.step_of("own-approved")["status"], "awaiting_approval")
        self.core.approve(self.ns(run_id="own-approved", step="s1", approver="Dipuraj",
                                  approval_ref="ok-1", request_id="app-own"))
        self.executor.advance("own-approved", backend, log=self.logs.append)
        self.assertEqual(self.step_of("own-approved")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
