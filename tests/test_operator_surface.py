"""What an operator can find without reading source: how notices are delivered, how the
company is paused, and that a suspended organization really starts nothing.

NOTIFY-01 and B-03. Detection was built in cycle 2; delivery was documented in audit reports
only. A control nobody can find is not a control.
"""
from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NoticeDeliveryIsDiscoverableTest(unittest.TestCase):
    def test_every_operator_facing_entry_point_names_the_delivery_command(self):
        places = {
            "deploy/myorg.env.example": ROOT / "deploy" / "myorg.env.example",
            "deploy/myorg-scheduler.service": ROOT / "deploy" / "myorg-scheduler.service",
            "docs/OPERATIONS-RUNBOOK.md": ROOT / "docs" / "OPERATIONS-RUNBOOK.md",
            "README.md": ROOT / "README.md",
        }
        for name, path in places.items():
            self.assertIn("MYORG_NOTIFY_COMMAND", path.read_text(encoding="utf-8"), name)
        runbook = places["docs/OPERATIONS-RUNBOOK.md"].read_text(encoding="utf-8")
        self.assertIn("## Being told", runbook)
        self.assertIn("_outbox.jsonl", runbook)

    def test_the_scheduler_help_says_where_notices_go(self):
        from runtime import scheduler
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            scheduler.main(["--help"])
        self.assertIn("MYORG_NOTIFY_COMMAND", out.getvalue())

    def test_a_supervised_loop_warns_once_at_start_when_nobody_will_be_told(self):
        from runtime import scheduler
        from runtime.executor import StubBackend
        previous = os.environ.pop("MYORG_NOTIFY_COMMAND", None)
        self.addCleanup(lambda: os.environ.update({"MYORG_NOTIFY_COMMAND": previous})
                        if previous is not None else None)
        with tempfile.TemporaryDirectory() as runs:
            os.environ["MYORG_RUNS_DIR"] = runs
            self.addCleanup(os.environ.pop, "MYORG_RUNS_DIR", None)
            from runtime import company_runtime
            importlib.reload(company_runtime)
            self.addCleanup(lambda: importlib.reload(company_runtime))
            logs: list[str] = []
            scheduler.serve(StubBackend(), interval=1, max_passes=2, sleeper=lambda _s: None,
                            log=logs.append, stop_when_idle=False)
            warnings = [line for line in logs if "MYORG_NOTIFY_COMMAND" in line]
            self.assertEqual(len(warnings), 1, logs)
            self.assertIn("#being-told", warnings[0])
            # A hand-run sweep (`stop_when_idle`) is not unattended and does not nag.
            logs.clear()
            scheduler.serve(StubBackend(), interval=1, max_passes=1, sleeper=lambda _s: None,
                            log=logs.append, stop_when_idle=True)
            self.assertFalse([line for line in logs if "MYORG_NOTIFY_COMMAND" in line])


class SuspendedMeansSuspendedTest(unittest.TestCase):
    """The organization's `suspended` status already denied every token. It now also stops
    intake and refuses webhooks -- one concept for 'the company is paused', not two."""

    def setUp(self) -> None:
        from runtime.db import Store
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = {k: os.environ.get(k) for k in
                     ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", "MYORG_DB", "MYORG_ORG_ID")}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._tmp.name) / "_audit.jsonl")
        os.environ["MYORG_DB"] = str(Path(self._tmp.name) / "myorg.db")
        os.environ["MYORG_ORG_ID"] = "acme"
        from runtime import company_runtime, executor, scheduler, triggers
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.scheduler = importlib.reload(scheduler)
        self.triggers = triggers
        for module in (company_runtime, executor, scheduler):
            self.addCleanup(lambda m=module: importlib.reload(m))
        self.store = Store(os.environ["MYORG_DB"])
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.store.upsert_actor("acme", "chief", "human", "Chief", ["system-admin"])
        from datetime import timedelta
        self.store.create_schedule(
            "acme", "hourly", "interval", "Review the pipeline",
            triggers.stamp(triggers.utc_now() - timedelta(minutes=1)), "chief",
            "create-sched", "trace-1", interval_seconds=3600)
        self.logs: list[str] = []

    def _restore(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_status_reads_back_and_missing_is_not_active(self):
        self.assertEqual(self.store.organization_status("acme"), "active")
        self.assertEqual(self.store.organization_status("nobody"), "missing")
        self.store.set_organization_status("acme", "suspended")
        self.assertEqual(self.store.organization_status("acme"), "suspended")

    def make_run(self, run_id: str) -> None:
        workflow = ROOT / "runtime" / "workflows" / "manual-gold-run.json"
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(workflow), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org="acme"))

    def done(self, run_id: str) -> int:
        state = self.core.read_events(run_id)[-1]
        return sum(s["status"] == "completed" for s in state["steps"].values())

    def test_a_suspended_organization_starts_nothing_drives_nothing_and_still_watches(self):
        """Tenant off means off for the autonomous paths too -- not only for the paths that
        happen to authenticate. A ready run is left exactly where it was."""
        from runtime.planner import StubPlannerBackend
        from runtime.executor import StubBackend
        self.make_run("sus-moving")
        self.store.set_organization_status("acme", "suspended")

        result = self.scheduler.sweep(StubBackend(), log=self.logs.append,
                                      planner_backend=StubPlannerBackend())

        self.assertEqual(result.started, [], "intake must start nothing while suspended")
        self.assertNotIn("sus-moving", result.driven, "a suspended org's runs are not driven")
        self.assertIn("sus-moving", result.skipped)
        self.assertEqual(self.done("sus-moving"), 0)
        self.assertTrue(any("suspended" in line for line in self.logs))
        # The watchers still ran: the sweep projected and escalated as usual.
        self.assertTrue(self.store.runs("acme"), "projection still mirrors while suspended")
        # The schedule is still due -- it fires the moment the pause is lifted, and the
        # run picks up where it stopped.
        self.assertTrue(self.store.schedules("acme")[0]["enabled"])
        self.store.set_organization_status("acme", "active")
        resumed = self.scheduler.sweep(StubBackend(), log=self.logs.append,
                                       planner_backend=StubPlannerBackend())
        self.assertEqual(len(resumed.started), 1)
        self.assertIn("sus-moving", resumed.driven)
        self.assertEqual(self.done("sus-moving"), 3)
        for run_id in resumed.started:
            self.addCleanup(lambda r=run_id: (ROOT / "runtime" / "workflows" / f"{r}.json")
                            .unlink(missing_ok=True))

    def test_suspension_mid_pass_lets_the_dispatched_step_finish_and_stops_the_next(self):
        """The one in-flight semantics: a claim already dispatched records its own result;
        the *next* dispatch does not happen. No claim is broken, nothing is rolled back."""
        from runtime.executor import StubBackend
        store, test = self.store, self

        class SuspendsDuringFirstStep(StubBackend):
            fired = False

            def __call__(self, request):
                if request.kind == "work" and not self.fired:
                    self.fired = True
                    store.set_organization_status("acme", "suspended")  # another process
                return super().__call__(request)

        self.make_run("sus-flight")
        result = self.scheduler.sweep(SuspendsDuringFirstStep(), log=self.logs.append)
        self.assertIn("sus-flight", result.driven)
        state = self.core.read_events("sus-flight")[-1]
        self.assertEqual(state["steps"]["frame-goal"]["status"], "completed",
                         "the step in flight when suspension landed still completed")
        self.assertEqual(state["steps"]["produce-output"]["status"], "ready",
                         "the next step was ready and was not dispatched")
        self.assertTrue(any("not driven -- the organization is suspended" in line
                            for line in self.logs))
        test.assertEqual(state["run_status"], "active")

    def test_the_agent_api_offers_and_claims_nothing_while_suspended(self):
        import importlib
        from runtime import agent_api
        api = importlib.reload(agent_api)
        self.addCleanup(lambda: importlib.reload(agent_api))
        self.make_run("sus-api")
        self.assertEqual([w["run_id"] for w in api.open_work()], ["sus-api"])
        self.store.set_organization_status("acme", "suspended")
        self.assertEqual(api.open_work(), [])
        with self.assertRaises(api.ApiError) as refused:
            api.claim({"run_id": "sus-api", "step": "frame-goal", "agent": "chief-of-staff"})
        self.assertEqual(refused.exception.status, 409)
        self.assertIn("suspended", str(refused.exception))
        self.assertFalse(self.core.claim_is_live(
            self.core.read_events("sus-api")[-1]["steps"]["frame-goal"]))

    def test_the_metrics_say_so(self):
        from runtime.observability import RuntimeGauges
        gauges = RuntimeGauges(self.store, ttl_seconds=0)
        self.assertIn("myorg_org_suspended 0", gauges.render().decode())
        self.store.set_organization_status("acme", "suspended")
        self.assertIn("myorg_org_suspended 1", gauges.render().decode())
        alerts = (ROOT / "deploy" / "prometheus-alerts.yml").read_text(encoding="utf-8")
        self.assertIn("myorg_org_suspended", alerts)
        self.assertIn("myorg_spend_usd_total", alerts)  # B-05a

    def test_the_runbook_names_both_pause_levers(self):
        runbook = (ROOT / "docs" / "OPERATIONS-RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("## Pausing the company", runbook)
        self.assertIn("organization-status", runbook)
        self.assertIn("/v1/schedules/{id}/status", runbook)
        self.assertIn("## Stopping a run", runbook)


if __name__ == "__main__":
    unittest.main()
