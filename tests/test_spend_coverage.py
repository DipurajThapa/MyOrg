"""Every model call the ceiling should see, it sees (B-04).

Measured before it was built (docs/EXECUTION-TRACKER.md §5.4): on a maker-checker RETURN
loop half of all calls were the checker's and none were charged; a triggered run's plan --
up to three calls -- was bought before the run existed and charged to nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests.test_budget import BudgetTestBase

ROOT = Path(__file__).resolve().parents[1]


class Priced:
    """A stub that answers like the stub backend and prices every call."""

    def __init__(self, price: float, returns: int = 0):
        from runtime.backends import Output, StubBackend
        self.price, self.returns, self.stub, self.Output = price, returns, StubBackend(), Output

    def __call__(self, request):
        if request.kind == "check" and self.returns > 0:
            self.returns -= 1
            return self.Output("VERDICT: RETURN\n- Needs a section on the risks first.\n",
                               cost_usd=self.price)
        return self.Output(self.stub(request), cost_usd=self.price)


class SpendCoverageTest(BudgetTestBase):
    def setUp(self) -> None:
        super().setUp()
        # A RETURN verdict proposes a lesson; keep it out of the company's real memory.
        import importlib
        from runtime import memory
        self._memory_dir = os.environ.get("MYORG_MEMORY_DIR")
        os.environ["MYORG_MEMORY_DIR"] = self._tmp.name
        importlib.reload(memory)
        self.addCleanup(lambda: importlib.reload(memory))
        self.addCleanup(lambda: os.environ.update({"MYORG_MEMORY_DIR": self._memory_dir})
                        if self._memory_dir is not None else os.environ.pop("MYORG_MEMORY_DIR", None))

    def make_checked_run(self, run_id: str) -> None:
        workflow = {"version": 1, "id": f"wf-{run_id}", "goal": "coverage", "max_cycles": 30,
                    "steps": [{"id": "s1", "owner": "cto-engineering", "checker": "coo-operations",
                               "max_review_cycles": 2, "action": "internal_write",
                               "depends_on": [], "max_attempts": 4}]}
        path = Path(self._tmp.name) / f"{run_id}.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.executor.quietly(self.core.create_run, self.ns(
            workflow=str(path), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org="default"))

    def test_the_checker_s_review_is_charged_every_cycle(self):
        self.make_checked_run("bud-check")
        self.executor.advance("bud-check", Priced(0.25, returns=2), log=self.logs.append)
        state = self.state("bud-check")
        self.assertEqual(state["run_status"], "completed")
        # 3 maker dispatches + 3 reviews, all at $0.25.
        self.assertAlmostEqual(state["spend_usd"], 6 * 0.25, places=6)
        self.assertAlmostEqual(state["steps"]["s1"]["spend_usd"], 6 * 0.25, places=6)

    def test_a_rejecting_checker_still_charges_its_review(self):
        self.make_checked_run("bud-reject")

        class Rejecting(Priced):
            def __call__(self, request):
                if request.kind == "check":
                    return self.Output("VERDICT: REJECT\n- Not salvageable.\n", cost_usd=self.price)
                return super().__call__(request)

        self.executor.advance("bud-reject", Rejecting(0.5), log=self.logs.append)
        state = self.state("bud-reject")
        self.assertEqual(state["run_status"], "rejected_by_checker")
        self.assertAlmostEqual(state["spend_usd"], 2 * 0.5, places=6)

    def test_the_plan_is_the_first_line_of_a_triggered_run_s_bill(self):
        from runtime import triggers
        from runtime.backends import Output
        from runtime.db import Store
        from runtime.planner import StubPlannerBackend

        class PricedPlanner:
            def __init__(self):
                self.calls = 0

            def __call__(self, request):
                self.calls += 1
                if self.calls == 1:
                    return Output("not json", cost_usd=0.4)  # one repair round
                return Output(StubPlannerBackend()(request), cost_usd=0.6)

        store = Store(Path(self._tmp.name) / "trig.db")
        store.migrate()
        store.bootstrap_organization("acme", "Acme")
        store.enqueue_trigger("acme", "in-bud-plan", "schedule", "hourly", "plan me")
        started = triggers.start_queued(store, "acme", PricedPlanner(), log=self.logs.append)
        self.assertEqual(len(started), 1)
        run_id = started[0]["run_id"]
        self.addCleanup(lambda: (ROOT / "runtime" / "workflows" / f"{run_id}.json")
                        .unlink(missing_ok=True))
        state = self.state(run_id)
        self.assertAlmostEqual(state["planning_spend_usd"], 1.0, places=6)
        self.assertAlmostEqual(state["spend_usd"], 1.0, places=6)

    def test_a_plan_alone_can_park_the_first_step(self):
        os.environ["MYORG_RUN_CEILING_USD"] = "0.5"
        self.make_run("bud-planned", steps=1)
        # Simulate a run seeded above the ceiling by planning cost.
        workflow = Path(self._tmp.name) / "bud-planned.wf.json"
        self.executor.quietly(self.core.create_run, self.ns(
            workflow=str(workflow), run_id="bud-planned-2", actor="chief-of-staff",
            request_id="create-bud-planned-2", org="default", spend=0.75))
        self.executor.advance("bud-planned-2", Priced(0.1), log=self.logs.append)
        step = self.state("bud-planned-2")["steps"]["s1"]
        self.assertEqual(step["status"], "awaiting_approval")
        self.assertIn("ceiling", step["held_reason"])
