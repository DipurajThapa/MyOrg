from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
