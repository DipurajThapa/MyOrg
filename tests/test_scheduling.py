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


if __name__ == "__main__":
    unittest.main()
