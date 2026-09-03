from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"


class ApprovalsTest(unittest.TestCase):
    """Exercises the human gate on real runs, driven to the point of decision."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("MYORG_RUNS_DIR")
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        self.addCleanup(self._restore)

        from runtime import approval_server, approvals, company_runtime, executor
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.approvals = importlib.reload(approvals)
        self.server = importlib.reload(approval_server)
        for module in (company_runtime, executor, approvals, approval_server):
            self.addCleanup(lambda m=module: importlib.reload(m))

        self.logs: list[str] = []
        self.addCleanup(self.clear_evidence)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("gate-*.evidence"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("MYORG_RUNS_DIR", None)
        else:
            os.environ["MYORG_RUNS_DIR"] = self._previous

    def park(self, run_id: str, workflow: Path = WORKFLOW):
        """Drive a run until it is sitting on a human."""
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(workflow), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}"))
        self.executor.advance(run_id, self.executor.StubBackend(), log=self.logs.append)

    # --- what is waiting -----------------------------------------------------------

    def test_a_parked_step_shows_up_with_enough_context_to_decide(self):
        self.park("gate-one")
        waiting = self.approvals.pending()

        self.assertEqual(len(waiting), 1)
        decision = waiting[0]
        self.assertEqual((decision.run_id, decision.step_id), ("gate-one", "release-output"))
        self.assertEqual(decision.risk, "yellow")
        self.assertTrue(decision.actionable)
        self.assertIn("cannot be undone", decision.reason)
        # The upstream work is attached, so the decision is not taken blind.
        self.assertTrue(decision.context)
        self.assertIn("validate-output", decision.context[0][0])

    def test_nothing_waiting_is_reported_as_nothing_waiting(self):
        self.assertEqual(self.approvals.pending(), [])
        self.assertIn("Nothing is waiting", self.approvals.render([]))

    def test_decisions_from_every_run_are_gathered(self):
        self.park("gate-two")
        self.park("gate-three")
        self.assertEqual({d.run_id for d in self.approvals.pending()},
                         {"gate-two", "gate-three"})

    def test_a_red_step_is_handed_back_not_offered_for_approval(self):
        import json
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        for step in workflow["steps"]:
            if step["action"] == "publish":
                step["action"] = "permanent_delete"
        red = Path(self._tmp.name) / "red.json"
        red.write_text(json.dumps(workflow), encoding="utf-8")

        self.park("gate-red", red)
        decision = self.approvals.pending()[0]
        self.assertEqual(decision.status, "blocked_human")
        self.assertFalse(decision.actionable)
        self.assertIn("Never automated", decision.reason)

    # --- deciding ------------------------------------------------------------------

    def test_approving_lets_the_run_continue_and_records_who_and_why(self):
        self.park("gate-yes")
        self.approvals.decide("gate-yes", "release-output", True, "dipuraj", "evidence read")

        state = self.executor.current_state("gate-yes")
        step = state["steps"]["release-output"]
        self.assertEqual(step["status"], "in_progress")
        self.assertEqual(step["approver"], "dipuraj")
        self.assertEqual(step["approval_ref"], "evidence read")
        self.assertEqual(self.approvals.pending(), [])

    def test_rejecting_ends_the_run(self):
        self.park("gate-no")
        self.approvals.decide("gate-no", "release-output", False, "dipuraj", "not ready")
        self.assertEqual(self.executor.current_state("gate-no")["run_status"], "rejected")

    def test_a_decision_must_carry_a_name_and_a_reason(self):
        self.park("gate-anon")
        with self.assertRaises(SystemExit):
            self.approvals.decide("gate-anon", "release-output", True, "  ", "why")
        with self.assertRaises(SystemExit):
            self.approvals.decide("gate-anon", "release-output", True, "dipuraj", " ")
        # Still waiting -- neither attempt changed anything.
        self.assertEqual(len(self.approvals.pending()), 1)

    def test_the_driver_cannot_approve_on_its_own(self):
        """The whole point: driving a parked run must not clear the gate."""
        self.park("gate-firm")
        self.executor.advance("gate-firm", self.executor.StubBackend(), log=self.logs.append)
        self.assertEqual(len(self.approvals.pending()), 1)
        self.assertEqual(
            self.executor.current_state("gate-firm")["steps"]["release-output"]["status"],
            "awaiting_approval")

    # --- order: decisions must be offered in the sequence they should be taken -----

    def test_a_handed_back_red_step_is_offered_before_ordinary_approvals(self):
        import json
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        for step in workflow["steps"]:
            if step["action"] == "publish":
                step["action"] = "permanent_delete"
        red = Path(self._tmp.name) / "red3.json"
        red.write_text(json.dumps(workflow), encoding="utf-8")

        self.park("gate-ordinary")
        self.park("gate-stopper", red)
        order = self.approvals.pending()
        self.assertEqual(order[0].run_id, "gate-stopper")
        self.assertFalse(order[0].actionable)

    def test_an_earlier_step_is_offered_before_a_later_one(self):
        import json
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        # Two gates on the same chain: one early, one at the end.
        workflow["steps"][1]["action"] = "publish"
        two = Path(self._tmp.name) / "two-gates.json"
        two.write_text(json.dumps(workflow), encoding="utf-8")

        self.park("gate-chain", two)
        order = [d.step_id for d in self.approvals.pending()]
        self.assertEqual(order[0], "produce-output")
        self.assertLess(order.index("produce-output"), len(order))
        # The earlier gate is the one holding up the rest of the chain.
        first = self.approvals.pending()[0]
        self.assertGreater(first.unblocks, 0)
        self.assertIn("cannot start until you decide", first.impact)

    def test_a_decision_that_blocks_more_work_is_offered_first(self):
        self.park("gate-x")
        self.park("gate-y")
        decisions = self.approvals.pending()
        # Same shape, so ties fall back to a stable run order rather than randomness.
        self.assertEqual([d.run_id for d in decisions], ["gate-x", "gate-y"])
        self.assertEqual(decisions, self.approvals.pending())

    def test_the_last_step_says_nothing_is_waiting_on_it(self):
        self.park("gate-last")
        self.assertEqual(self.approvals.pending()[0].unblocks, 0)
        self.assertIn("Nothing else is waiting", self.approvals.pending()[0].impact)

    def test_the_page_numbers_the_decisions_in_order(self):
        self.park("gate-n1")
        self.park("gate-n2")
        markup = self.server.page(self.approvals.pending())
        self.assertIn("1 of 2", markup)
        self.assertIn("2 of 2", markup)
        self.assertIn("in the order they should be decided", markup)

    # --- the page ------------------------------------------------------------------

    def test_the_page_shows_the_waiting_work_and_a_way_to_decide(self):
        self.allow_local_decisions()  # the form exists only on the deprecated path (B-09)
        self.park("gate-page")
        markup = self.server.page(self.approvals.pending())
        self.assertIn("release-output", markup)
        self.assertIn('name="approver"', markup)
        self.assertIn('value="approve"', markup)
        self.assertIn('value="reject"', markup)

    def test_the_empty_page_says_so_plainly(self):
        markup = self.server.page([])
        self.assertIn("Nothing is waiting on you", markup)
        self.assertNotIn('value="approve"', markup)

    def test_a_red_step_gets_no_approve_button(self):
        import json
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        for step in workflow["steps"]:
            if step["action"] == "publish":
                step["action"] = "permanent_delete"
        red = Path(self._tmp.name) / "red2.json"
        red.write_text(json.dumps(workflow), encoding="utf-8")

        self.park("gate-redui", red)
        markup = self.server.page(self.approvals.pending())
        self.assertNotIn('value="approve"', markup)
        self.assertIn("Do this yourself", markup)

    def test_brief_text_is_escaped_not_injected(self):
        """The brief is model output rendered into HTML -- it must never be trusted."""
        from runtime.briefing import Brief
        self.park("gate-escape")
        decision = self.approvals.pending()[0]
        hostile = type(decision)(**{**decision.__dict__, "brief": Brief(
            ask="<script>alert(1)</script>",
            if_yes="<img src=x onerror=alert(2)>",
            findings=("<b>bold</b>",),
            watch="</style><script>alert(3)</script>",
            recommend="APPROVE - <script>alert(4)</script>")})
        markup = self.server.page([hostile])

        for payload in ("<script>alert(1)</script>", "<img src=x onerror=alert(2)>",
                        "<b>bold</b>", "<script>alert(3)</script>",
                        "<script>alert(4)</script>"):
            self.assertNotIn(payload, markup)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", markup)

    def test_run_and_step_identifiers_are_escaped(self):
        self.park("gate-escape2")
        decision = self.approvals.pending()[0]
        hostile = type(decision)(**{**decision.__dict__,
                                    "owner": "<script>x</script>",
                                    "action": "<script>y</script>"})
        markup = self.server.page([hostile])
        self.assertNotIn("<script>x</script>", markup)
        self.assertNotIn("<script>y</script>", markup)

    def allow_local_decisions(self) -> None:
        import os
        os.environ[self.server.LOCAL_STEP_DECISIONS_ENV] = "1"
        self.addCleanup(os.environ.pop, self.server.LOCAL_STEP_DECISIONS_ENV, None)

    def test_step_decisions_are_off_here_by_default_and_point_to_the_control_center(self):
        """B-09: this loopback page has no identity, role, org scope or required reason,
        so it no longer decides steps unless an operator turns the deprecated path on."""
        import os
        os.environ.pop(self.server.LOCAL_STEP_DECISIONS_ENV, None)
        self.park("gate-off")
        markup = self.server.page(self.approvals.pending())
        self.assertNotIn('action="/decide"', markup)
        self.assertIn("Control Center", markup)
        flash = self.server.apply_decision({
            "run_id": "gate-off", "step": "release-output", "verdict": "approve",
            "approver": "dipuraj", "note": "looks right"})
        self.assertIn("Not recorded", flash)
        self.assertIn("Control Center", flash)
        self.assertEqual(len(self.approvals.pending()), 1)

    def test_a_posted_decision_is_applied_and_reported(self):
        self.allow_local_decisions()
        self.park("gate-post")
        self.assertIn('action="/decide"', self.server.page(self.approvals.pending()))
        flash = self.server.apply_decision({
            "run_id": "gate-post", "step": "release-output", "verdict": "approve",
            "approver": "dipuraj", "note": "looks right"})
        self.assertIn("Approved", flash)
        self.assertEqual(self.approvals.pending(), [])

    def test_a_bad_posted_decision_is_refused_with_a_readable_message(self):
        self.allow_local_decisions()
        self.park("gate-badpost")
        flash = self.server.apply_decision({
            "run_id": "gate-badpost", "step": "release-output", "verdict": "approve",
            "approver": "", "note": "x"})
        self.assertIn("Not recorded", flash)
        self.assertEqual(len(self.approvals.pending()), 1)


if __name__ == "__main__":
    unittest.main()
