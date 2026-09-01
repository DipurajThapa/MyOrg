from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"
MAKER_CHECKER = ROOT / "runtime" / "workflows" / "maker-checker-gold-run.json"


class SchedulingTest(unittest.TestCase):
    """Health and the sweep loop, on real runs, with no model calls."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("MYORG_RUNS_DIR")
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        self.addCleanup(self._restore)

        from runtime import company_runtime, executor, health, scheduler
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.health = importlib.reload(health)
        self.scheduler = importlib.reload(scheduler)
        for module in (company_runtime, executor, health, scheduler):
            self.addCleanup(lambda m=module: importlib.reload(m))

        self.logs: list[str] = []
        self.backend = self.executor.StubBackend()
        self.addCleanup(self.clear_evidence)

    def clear_evidence(self) -> None:
        for pattern in ("sch-*.evidence", "sch-*.brief"):
            for path in self.executor.EVIDENCE_DIR.glob(pattern):
                path.unlink(missing_ok=True)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("MYORG_RUNS_DIR", None)
        else:
            os.environ["MYORG_RUNS_DIR"] = self._previous

    def create(self, run_id: str, workflow: Path = WORKFLOW) -> None:
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(workflow), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}"))

    # --- health --------------------------------------------------------------------

    def test_a_fresh_run_is_running(self):
        self.create("sch-fresh")
        run = self.health.health("sch-fresh")
        self.assertEqual(run.state, self.health.RUNNING)
        self.assertEqual((run.done, run.total), (0, 4))
        self.assertFalse(run.needs_attention)

    def test_a_parked_run_says_it_is_waiting_on_you(self):
        self.create("sch-parked")
        self.executor.advance("sch-parked", self.backend, log=self.logs.append)
        run = self.health.health("sch-parked")
        self.assertEqual(run.state, self.health.WAITING)
        self.assertEqual(run.waiting_on, ("release-output",))
        self.assertTrue(run.needs_attention)
        self.assertEqual(run.percent, 75)

    def test_a_run_out_of_retries_is_failed_not_merely_blocked(self):
        def failing(request):
            raise self.executor.ExecutorError("down")

        self.create("sch-dead")
        self.executor.advance("sch-dead", failing, log=self.logs.append)
        run = self.health.health("sch-dead")
        self.assertEqual(run.state, self.health.FAILED)
        self.assertTrue(run.needs_attention)

    def test_a_quiet_run_with_work_left_is_reported_stalled(self):
        self.create("sch-stale")
        later = datetime.now(timezone.utc) + timedelta(
            minutes=self.health.STALLED_AFTER_MINUTES + 5)
        run = self.health.health("sch-stale", now=later)
        self.assertEqual(run.state, self.health.STALLED)
        self.assertGreaterEqual(run.idle_minutes, self.health.STALLED_AFTER_MINUTES)

    def test_trouble_is_listed_before_everything_else(self):
        self.create("sch-ok")
        self.create("sch-parked2")
        self.executor.advance("sch-parked2", self.backend, log=self.logs.append)

        def failing(request):
            raise self.executor.ExecutorError("down")

        self.create("sch-broken")
        self.executor.advance("sch-broken", failing, log=self.logs.append)

        states = [run.state for run in self.health.all_health()]
        self.assertEqual(states[0], self.health.FAILED)
        self.assertLess(states.index(self.health.WAITING),
                        states.index(self.health.RUNNING))

    def test_the_summary_flags_what_needs_attention(self):
        self.create("sch-render")
        self.executor.advance("sch-render", self.backend, log=self.logs.append)
        text = self.health.render(self.health.all_health())
        self.assertIn("sch-render", text)
        self.assertIn("1 need attention", text)
        self.assertIn("!", text)

    def test_no_runs_reads_cleanly(self):
        self.assertEqual(self.health.render([]), "No runs yet.")

    # --- the sweep -----------------------------------------------------------------

    def test_one_sweep_drives_every_run_that_can_move(self):
        self.create("sch-a")
        self.create("sch-b")
        result = self.scheduler.sweep(self.backend, log=self.logs.append)

        self.assertEqual(sorted(result.driven), ["sch-a", "sch-b"])
        for run_id in ("sch-a", "sch-b"):
            self.assertEqual(self.health.health(run_id).state, self.health.WAITING)

    def test_a_sweep_leaves_alone_what_is_waiting_on_a_human(self):
        self.create("sch-hold")
        self.executor.advance("sch-hold", self.backend, log=self.logs.append)
        result = self.scheduler.sweep(self.backend, log=self.logs.append)

        self.assertIn("sch-hold", result.skipped)
        self.assertEqual(result.driven, [])
        self.assertEqual(
            self.executor.current_state("sch-hold")["steps"]["release-output"]["status"],
            "awaiting_approval")

    def test_one_broken_run_does_not_stop_the_others(self):
        self.create("sch-good")
        self.create("sch-bad")
        # Corrupt one run's record and check the other still runs.
        path = Path(self._tmp.name) / "sch-bad.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")

        result = self.scheduler.sweep(self.backend, log=self.logs.append)
        self.assertIn("sch-good", result.driven)
        self.assertIn("sch-bad", set(result.failed) | set(result.skipped))
        self.assertEqual(self.health.health("sch-bad").state, self.health.FAILED)
        self.assertIn("unreadable", self.health.health("sch-bad").detail)

    def test_the_loop_stops_when_nothing_can_move(self):
        self.create("sch-loop")
        slept: list[int] = []
        passes = self.scheduler.serve(self.backend, interval=1, max_passes=10,
                                      sleeper=slept.append, log=self.logs.append)
        self.assertEqual(passes, 2)  # one pass drives it, the next finds nothing
        self.assertTrue(any("nothing left that can move" in line for line in self.logs))

    def test_the_loop_never_runs_past_its_ceiling(self):
        self.create("sch-cap")
        # A backend that always fails leaves the run failed, so the loop must stop on
        # its own; the ceiling is the backstop we assert here.
        slept: list[int] = []
        passes = self.scheduler.serve(self.backend, interval=1, max_passes=1,
                                      sleeper=slept.append, log=self.logs.append)
        self.assertEqual(passes, 1)
        self.assertEqual(slept, [])  # never sleeps after the final pass

    def test_a_sweep_creates_no_database_unless_one_is_configured(self):
        import os
        from unittest.mock import patch
        # The default path has to be one this test owns. Reading the repo's real
        # runtime/data/myorg.db made this pass only on a machine where nobody had run the
        # bootstrap -- and setting MYORG_DB instead would *be* configuring a store, which
        # is the opposite of what this test is about.
        unasked = Path(self._tmp.name) / "unasked-for.db"
        os.environ.pop("MYORG_DB", None)
        self.create("sch-nodb")
        with patch("runtime.projection.default_db", return_value=unasked):
            self.scheduler.sweep(self.backend, log=self.logs.append)
        self.assertFalse(unasked.is_file(),
                         "the driver must not conjure a store nobody asked for")

    def test_a_sweep_mirrors_into_a_store_when_one_is_configured(self):
        import os
        target = Path(self._tmp.name) / "mirror.db"
        os.environ["MYORG_DB"] = str(target)
        self.addCleanup(os.environ.pop, "MYORG_DB", None)

        self.create("sch-mirror")
        self.scheduler.sweep(self.backend, log=self.logs.append)

        from runtime.projection import open_store
        store = open_store(target)
        with store.reading() as connection:
            runs = [r["id"] for r in connection.execute("SELECT id FROM runs")]
        self.assertIn("sch-mirror", runs)

    def test_a_stalled_run_is_rescued_not_skipped(self):
        """A stall is work sitting still. The sweep must pick it up, not walk past."""
        self.create("sch-stall")
        run = self.health.health("sch-stall")
        self.assertIn(self.health.RUNNING, (run.state,))

        # Age it past the stall threshold; it still has ready work.
        import datetime
        later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            minutes=self.health.STALLED_AFTER_MINUTES + 5)
        self.assertEqual(self.health.health("sch-stall", now=later).state,
                         self.health.STALLED)
        self.assertIn("sch-stall", self.scheduler.movable_runs(now=later))

        result = self.scheduler.sweep(self.backend, log=self.logs.append)
        self.assertIn("sch-stall", result.driven)

    def test_a_finished_run_is_never_driven_again(self):
        self.create("sch-done", MAKER_CHECKER)
        self.executor.advance("sch-done", self.backend, log=self.logs.append)
        self.executor.quietly(self.core.approve, self.executor.namespace(
            run_id="sch-done", step="release-output", approver="op",
            approval_ref="ok", request_id="sch-approve"))
        self.executor.advance("sch-done", self.backend, log=self.logs.append)

        self.assertEqual(self.health.health("sch-done").state, self.health.FINISHED)
        self.assertIn("sch-done", self.scheduler.sweep(self.backend,
                                                       log=self.logs.append).skipped)


class SupervisedServiceTest(SchedulingTest):
    """DEP-07: the loop as a service rather than a command.

    As a command, "nothing can move" means the work is done and stopping is right. As a
    service it means the company is waiting for a trigger, and stopping would end autonomy
    until somebody opened a terminal -- which is the thing the service exists to avoid.
    """

    def test_a_supervised_loop_keeps_waiting_when_there_is_nothing_to_do(self):
        slept: list[int] = []
        passes = self.scheduler.serve(self.backend, interval=1, max_passes=3,
                                      sleeper=slept.append, log=self.logs.append,
                                      stop_when_idle=False)
        self.assertEqual(passes, 3)
        self.assertFalse(any("nothing left that can move" in line for line in self.logs))

    def test_a_stop_request_ends_the_loop_between_passes_not_inside_one(self):
        stopper = self.scheduler.Shutdown()
        self.create("sch-signal")
        passes = self.scheduler.serve(self.backend, interval=1, max_passes=0,
                                      sleeper=lambda _s: stopper.request(),
                                      log=self.logs.append, stop_when_idle=False,
                                      shutdown=stopper)
        self.assertEqual(passes, 1)
        self.assertTrue(any("stop requested" in line for line in self.logs))
        # The pass it was in still completed, so nothing is left half-driven.
        self.assertIn("sch-signal", [r.run_id for r in self.health.all_health()])

    def test_a_supervised_loop_has_no_pass_ceiling_but_still_stops_on_request(self):
        stopper = self.scheduler.Shutdown()
        counted = {"passes": 0}

        def count_then_stop(_seconds):
            counted["passes"] += 1
            if counted["passes"] >= 5:
                stopper.request()

        passes = self.scheduler.serve(self.backend, interval=1, max_passes=0,
                                      sleeper=count_then_stop, log=self.logs.append,
                                      stop_when_idle=False, shutdown=stopper)
        self.assertEqual(passes, 5)

    def test_a_second_supervised_loop_refuses_to_start(self):
        """Steps are fenced and schedules are fenced, so two loops cannot corrupt state --
        but they can plan the same goal twice and pay for the same step twice. The common
        way to get two is mundane: a unit restarting while an operator has one in a
        terminal."""
        with self.scheduler.single_instance():
            with self.assertRaises(self.scheduler.AlreadyRunning):
                with self.scheduler.single_instance():
                    pass

    def test_the_guard_is_released_when_the_loop_ends(self):
        with self.scheduler.single_instance():
            pass
        with self.scheduler.single_instance():  # must not raise
            pass

    def test_a_one_shot_sweep_is_not_guarded(self):
        """`--once` is an operator at a keyboard; refusing it because a service is running
        would make the company impossible to inspect while it works."""
        with self.scheduler.single_instance():
            with self.scheduler.single_instance(enabled=False):
                pass

    def test_a_sweep_starts_nothing_new_unless_a_planner_is_supplied(self):
        """The old contract, kept: every caller written before triggers existed still
        gets a drive-only sweep."""
        self.assertEqual(self.scheduler.sweep(self.backend, log=self.logs.append).started, [])

    def test_a_sweep_never_mirrors_into_the_companys_real_database(self):
        """Regression, found by reading production: ten fabricated `sch-*` runs from this
        very test file had been mirrored into `runtime/data/myorg.db`. `MYORG_RUNS_DIR`
        redirected the log but not the projection target, so anything pretending to be a
        run could write to the operator's read model."""
        import os
        from runtime.projection import default_db
        os.environ.pop("MYORG_DB", None)
        resolved = default_db()
        self.assertEqual(resolved.parent, Path(self._tmp.name),
                         "the read model must follow MYORG_RUNS_DIR")
        self.assertNotEqual(resolved.resolve(),
                            (ROOT / "runtime" / "data" / "myorg.db").resolve())

        self.create("sch-isolated")
        self.scheduler.sweep(self.backend, log=self.logs.append)
        real = ROOT / "runtime" / "data" / "myorg.db"
        if real.is_file():
            from runtime.db import Store
            with Store(real).reading() as connection:
                ids = [row["id"] for row in connection.execute("SELECT id FROM runs")]
            self.assertNotIn("sch-isolated", ids)

    def test_intake_is_a_no_op_without_a_store(self):
        import os
        from unittest.mock import patch
        unasked = Path(self._tmp.name) / "absent.db"
        os.environ.pop("MYORG_DB", None)
        with patch("runtime.projection.default_db", return_value=unasked):
            self.assertIsNone(self.scheduler.trigger_store())
            self.assertEqual(self.scheduler.intake(object(), log=self.logs.append), [])
        self.assertFalse(unasked.exists())


class TriggeredSweepTest(SchedulingTest):
    """A run that exists because the clock said so, driven in the same pass that made it."""

    def setUp(self) -> None:
        super().setUp()
        import os
        from datetime import timedelta

        from runtime import triggers
        from runtime.db import Store
        self.triggers = triggers
        target = Path(self._tmp.name) / "triggered.db"
        os.environ["MYORG_DB"] = str(target)
        os.environ["MYORG_ORG_ID"] = "acme"
        self.addCleanup(os.environ.pop, "MYORG_DB", None)
        self.addCleanup(os.environ.pop, "MYORG_ORG_ID", None)
        self.store = Store(target)
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.store.upsert_actor("acme", "chief", "human", "Chief", ["system-admin"])
        self.store.create_schedule(
            "acme", "hourly-sweep", "interval", "Review the open pipeline",
            triggers.stamp(triggers.utc_now() - timedelta(minutes=1)), "chief",
            "create-sched", "trace-1", interval_seconds=3600)

    def test_the_clock_starts_a_run_and_the_same_sweep_drives_it(self):
        from runtime.planner import StubPlannerBackend
        result = self.scheduler.sweep(self.backend, log=self.logs.append,
                                      planner_backend=StubPlannerBackend())
        self.assertEqual(len(result.started), 1, "the clock should have started exactly one run")
        started = result.started[0]
        self.addCleanup(lambda: (ROOT / "runtime" / "workflows" / f"{started}.json")
                        .unlink(missing_ok=True))
        self.assertTrue(self.core.run_path(started).exists())
        self.assertIn(started, result.driven, "a run created this pass should also move this pass")
        self.assertIn("started 1", result.summary())

    def test_a_second_sweep_does_not_start_the_same_work_again(self):
        from runtime.planner import StubPlannerBackend
        first = self.scheduler.sweep(self.backend, log=self.logs.append,
                                     planner_backend=StubPlannerBackend())
        for run_id in first.started:
            self.addCleanup(lambda r=run_id: (ROOT / "runtime" / "workflows" / f"{r}.json")
                            .unlink(missing_ok=True))
        second = self.scheduler.sweep(self.backend, log=self.logs.append,
                                      planner_backend=StubPlannerBackend())
        self.assertEqual(second.started, [])


if __name__ == "__main__":
    unittest.main()
