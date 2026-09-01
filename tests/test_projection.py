from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"


class ProjectionTest(unittest.TestCase):
    """The event log and the store must describe the same company."""

    def setUp(self) -> None:
        self._runs = tempfile.TemporaryDirectory()
        self._data = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self.addCleanup(self._data.cleanup)
        self._env = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_ORG_ID")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ.pop("MYORG_ORG_ID", None)
        self.addCleanup(self._restore)

        from runtime import company_runtime, executor, projection
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.projection = importlib.reload(projection)
        for module in (company_runtime, executor, projection):
            self.addCleanup(lambda m=module: importlib.reload(m))

        self.store = self.projection.open_store(Path(self._data.name) / "myorg.db")
        self.addCleanup(self.clear_evidence)
        self.logs: list[str] = []

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("proj-*.evidence"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def create(self, run_id: str, org: str | None = None) -> None:
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(WORKFLOW), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org=org))

    def rows(self, table: str) -> list[dict]:
        with self.store.reading() as connection:
            return [dict(r) for r in connection.execute(f"SELECT * FROM {table}")]

    # --- a run reaches the store at all --------------------------------------------

    def test_a_run_created_by_the_driver_appears_in_the_store(self):
        self.create("proj-one")
        result = self.projection.project_run(self.store, "proj-one")

        self.assertEqual(result["run_id"], "proj-one")
        run = self.rows("runs")[0]
        self.assertEqual(run["id"], "proj-one")
        self.assertEqual(run["runtime_status"], "active")
        self.assertEqual(run["max_cycles"], 12)
        self.assertEqual(len(self.rows("run_steps")), 4)

    def test_every_step_is_projected_with_its_owner_and_risk(self):
        self.create("proj-steps")
        self.projection.project_run(self.store, "proj-steps")
        steps = {r["step_id"]: r for r in self.rows("run_steps")}

        self.assertEqual(steps["frame-goal"]["owner"], "chief-of-staff")
        self.assertEqual(steps["release-output"]["risk"], "yellow")
        self.assertEqual(steps["frame-goal"]["status"], "ready")
        self.assertEqual(json.loads(steps["produce-output"]["depends_on"]), ["frame-goal"])

    def test_projecting_twice_changes_nothing(self):
        self.create("proj-idem")
        self.projection.project_run(self.store, "proj-idem")
        first = self.rows("run_steps")
        self.projection.project_run(self.store, "proj-idem")

        self.assertEqual(len(self.rows("runs")), 1)
        self.assertEqual(len(self.rows("run_steps")), len(first))

    def test_progress_is_reflected_on_the_next_projection(self):
        self.create("proj-move")
        self.projection.project_run(self.store, "proj-move")
        self.executor.advance("proj-move", self.executor.StubBackend(),
                              log=self.logs.append)
        self.projection.project_run(self.store, "proj-move")

        steps = {r["step_id"]: r for r in self.rows("run_steps")}
        self.assertEqual(steps["frame-goal"]["status"], "completed")
        self.assertEqual(steps["release-output"]["status"], "awaiting_approval")
        self.assertTrue(steps["frame-goal"]["evidence_sha256"])

    def test_an_unknown_run_is_reported_not_invented(self):
        self.assertIsNone(self.projection.project_run(self.store, "no-such-run"))

    # --- the store's older status vocabulary still holds ----------------------------

    def test_precise_runtime_status_is_kept_beside_the_coarse_one(self):
        self.assertEqual(self.projection.coarse("blocked_review_limit"), "blocked")
        self.assertEqual(self.projection.coarse("blocked_human"), "blocked")
        self.assertEqual(self.projection.coarse("rejected_by_checker"), "cancelled")
        self.assertEqual(self.projection.coarse("completed"), "completed")
        self.assertEqual(self.projection.coarse("active"), "active")

    def test_a_rejected_run_lands_in_the_store_without_breaking_its_check(self):
        self.create("proj-reject")
        self.executor.advance("proj-reject", self.executor.StubBackend(),
                              log=self.logs.append)
        self.executor.quietly(self.core.reject, self.executor.namespace(
            run_id="proj-reject", step="release-output", approver="dipuraj",
            approval_ref="not ready", request_id="proj-reject-1"))
        self.projection.project_run(self.store, "proj-reject")

        run = self.rows("runs")[0]
        self.assertEqual(run["status"], "cancelled")          # the store's vocabulary
        self.assertEqual(run["runtime_status"], "rejected")   # the runtime's own word

    # --- organizations are a real boundary now --------------------------------------

    def test_a_run_carries_the_organization_that_owns_it(self):
        self.create("proj-org", org="acme")
        state = self.executor.current_state("proj-org")
        self.assertEqual(state["org_id"], "acme")

        self.projection.project_run(self.store, "proj-org")
        self.assertEqual(self.rows("runs")[0]["org_id"], "acme")

    def test_runs_without_an_explicit_org_get_the_default_one(self):
        self.create("proj-default")
        self.assertEqual(self.executor.current_state("proj-default")["org_id"], "default")

    def test_one_organizations_runs_are_not_visible_to_another(self):
        self.create("proj-acme", org="acme")
        self.create("proj-other", org="other")
        self.projection.project_all(self.store, log=self.logs.append)

        acme = self.projection.waiting_on_humans(self.store, "acme")
        self.assertEqual([r["run_id"] for r in acme], [])
        with self.store.reading() as connection:
            owned = connection.execute(
                "SELECT id FROM runs WHERE org_id=?", ("acme",)).fetchall()
        self.assertEqual([r["id"] for r in owned], ["proj-acme"])

    def test_the_organization_is_created_if_it_is_new(self):
        self.create("proj-newco", org="newco")
        self.projection.project_run(self.store, "proj-newco")
        with self.store.reading() as connection:
            orgs = [r["id"] for r in connection.execute("SELECT id FROM organizations")]
        self.assertIn("newco", orgs)

    # --- what the operator can now ask the store ------------------------------------

    def test_the_store_can_answer_what_is_waiting_on_a_human(self):
        self.create("proj-waiting")
        self.executor.advance("proj-waiting", self.executor.StubBackend(),
                              log=self.logs.append)
        self.projection.project_all(self.store, log=self.logs.append)

        waiting = self.projection.waiting_on_humans(self.store, "default")
        self.assertEqual([(r["run_id"], r["step_id"]) for r in waiting],
                         [("proj-waiting", "release-output")])
        self.assertEqual(waiting[0]["risk"], "yellow")

    def test_project_all_covers_every_run(self):
        for name in ("proj-a", "proj-b", "proj-c"):
            self.create(name)
        projected = self.projection.project_all(self.store, log=self.logs.append)
        self.assertEqual(sorted(r["run_id"] for r in projected),
                         ["proj-a", "proj-b", "proj-c"])

    # --- the log stays the system of record ------------------------------------------

    def test_projecting_never_writes_back_into_the_event_log(self):
        self.create("proj-readonly")
        path = Path(self._runs.name) / "proj-readonly.jsonl"
        before = path.read_bytes()
        self.projection.project_run(self.store, "proj-readonly")
        self.assertEqual(path.read_bytes(), before)

    def test_a_tampered_log_is_refused_rather_than_projected(self):
        self.create("proj-tamper")
        path = Path(self._runs.name) / "proj-tamper.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "chief-of-staff", "cto-engineering", 1), encoding="utf-8")
        self.assertIsNone(self.projection.project_run(self.store, "proj-tamper"))
        self.assertEqual(self.rows("runs"), [])


if __name__ == "__main__":
    unittest.main()
