"""Standing the company up on a real host, in one governed command.

ARCH-06 in the REV2 audit: every table, migration and projection existed, but no store had
ever been created outside a test fixture. `runtime/data/` did not exist, so the operator
read model, the decision queue over HTTP and the projection had never met real data. The
missing piece was not code -- it was a bootstrap an operator can actually run, and that
says what to do next.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime.auth import TokenService
from runtime.db import Store

ROOT = Path(__file__).resolve().parents[1]
SECRET = "0123456789abcdef0123456789abcdef"


class BootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "myorg.db"
        self._previous = os.environ.get("MYORG_AUTH_SECRET")
        os.environ["MYORG_AUTH_SECRET"] = SECRET
        self.addCleanup(self._restore)
        from runtime import admin
        self.admin = importlib.reload(admin)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("MYORG_AUTH_SECRET", None)
        else:
            os.environ["MYORG_AUTH_SECRET"] = self._previous

    def bootstrap(self, *extra: str) -> dict:
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.admin.main(["--db", str(self.db), "bootstrap", "--org", "acme",
                                    "--name", "Acme Ltd", "--operator", "chief",
                                    "--operator-name", "Chief Operator", *extra])
        self.assertEqual(code, 0)
        # The command prints a JSON summary first, then human next steps.
        return json.loads(buffer.getvalue().split("\n\n", 1)[0])

    # --- it stands the company up ---------------------------------------------------

    def test_one_command_creates_a_usable_store(self) -> None:
        summary = self.bootstrap()
        self.assertTrue(self.db.is_file())
        self.assertEqual(summary["organization"], "acme")
        self.assertEqual(summary["operator"], "chief")
        store = Store(self.db)
        self.assertEqual(store.migrate(), [], "migrations must already be applied")
        actor = store.actor("acme", "chief")
        self.assertEqual(actor["actor_type"], "human")
        self.assertIn("decision-owner", actor["roles"])

    def test_the_operator_can_actually_sign_in(self) -> None:
        summary = self.bootstrap()
        store = Store(self.db)
        principal = TokenService(store, SECRET).verify(summary["token"])
        self.assertEqual((principal.org_id, principal.actor_id), ("acme", "chief"))
        self.assertTrue(principal.has_role("decision-owner"))

    def test_running_it_twice_changes_nothing(self) -> None:
        first = self.bootstrap()
        second = self.bootstrap()
        self.assertEqual(first["organization"], second["organization"])
        store = Store(self.db)
        self.assertEqual(store.actor("acme", "chief")["display_name"], "Chief Operator")
        self.assertEqual(store.migrate(), [])

    def test_it_says_what_to_do_next(self) -> None:
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.admin.main(["--db", str(self.db), "bootstrap", "--org", "acme",
                             "--name", "Acme Ltd", "--operator", "chief",
                             "--operator-name", "Chief Operator"])
        printed = buffer.getvalue()
        self.assertIn("MYORG_AUTH_SECRET", printed)
        self.assertIn("MYORG_DB", printed)

    # --- and it fails in ways an operator can act on --------------------------------

    def test_without_a_secret_it_refuses_rather_than_issuing_nothing(self) -> None:
        os.environ.pop("MYORG_AUTH_SECRET", None)
        with self.assertRaises(SystemExit) as caught:
            self.admin.main(["--db", str(self.db), "bootstrap", "--org", "acme",
                             "--name", "Acme Ltd", "--operator", "chief",
                             "--operator-name", "Chief Operator"])
        self.assertIn("MYORG_AUTH_SECRET", str(caught.exception))

    def test_an_unknown_role_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self.admin.main(["--db", str(self.db), "bootstrap", "--org", "acme",
                             "--name", "Acme Ltd", "--operator", "chief",
                             "--operator-name", "Chief Operator", "--role", "superuser"])


class ProjectionAgainstARealStoreTest(unittest.TestCase):
    """The half the audit said had never met real data: log -> store -> read model."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self._runs = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self.db = Path(self.temporary.name) / "myorg.db"

        self._previous = {k: os.environ.get(k) for k in
                          ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG", "MYORG_AUTH_SECRET", "MYORG_DB")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._runs.name) / "_audit-log.jsonl")
        os.environ["MYORG_AUTH_SECRET"] = SECRET
        os.environ["MYORG_DB"] = str(self.db)
        self.addCleanup(self._restore)

        from runtime import admin, company_runtime, projection
        self.admin = importlib.reload(admin)
        self.core = importlib.reload(company_runtime)
        self.projection = importlib.reload(projection)
        for module in (admin, company_runtime, projection):
            self.addCleanup(lambda m=module: importlib.reload(m))

        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.admin.main(["--db", str(self.db), "bootstrap", "--org", "acme",
                             "--name", "Acme Ltd", "--operator", "chief",
                             "--operator-name", "Chief Operator"])

    def _restore(self) -> None:
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ns(self, **fields):
        return argparse.Namespace(**fields)

    def park_a_run(self) -> None:
        workflow = {"version": 1, "id": "wf-arch-six", "goal": "prove the read model is real",
                    "max_cycles": 12,
                    "steps": [{"id": "s1", "owner": "cmo-marketing", "action": "publish",
                               "depends_on": [], "max_attempts": 2}]}
        path = Path(self._runs.name) / "arch-six.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        self.core.create_run(self.ns(workflow=str(path), run_id="arch-six",
                                     actor="chief-of-staff", request_id="create-arch-six",
                                     org="acme"))
        self.core.request_step(self.ns(run_id="arch-six", step="s1", actor="cmo-marketing",
                                       holder="driver-a", request_id="req-arch-six"))

    def test_a_real_run_reaches_the_store(self) -> None:
        self.park_a_run()
        store = Store(self.db)
        projected = self.projection.project_all(store, log=lambda _m: None)
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]["run_id"], "arch-six")

    def test_the_read_model_answers_what_is_waiting_on_a_person(self) -> None:
        self.park_a_run()
        store = Store(self.db)
        self.projection.project_all(store, log=lambda _m: None)
        waiting = self.projection.waiting_on_humans(store, "acme")
        self.assertEqual(len(waiting), 1)
        self.assertEqual((waiting[0]["run_id"], waiting[0]["status"]),
                         ("arch-six", "awaiting_approval"))

    def test_the_store_and_the_run_log_agree(self) -> None:
        """Two halves, one answer -- the whole point of the one-way projection."""
        self.park_a_run()
        store = Store(self.db)
        self.projection.project_all(store, log=lambda _m: None)
        from runtime.service import MyOrgService
        principal = TokenService(store, SECRET).verify(
            TokenService(store, SECRET).issue("acme", "chief"))
        from_log = MyOrgService(store).pending_decisions(principal)
        from_store = self.projection.waiting_on_humans(store, "acme")
        self.assertEqual([(d["run_id"], d["step"]) for d in from_log],
                         [(d["run_id"], d["step_id"]) for d in from_store])


if __name__ == "__main__":
    unittest.main()
