"""Work in, work out — the two halves an operator needs beside the approval queue.

Before this the planner was reachable only by a signed webhook or the company's own clock,
and a run's work products existed only as files on disk. `submit_idea` makes a person a
trigger source; `run_output` makes what the company produced readable. Both ride machinery
that already existed, so what matters here is the boundary: who may ask for work, what an
evidence reference is allowed to point at, and which runs a caller may see at all.
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from runtime.auth import TokenService
from runtime.db import Store
from runtime.service import Forbidden, MyOrgService, ServiceError

ROOT = Path(__file__).resolve().parents[1]
SECRET = "0123456789abcdef0123456789abcdef"


class OperatorWork(unittest.TestCase):
    def setUp(self):
        # Inside the repository on purpose: `company_runtime.evidence_path` refuses evidence
        # that does not resolve under the repository root, so a runs directory in the system
        # temp area cannot complete a step at all.
        self._tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self._tmp.cleanup)
        keys = ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")
        self._previous = {key: os.environ.get(key) for key in keys}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._tmp.name) / "_audit-log.jsonl")

        from runtime import company_runtime
        self.core = importlib.reload(company_runtime)

        self.store = Store(Path(self._tmp.name) / "myorg.db")
        self.store.migrate()
        for org in ("acme", "other", self.core.DEFAULT_ORG):
            self.store.bootstrap_organization(org, org)
            self.store.upsert_actor(org, "owner", "human", "Owner", ["decision-owner"])
        self.store.upsert_actor("acme", "looker", "human", "Looker", ["viewer"])
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

    def _restore(self):
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from runtime import company_runtime
        importlib.reload(company_runtime)

    def principal(self, actor="owner", org="acme"):
        return self.tokens.verify(self.tokens.issue(org, actor))

    # --- asking for work ------------------------------------------------------

    def test_an_idea_is_queued_as_operator_and_names_the_run_it_will_become(self):
        result = self.service.submit_idea(
            self.principal(), {"goal": "Summarise last quarter's support tickets."}, "idea-one")
        self.assertEqual(result["status"], "queued")
        self.assertTrue(result["created"])
        self.assertTrue(result["run_id"].startswith("run-"))
        queued = self.store.queued_triggers("acme", 10)
        self.assertEqual([row["source"] for row in queued], ["operator"],
                         "a person is a distinct trigger source, not a fake schedule")
        self.assertEqual(queued[0]["goal"], "Summarise last quarter's support tickets.")

    def test_the_same_request_id_queues_one_idea(self):
        first = self.service.submit_idea(self.principal(), {"goal": "Do the thing once."}, "idea-dup")
        second = self.service.submit_idea(self.principal(), {"goal": "Do the thing once."}, "idea-dup")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["intake_id"], second["intake_id"])
        self.assertEqual(len(self.store.queued_triggers("acme", 10)), 1)

    def test_the_decision_owner_may_ask_for_work_and_a_viewer_may_not(self):
        self.service.submit_idea(self.principal(), {"goal": "A goal long enough."}, "idea-role")
        with self.assertRaises(Forbidden):
            self.service.submit_idea(self.principal("looker"),
                                     {"goal": "A goal long enough."}, "idea-denied")

    def test_a_goal_must_be_one_printable_line_of_a_sane_length(self):
        for goal in ("too short", "x" * 501, "two\nlines that are long enough"):
            with self.assertRaises(ServiceError, msg=goal):
                self.service.submit_idea(self.principal(), {"goal": goal}, "idea-bad")
        with self.assertRaises(ServiceError):
            self.service.submit_idea(self.principal(),
                                     {"goal": "A fine goal.", "extra": 1}, "idea-extra")
        self.assertEqual(self.store.queued_triggers("acme", 10), [])

    def test_ideas_are_scoped_to_the_organization_that_asked(self):
        self.service.submit_idea(self.principal(), {"goal": "Acme's own work item."}, "idea-acme")
        self.assertEqual(len(self.service.ideas(self.principal())), 1)
        self.assertEqual(self.service.ideas(self.principal("owner", "other")), [])

    def test_an_abandoned_idea_stays_on_screen_and_raises_a_notice(self):
        """The planner can give up. Nothing else here is a run, so a dead trigger fell
        through every check: money spent, request deleted from every screen, operator never
        told. It must stay listed *and* reach the person who asked."""
        asked = self.service.submit_idea(
            self.principal(), {"goal": "Something the planner will choke on."}, "idea-doomed")
        self.store.settle_trigger("acme", asked["intake_id"], "failed", None,
                                  "claude exited 1: result=Prompt is too long")
        listed = self.service.ideas(self.principal())
        self.assertEqual([item["status"] for item in listed], ["failed"])
        self.assertIn("Prompt is too long", listed[0]["last_error"])

        from runtime import escalation
        with unittest.mock.patch("runtime.projection.default_db",
                                 return_value=self.store.path):
            raised = escalation.escalate_ideas()
        self.assertEqual([notice.kind for notice in raised], ["idea_failed"])
        self.assertIn("Prompt is too long", raised[0].detail)
        self.assertIn("choke on", raised[0].subject)

    def test_an_idea_retrying_for_too_long_stops_being_silent(self):
        """A transient failure spends no attempt, so an idea retries every sweep for as long
        as the other end stays busy. Right for a bad minute, silent forever during an outage:
        after the threshold the retrying itself is what a person needs to hear."""
        import sqlite3
        from runtime import escalation
        asked = self.service.submit_idea(
            self.principal(), {"goal": "Something during an API outage."}, "idea-stuck")
        self.store.settle_trigger("acme", asked["intake_id"], "queued", None,
                                  "claude exited 1: result=API Error: 529 Overloaded",
                                  count_attempt=False)

        with unittest.mock.patch("runtime.projection.default_db", return_value=self.store.path):
            self.assertEqual(escalation.escalate_stuck_ideas(self.store), [],
                             "a fresh failure is not yet news")

            # Age it past the threshold; the row is the only clock this check has.
            # `with sqlite3.connect(...)` commits but does not close, and an open handle
            # stops Windows removing the temp directory at cleanup.
            old = "2020-01-01T00:00:00Z"
            connection = sqlite3.connect(self.store.path)
            try:
                connection.execute("UPDATE trigger_intake SET created_at=? WHERE id=?",
                                   (old, asked["intake_id"]))
                connection.commit()
            finally:
                connection.close()
            raised = escalation.escalate_stuck_ideas(self.store)

        self.assertEqual([notice.kind for notice in raised], ["idea_stuck"])
        self.assertEqual(raised[0].severity, "attention",
                         "it may still start on its own, so it is not blocking")
        self.assertIn("529", raised[0].detail)
        self.assertIn("retrying", raised[0].subject)
        self.assertEqual(self.store.queued_triggers("acme", 5)[0]["attempts"], 0,
                         "telling somebody must not also give up on it")

    def test_an_idea_stays_listed_until_its_run_is_actually_visible(self):
        """Intake marks a trigger `started` immediately; the read model only catches up at
        the end of a sweep. In between, the idea must still be on screen somewhere."""
        asked = self.service.submit_idea(
            self.principal(), {"goal": "Something that takes a while to plan."}, "idea-slow")
        run_id = self.make_run("run-slow-work")
        self.store.settle_trigger("acme", asked["intake_id"], "started", run_id, None)
        self.assertEqual([item["run_id"] for item in self.service.ideas(self.principal())],
                         [run_id], "started but unmirrored work is still the operator's")

        from runtime import projection
        projection.project_run(self.store, run_id)
        self.assertEqual(self.service.ideas(self.principal()), [],
                         "once the run is visible the idea stops being listed twice")

    # --- reading what came out ------------------------------------------------

    def make_run(self, run_id: str, org: str | None = "acme") -> str:
        """A real run through `create_run`, so its log hashes like every other run's."""
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": f"produce something for {run_id}",
                    "max_cycles": 20,
                    "steps": [{"id": "s1", "owner": "cto-engineering", "action": "internal_write",
                               "depends_on": [], "max_attempts": 2}]}
        path = Path(self._tmp.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        arguments = {"workflow": str(path), "run_id": run_id, "actor": "chief-of-staff",
                     "request_id": f"create-{run_id}", "spend": 0.0}
        if org is not None:
            arguments["org"] = org
        with redirect_stdout(io.StringIO()):
            self.core.create_run(argparse.Namespace(**arguments))
        return run_id

    def complete_step(self, run_id: str, text: str) -> None:
        proof = self.core.RUNS / f"{run_id}.s1.evidence"
        proof.write_text(text, encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.core.request_step(argparse.Namespace(
                run_id=run_id, step="s1", actor="cto-engineering", holder="driver",
                request_id=f"req-{run_id}"))
            self.core.complete(argparse.Namespace(
                run_id=run_id, step="s1", evidence=str(proof), holder="driver",
                actor="cto-engineering", claim_token=None, spend=0.0,
                revision=self.core.read_events(run_id)[-1]["workflow_revision"],
                request_id=f"done-{run_id}"))

    def test_a_run_reports_each_step_with_what_it_produced(self):
        run_id = self.make_run("run-out-one")
        self.complete_step(run_id, "ENGINEERING NOTE - the deliverable")
        result = self.service.run_output(self.principal(), run_id)
        self.assertEqual(result["run_id"], run_id)
        self.assertEqual(len(result["steps"]), 1)
        step = result["steps"][0]
        self.assertEqual((step["step"], step["status"], step["owner"]),
                         ("s1", "completed", "cto-engineering"))
        self.assertIn("the deliverable", step["output"])

    def test_a_step_that_has_produced_nothing_yet_reports_no_output_not_an_error(self):
        run_id = self.make_run("run-out-empty")
        step = self.service.run_output(self.principal(), run_id)["steps"][0]
        self.assertEqual(step["status"], "ready")
        self.assertEqual(step["output"], "")

    def test_another_organizations_run_answers_exactly_like_one_that_does_not_exist(self):
        run_id = self.make_run("run-out-other", org="other")
        with self.assertRaises(ServiceError) as theirs:
            self.service.run_output(self.principal(), run_id)
        with self.assertRaises(ServiceError) as absent:
            self.service.run_output(self.principal(), "run-never-existed")
        self.assertEqual(str(theirs.exception).replace(run_id, "X"),
                         str(absent.exception).replace("run-never-existed", "X"))

    def test_a_run_with_no_organization_belongs_to_the_default_one(self):
        """`projection.project_run` reads a missing `org_id` as the default organization, so
        the read model lists these runs and the console offers a Stop button for them. Every
        verb must agree, or the console shows a run that answers 'unknown run' the moment
        anybody acts on it -- which is what it did.

        `create_run` always stamps an organization now, so the only runs in this shape are
        ones written before the field existed. The rule is asserted against that state
        directly rather than by forging a log, whose hash chain would refuse it.
        """
        legacy = {"run_id": "run-out-legacy", "run_status": "active", "goal": "g", "steps": {}}
        self.assertNotIn("org_id", legacy)
        with unittest.mock.patch.object(self.core, "read_events", return_value=[legacy]):
            state = self.service._run_state(
                self.principal("owner", self.core.DEFAULT_ORG), "run-out-legacy")
            self.assertIs(state, legacy)
            with self.assertRaises(ServiceError):
                self.service._run_state(self.principal("owner", "other"), "run-out-legacy")

    def test_an_evidence_reference_cannot_escape_the_runs_directory(self):
        """The path is written by an agent, so it is data, never a path to open blindly."""
        self.assertIn("outside the runs directory",
                      MyOrgService._evidence_text("../../../Windows/win.ini"))
        self.assertIn("outside the runs directory",
                      MyOrgService._evidence_text("/etc/passwd"))
        self.assertEqual(MyOrgService._evidence_text(None), "")
        # Inside the runs directory but absent: a different answer, and not a refusal.
        self.assertIn("missing",
                      MyOrgService._evidence_text(str(self.core.RUNS / "not-there.evidence")))

    def test_a_large_evidence_file_is_truncated_rather_than_returned_whole(self):
        run_id = self.make_run("run-out-big")
        self.complete_step(run_id, "z" * (MyOrgService.MAX_EVIDENCE_BYTES + 4096))
        step = self.service.run_output(self.principal(), run_id)["steps"][0]
        self.assertTrue(step["output"].endswith("[truncated]"))
        self.assertLessEqual(len(step["output"]), MyOrgService.MAX_EVIDENCE_BYTES + 32)


if __name__ == "__main__":
    unittest.main()
