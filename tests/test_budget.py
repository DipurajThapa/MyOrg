"""What a run is allowed to spend, and what happens when it has spent it.

A-01/A-05/REC-11. Every other control in this runtime fails safe: a grader that cannot run
parks the step, an audit log that cannot be written stops the gate, an outward call with an
unknown outcome is never retried. Spend was the exception -- it failed *expensive*, and once
the company started running on schedules and webhooks it failed expensive with nobody at the
keyboard.

Measured before it was built (docs/ARCHITECTURE-OPPORTUNITIES-2026-09-01.md §6.1): a graded
step costs about $0.80 warm and about $2.80 cold, so the observed end-to-end run -- one plan,
two steps, three retries of one -- came to $5-7 and produced nothing, because no CRM was
authorized. A full trigger queue is $250-350 of that.
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


class BudgetTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        keys = ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", "MYORG_RUN_CEILING_USD")
        self._previous = {k: os.environ.get(k) for k in keys}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._tmp.name) / "_audit-log.jsonl")

        from runtime import company_runtime, executor
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        for module in (company_runtime, executor):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.addCleanup(self.clear_evidence)
        self.logs: list[str] = []

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("bud-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        return argparse.Namespace(**fields)

    def make_run(self, run_id: str, steps: int = 2, max_cycles: int = 12) -> None:
        workflow = {
            "version": 1, "id": f"wf-{run_id}", "goal": f"spend study {run_id}",
            "max_cycles": max_cycles,
            "steps": [{"id": f"s{n}", "owner": "cmo-marketing", "action": "draft",
                       "depends_on": [], "max_attempts": 3} for n in range(1, steps + 1)],
        }
        path = Path(self._tmp.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.executor.quietly(self.core.create_run, self.ns(
            workflow=str(path), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org="default"))

    def state(self, run_id: str) -> dict:
        return self.core.read_events(run_id)[-1]


class CostingBackend:
    """A backend whose every answer costs a known amount."""

    def __init__(self, cost: float, verdict: str = "VERDICT: MEETS"):
        self.cost = cost
        self.verdict = verdict
        self.calls = 0

    def __call__(self, request):
        from runtime.backends import Output
        self.calls += 1
        if getattr(request, "kind", "work") == "grade":
            return Output(self.verdict + "\n", self.cost)
        return Output(f"Deliverable for {request.step_id}. " * 20 + "\n", self.cost)


class SpendRecordingTest(BudgetTestBase):
    def test_a_completed_step_records_what_it_cost(self) -> None:
        self.make_run("bud-record", steps=1)
        self.executor.advance("bud-record", CostingBackend(0.25), log=self.logs.append)
        state = self.state("bud-record")
        self.assertAlmostEqual(state["steps"]["s1"]["spend_usd"], 0.25, places=4)
        self.assertAlmostEqual(state["spend_usd"], 0.25, places=4)

    def test_spend_accumulates_across_steps(self) -> None:
        self.make_run("bud-sum", steps=2)
        self.executor.advance("bud-sum", CostingBackend(0.30), log=self.logs.append)
        self.assertAlmostEqual(self.state("bud-sum")["spend_usd"], 0.60, places=4)

    def test_a_rejected_attempt_is_still_paid_for(self) -> None:
        """The expensive path in the observed run was three rejected attempts. A spend
        figure that only counted successes would have shown almost none of it."""
        self.make_run("bud-reject", steps=1)

        class Empty(CostingBackend):
            def __call__(self, request):
                from runtime.backends import Output
                self.calls += 1
                return Output("no.\n", self.cost)   # fails the structural gate

        self.executor.advance("bud-reject", Empty(0.40), log=self.logs.append)
        self.assertGreater(self.state("bud-reject")["spend_usd"], 0.0)

    def test_a_free_backend_records_nothing(self) -> None:
        self.make_run("bud-free", steps=1)
        self.executor.advance("bud-free", self.executor.StubBackend(), log=self.logs.append)
        self.assertEqual(self.state("bud-free").get("spend_usd", 0.0), 0.0)

    def test_recording_costs_no_extra_cycles(self) -> None:
        """Spend rides on the transition the dispatch was making anyway. A mutation of its
        own would have charged every step an extra cycle against the planner's budget."""
        self.make_run("bud-cycles", steps=1)
        self.executor.advance("bud-cycles", CostingBackend(0.10), log=self.logs.append)
        priced = self.state("bud-cycles")["cycle_count"]
        self.make_run("bud-cycles-free", steps=1)
        self.executor.advance("bud-cycles-free", self.executor.StubBackend(), log=self.logs.append)
        self.assertEqual(priced, self.state("bud-cycles-free")["cycle_count"])


class CeilingTest(BudgetTestBase):
    def test_a_run_over_its_ceiling_parks_instead_of_dispatching(self) -> None:
        os.environ["MYORG_RUN_CEILING_USD"] = "0.50"
        self.make_run("bud-stop", steps=2)
        backend = CostingBackend(0.60)
        self.executor.advance("bud-stop", backend, log=self.logs.append)
        state = self.state("bud-stop")
        self.assertEqual(state["steps"]["s1"]["status"], "completed")
        self.assertEqual(state["steps"]["s2"]["status"], "awaiting_approval")
        self.assertIn("ceiling", state["steps"]["s2"]["held_reason"])
        self.assertEqual(backend.calls, 1, "the second step must not have been dispatched")

    def test_the_parked_step_says_what_was_spent_and_what_the_limit_was(self) -> None:
        os.environ["MYORG_RUN_CEILING_USD"] = "0.50"
        self.make_run("bud-why", steps=2)
        self.executor.advance("bud-why", CostingBackend(0.60), log=self.logs.append)
        reason = self.state("bud-why")["steps"]["s2"]["held_reason"]
        self.assertIn("$0.60", reason)
        self.assertIn("$0.50", reason)

    def test_approving_a_budget_stop_buys_the_work_rather_than_accepting_it(self) -> None:
        """A step parked on the ceiling was never dispatched, so its "evidence" is the
        budget notice the runtime wrote. Completing with that handed a checker a receipt to
        certify as research -- a live checker refused twice and the review limit ended the
        run. Approval must send the step back to be *done*, not finish it."""
        os.environ["MYORG_RUN_CEILING_USD"] = "0.50"
        self.make_run("bud-buys", steps=2)
        backend = CostingBackend(0.60)
        self.executor.advance("bud-buys", backend, log=self.logs.append)
        held = self.state("bud-buys")["steps"]["s2"]
        self.assertEqual(held["status"], "awaiting_approval")
        self.assertEqual(held["held_kind"], "budget")

        self.executor.quietly(self.core.approve, self.ns(
            run_id="bud-buys", step="s2", approver="dipuraj",
            approval_ref="worth paying for", request_id="bud-buys-1"))
        step = self.state("bud-buys")["steps"]["s2"]
        self.assertEqual(step["status"], "ready", "it must be done, not completed")
        self.assertEqual(step["held_evidence"], "",
                         "the notice must not survive to be submitted as a deliverable")

        # And the run keeps going on the same ceiling, instead of parking on every step.
        self.executor.advance("bud-buys", backend, log=self.logs.append)
        final = self.state("bud-buys")["steps"]["s2"]
        self.assertEqual(final["status"], "completed")
        self.assertGreater(backend.calls, 1, "the work itself must have been dispatched")

    def test_a_hold_for_a_broken_control_still_accepts_the_work_it_kept(self) -> None:
        """The other half: when a gate could not run there *is* a deliverable, and approving
        it must still complete the step without redoing the work."""
        self.make_run("bud-ungraded", steps=1)
        self.executor.quietly(self.core.request_step, self.ns(
            run_id="bud-ungraded", step="s1", actor="cmo-marketing",
            holder="driver", request_id="ug-claim"))
        proof = self.executor.write_evidence("bud-ungraded", "s1", "the real deliverable")
        self.executor.quietly(self.core.hold, self.ns(
            run_id="bud-ungraded", step="s1", actor="cmo-marketing", evidence=proof,
            reason="grader unavailable", claim_token=None, spend=0.0, request_id="ug-hold"))
        self.assertEqual(self.state("bud-ungraded")["steps"]["s1"]["held_kind"], "ungraded")
        self.executor.quietly(self.core.approve, self.ns(
            run_id="bud-ungraded", step="s1", approver="dipuraj",
            approval_ref="read it myself", request_id="ug-approve"))
        step = self.state("bud-ungraded")["steps"]["s1"]
        self.assertEqual(step["status"], "in_progress")
        self.assertEqual(step["held_evidence"].replace("\\", "/"), proof.replace("\\", "/"),
                         "the work it kept is still there")

    def test_approving_the_parked_step_lets_the_run_continue(self) -> None:
        """The ceiling reuses `hold`, so it inherits VAL-07's resume path -- which is why
        the cost budget needed no extension command of its own."""
        os.environ["MYORG_RUN_CEILING_USD"] = "0.50"
        self.make_run("bud-resume", steps=2)
        backend = CostingBackend(0.60)
        self.executor.advance("bud-resume", backend, log=self.logs.append)
        self.executor.quietly(self.core.approve, self.ns(
            run_id="bud-resume", step="s2", approver="dipuraj",
            approval_ref="worth paying for", request_id="bud-approve-1"))
        os.environ["MYORG_RUN_CEILING_USD"] = "10"
        self.executor.advance("bud-resume", backend, log=self.logs.append)
        self.assertEqual(self.state("bud-resume")["steps"]["s2"]["status"], "completed")

    def test_a_zero_ceiling_disables_the_control(self) -> None:
        os.environ["MYORG_RUN_CEILING_USD"] = "0"
        self.make_run("bud-off", steps=2)
        self.executor.advance("bud-off", CostingBackend(9.0), log=self.logs.append)
        self.assertEqual(self.state("bud-off")["steps"]["s2"]["status"], "completed")

    def test_an_unreadable_ceiling_falls_back_rather_than_halting(self) -> None:
        os.environ["MYORG_RUN_CEILING_USD"] = "not a number"
        self.assertEqual(self.executor.run_ceiling_usd(), 5.0)

    def test_an_unreadable_spend_figure_lets_the_work_through(self) -> None:
        """Fails OPEN, deliberately, and the opposite way round from the grading gate. A
        grader that cannot run risks shipping bad work; a spend counter that cannot run
        risks only overspending, which the alert catches. A broken counter must never be
        able to halt the company."""
        os.environ["MYORG_RUN_CEILING_USD"] = "0.01"
        broken = {"spend_usd": "not a number"}
        self.assertFalse(self.executor.over_budget("bud-x", "s1", "cmo-marketing",
                                                   broken, self.logs.append))


class ExtendBudgetTest(BudgetTestBase):
    """REC-11: exhausting the cycle budget stranded finished work, and re-driving was a
    silent no-op that looked exactly like success."""

    def exhaust(self, run_id: str) -> None:
        """Drive until the cycle budget runs out. The driver raises when it does -- that is
        the pre-existing behaviour REC-11 is about, not something these tests introduce."""
        from runtime.backends import ExecutorError
        self.make_run(run_id, steps=3, max_cycles=4)
        try:
            self.executor.advance(run_id, self.executor.StubBackend(), log=self.logs.append)
        except ExecutorError:
            pass

    def test_a_run_out_of_cycles_can_be_given_more_and_resumes(self) -> None:
        self.exhaust("bud-extend")
        self.assertEqual(self.state("bud-extend")["run_status"], "blocked_cycle_limit")
        self.executor.quietly(self.core.extend_budget, self.ns(
            run_id="bud-extend", cycles=10, approver="dipuraj", request_id="bud-ext-1"))
        state = self.state("bud-extend")
        self.assertEqual(state["run_status"], "active")
        self.assertEqual(state["max_cycles"], 14)

    def test_extending_never_reruns_work_that_was_already_finished(self) -> None:
        self.exhaust("bud-keep")
        done = [s for s, v in self.state("bud-keep")["steps"].items()
                if v["status"] == "completed"]
        self.assertTrue(done, "the run should have finished something before running out")
        self.executor.quietly(self.core.extend_budget, self.ns(
            run_id="bud-keep", cycles=10, approver="dipuraj", request_id="bud-ext-2"))
        after = self.state("bud-keep")["steps"]
        for step_id in done:
            self.assertEqual(after[step_id]["status"], "completed")
            self.assertEqual(after[step_id]["attempts"], 1)

    def test_extending_is_a_named_human_decision(self) -> None:
        self.exhaust("bud-who")
        with self.assertRaises(SystemExit):
            self.core.extend_budget(self.ns(run_id="bud-who", cycles=5, approver="  ",
                                            request_id="bud-ext-3"))

    def test_extending_is_recorded_in_the_audit_log(self) -> None:
        self.exhaust("bud-audit")
        self.executor.quietly(self.core.extend_budget, self.ns(
            run_id="bud-audit", cycles=5, approver="dipuraj", request_id="bud-ext-4"))
        from runtime import audit
        entries = [json.loads(line) for line in audit.read_lines()]
        extended = [e for e in entries if e.get("action") == "run.budget_extended"]
        self.assertEqual(len(extended), 1)
        self.assertEqual(extended[0]["actor"], "dipuraj")
        self.assertEqual(audit.verify(), [])

    def test_extending_twice_with_one_request_id_is_a_replay(self) -> None:
        self.exhaust("bud-twice")
        for _ in range(2):
            self.executor.quietly(self.core.extend_budget, self.ns(
                run_id="bud-twice", cycles=5, approver="dipuraj", request_id="bud-ext-5"))
        self.assertEqual(self.state("bud-twice")["max_cycles"], 9)

    def test_a_healthy_run_cannot_be_extended(self) -> None:
        self.make_run("bud-healthy", steps=1)
        with self.assertRaises(SystemExit):
            self.core.extend_budget(self.ns(run_id="bud-healthy", cycles=5,
                                            approver="dipuraj", request_id="bud-ext-6"))

    def test_redriving_a_terminal_run_says_why_rather_than_going_quiet(self) -> None:
        """The half of REC-11 that made an operator retry look like success."""
        self.exhaust("bud-quiet")
        self.logs.clear()
        self.executor.advance("bud-quiet", self.executor.StubBackend(), log=self.logs.append)
        said = " ".join(self.logs)
        self.assertIn("blocked_cycle_limit", said)
        self.assertIn("extend-budget", said)


if __name__ == "__main__":
    unittest.main()
