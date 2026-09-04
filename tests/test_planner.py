from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from runtime import company_runtime as core
from runtime.executor import ExecutorError
from runtime.planner import (PlanRequest, StubPlannerBackend, actions_by_risk,
                             departments, enforce_budget, extract_json, plan)

GOAL = "Cut onboarding time for new enterprise customers"


class PlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logs: list[str] = []

    # --- the plan must be runnable, not merely plausible --------------------------

    def test_a_plan_passes_the_real_runtime_validator(self):
        workflow = plan(GOAL, "onboarding-speed", StubPlannerBackend(), log=self.logs.append)
        core.validate_workflow(workflow)  # raises SystemExit if the runtime would reject
        self.assertEqual(workflow["id"], "onboarding-speed")
        self.assertTrue(workflow["steps"])

    def test_the_plan_can_actually_start_a_run(self):
        workflow = plan(GOAL, "onboarding-run", StubPlannerBackend(), log=self.logs.append)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(workflow), encoding="utf-8")
            core.validate_workflow(json.loads(path.read_text(encoding="utf-8")))

    def test_a_broken_plan_is_sent_back_with_the_runtime_s_own_errors(self):
        seen: list = []
        good = StubPlannerBackend()

        def backend(request):
            seen.append(request)
            if len(seen) == 1:
                # Unknown owner: exactly the kind of thing the validator catches.
                return json.dumps({
                    "version": 1, "id": request.workflow_id, "goal": request.goal,
                    "max_cycles": 8,
                    "steps": [{"id": "do-it", "owner": "department-of-magic",
                               "action": "analyze", "depends_on": [], "max_attempts": 2}],
                })
            return good(request)

        workflow = plan(GOAL, "repaired", backend, log=self.logs.append)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].feedback, "")
        self.assertIn("department-of-magic", seen[1].feedback)
        self.assertIn("unknown owner", seen[1].feedback)
        self.assertIn("Fix exactly these errors", seen[1].prompt())
        core.validate_workflow(workflow)

    def test_giving_up_is_bounded_and_says_why(self):
        def always_bad(request):
            return json.dumps({"version": 1, "id": request.workflow_id, "goal": "x",
                               "max_cycles": 4, "steps": [{"id": "BAD ID"}]})

        with self.assertRaises(ExecutorError) as caught:
            plan(GOAL, "hopeless", always_bad, attempts=2, log=self.logs.append)
        self.assertIn("no valid plan after 2 attempts", str(caught.exception))

    def test_prose_around_the_json_is_tolerated(self):
        self.assertEqual(extract_json('Sure! ```json\n{"a": 1}\n``` done'), {"a": 1})
        with self.assertRaises(ExecutorError):
            extract_json("I would rather not.")
        with self.assertRaises(ExecutorError):
            extract_json("{not json at all}")

    def test_a_plan_gets_a_cycle_budget_it_can_finish_in(self):
        workflow = {"steps": [{"id": f"s{n}"} for n in range(6)], "max_cycles": 2}
        enforce_budget(workflow)
        self.assertGreaterEqual(workflow["max_cycles"], len(workflow["steps"]) * 2)
        # A generous budget the planner chose itself is left alone.
        roomy = {"steps": [{"id": "a"}], "max_cycles": 50}
        enforce_budget(roomy)
        self.assertEqual(roomy["max_cycles"], 50)

    # --- the planner must be told the truth about the company ---------------------

    def test_the_planner_is_given_the_real_departments_and_actions(self):
        request = PlanRequest(agent="chief-of-staff", goal=GOAL, workflow_id="x",
                              brief="brief")
        rules = request.rules()
        for department in departments():
            self.assertIn(department, rules)
        self.assertIn("cto-engineering", rules)
        self.assertNotIn("department-of-magic", rules)

    def test_the_planner_is_told_which_actions_stop_for_a_human(self):
        rules = PlanRequest(agent="chief-of-staff", goal=GOAL, workflow_id="x",
                            brief="brief").rules()
        risks = actions_by_risk()
        self.assertIn("publish", risks["yellow"])
        self.assertIn("move_money", risks["red"])
        self.assertIn("stops for a human", rules)
        self.assertIn("never automated", rules)

    def test_departments_come_from_the_agent_files_not_a_hardcoded_list(self):
        self.assertEqual(len(departments()), 17)
        self.assertTrue(all(core.agent_exists(name) for name in departments()))

    def test_the_planner_is_told_to_write_criteria_that_can_be_met(self):
        """Three consecutive real runs died on the same shape of criterion -- 'every claim
        carries a dated source link' across ten products, failed on one missing price. The
        planner was never told what a step can actually do, so it asked for the impossible
        and the grader was right to refuse it every time."""
        from runtime.planner import PlanRequest, SEARCHERS
        rules = PlanRequest(agent="chief-of-staff", goal="g", workflow_id="w",
                            brief="b").rules()
        self.assertEqual(SEARCHERS, {"chief-knowledge-officer", "head-of-data"},
                         "the rule text must follow the real grants, not a hardcoded list")
        for role in SEARCHERS:
            self.assertIn(role, rules, "the planner must know who can search")
        self.assertIn("Never write 'every'", rules)
        self.assertIn("Put a number on", rules)
        self.assertIn("cannot be satisfied and cannot be graded", rules)

    def test_the_planner_budgets_attempts_for_grading_as_well_as_review(self):
        """Grading costs attempts before a checker ever sees the work, and the prompt has
        to say so. The floor is now enforced, so the prompt must quote the same number the
        validator refuses on -- a plan written to a rule the runtime does not hold, or a
        runtime holding one the prompt never states, wastes a repair round either way."""
        from runtime.planner import PlanRequest
        rules = PlanRequest(agent="chief-of-staff", goal="g", workflow_id="w",
                            brief="b").rules()
        self.assertIn("max_attempts at least max_review_cycles + 2", rules)
        self.assertIn("a rejected attempt is spent", rules)
        self.assertIn("the floor the runtime enforces", rules)
        self.assertNotIn("max_review_cycles + 1", rules,
                         "the old, looser floor must not survive anywhere in the prompt")

    def test_the_runtime_enforces_the_floor_the_prompt_asks_for(self):
        """This used to assert the opposite -- that validation demanded only viability,
        because raising the floor was a migration rather than a fix. The migration was
        done: the prompt had advised max_review_cycles + 2 for a while and models kept
        writing the schema minimum instead, and every large generated workflow on disk
        died of it, first research step out of attempts with 25 steps still pending behind
        it. A rule a model can read past is not a rule.
        """
        def floor_case(attempts: int) -> dict:
            return {"version": 1, "id": "wf-floor", "goal": "g", "max_cycles": 8,
                    "steps": [{"id": "s1", "owner": "cto-engineering",
                               "action": "internal_write", "depends_on": [],
                               "max_attempts": attempts, "checker": "cpo-product",
                               "max_review_cycles": 1}]}

        core.validate_workflow(floor_case(3))  # 1 review cycle + 2: accepted
        with self.assertRaises(SystemExit) as refused:
            core.validate_workflow(floor_case(2))  # + 1: no room for a grader rejection
        self.assertIn("max_review_cycles + 2", str(refused.exception),
                      "the refusal is planner repair feedback -- it must name the rule")

    def test_every_shipped_workflow_meets_the_floor(self):
        """The migration is only done if the files agree with the validator."""
        import glob, json
        shipped = sorted(glob.glob(str(ROOT / "runtime" / "workflows" / "*.json")))
        self.assertTrue(shipped, "no shipped workflows found to check")
        for path in shipped:
            with self.subTest(workflow=path), open(path, encoding="utf-8") as handle:
                core.validate_workflow(json.load(handle))

    def test_a_busy_server_is_not_treated_as_a_badly_written_plan(self):
        """Repair attempts exist to tell the model its JSON was wrong. Feeding a transport
        error back as feedback spent the whole repair budget inside one outage -- three
        calls into an already-overloaded server, then a permanent give-up."""
        from runtime.executor import ExecutorError
        from runtime.planner import plan
        calls = []

        def busy(request):
            calls.append(request.feedback)
            raise ExecutorError("claude exited 1: result=API Error: 529 Overloaded")

        with self.assertRaises(ExecutorError) as caught:
            plan("a goal", "wf-busy", busy, attempts=3, log=lambda _m: None)
        self.assertIn("529", str(caught.exception))
        self.assertEqual(len(calls), 1, "it must stop after the first busy answer")

    def test_a_malformed_answer_still_gets_its_repair_attempts(self):
        """The other half: a bad plan is exactly what the repair loop is for."""
        from runtime.executor import ExecutorError
        from runtime.planner import plan
        calls = []

        def rubbish(request):
            calls.append(request.feedback)
            return "not json at all"

        with self.assertRaises(ExecutorError):
            plan("a goal", "wf-rubbish", rubbish, attempts=3, log=lambda _m: None)
        self.assertEqual(len(calls), 3, "all three repair attempts are used")
        self.assertTrue(calls[1], "the second attempt is told what was wrong with the first")


if __name__ == "__main__":
    unittest.main()
