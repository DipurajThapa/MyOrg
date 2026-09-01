from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"


class DurabilityTest(unittest.TestCase):
    """Can we tell the record is intact, and can we get it back?"""

    def setUp(self) -> None:
        self._runs = tempfile.TemporaryDirectory()
        self._store = tempfile.TemporaryDirectory()
        self.addCleanup(self._runs.cleanup)
        self.addCleanup(self._store.cleanup)
        self._env = {k: os.environ.get(k) for k in ("MYORG_RUNS_DIR", "MYORG_MEMORY_DIR")}
        os.environ["MYORG_RUNS_DIR"] = self._runs.name
        os.environ["MYORG_MEMORY_DIR"] = self._store.name
        self.addCleanup(self._restore)

        from runtime import company_runtime, durability, executor, memory
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        self.memory = importlib.reload(memory)
        self.durability = importlib.reload(durability)
        for module in (company_runtime, executor, memory, durability):
            self.addCleanup(lambda m=module: importlib.reload(m))

        self.logs: list[str] = []
        self.addCleanup(self.clear_evidence)

    def clear_evidence(self) -> None:
        for path in self.executor.EVIDENCE_DIR.glob("dur-*"):
            path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def worked(self, run_id: str = "dur-one") -> str:
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(WORKFLOW), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org="default"))
        self.executor.advance(run_id, self.executor.StubBackend(), log=self.logs.append)
        return run_id

    # --- knowing the record is sound -------------------------------------------------

    def test_a_healthy_company_reports_itself_sound(self):
        self.worked()
        report = self.durability.verify()
        self.assertTrue(report.sound)
        self.assertIn("dur-one", report.runs_ok)
        self.assertIn("intact", report.summary())

    def test_a_broken_event_chain_is_caught(self):
        run_id = self.worked("dur-broken")
        path = Path(self._runs.name) / f"{run_id}.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "chief-of-staff", "cto-engineering", 1), encoding="utf-8")

        report = self.durability.verify()
        self.assertFalse(report.sound)
        self.assertIn(run_id, report.runs_broken)
        self.assertIn("broken chain", report.summary())

    def test_evidence_edited_after_the_fact_is_caught(self):
        self.worked("dur-edited")
        state = self.executor.current_state("dur-edited")
        evidence = ROOT / state["steps"]["frame-goal"]["evidence"]
        evidence.write_text("quietly rewritten\n", encoding="utf-8")

        report = self.durability.verify()
        self.assertFalse(report.sound)
        self.assertIn("dur-edited/frame-goal", report.evidence_altered)

    def test_evidence_that_has_gone_missing_is_caught(self):
        self.worked("dur-gone")
        state = self.executor.current_state("dur-gone")
        (ROOT / state["steps"]["frame-goal"]["evidence"]).unlink()

        report = self.durability.verify()
        self.assertFalse(report.sound)
        self.assertIn("dur-gone/frame-goal", report.evidence_missing)

    def test_a_damaged_memory_store_is_caught(self):
        entry = self.memory.propose("A lesson", "Worth keeping for later.", "coo-operations")
        self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        path = self.memory.store_path(self.memory.DEFAULT_ORG)
        path.write_text(path.read_text(encoding="utf-8").replace(
            "Worth keeping", "Quietly changed"), encoding="utf-8")

        report = self.durability.verify()
        self.assertFalse(report.sound)
        self.assertIn(self.memory.DEFAULT_ORG, report.memory_broken)

    # --- getting it back --------------------------------------------------------------

    def test_a_backup_records_what_it_holds(self):
        self.worked("dur-save")
        archive = Path(self._store.name) / "backup.tgz"
        manifest = self.durability.backup(archive)

        self.assertTrue(archive.is_file())
        self.assertIn("dur-save", manifest["runs"])
        self.assertTrue(manifest["sound_at_backup"])
        self.assertEqual(len(manifest["sha256"]), 64)

    def test_a_backup_of_a_damaged_company_says_so(self):
        run_id = self.worked("dur-warn")
        path = Path(self._runs.name) / f"{run_id}.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "junk\n", encoding="utf-8")

        manifest = self.durability.backup(Path(self._store.name) / "warned.tgz")
        self.assertFalse(manifest["sound_at_backup"])
        self.assertIn("DAMAGE", manifest["summary"])

    def test_a_restore_brings_the_runs_back(self):
        self.worked("dur-restore")
        archive = Path(self._store.name) / "full.tgz"
        self.durability.backup(archive)

        target = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        manifest = self.durability.restore(archive, target)

        self.assertIn("dur-restore", manifest["runs"])
        self.assertIn("runs", manifest["directories"])
        self.assertTrue((target / "runs" / "dur-restore.jsonl").is_file())
        # And the manifest remembers where it came from, for an operator restoring blind.
        self.assertIn("runs", manifest["taken_from"])

    def test_a_restore_will_not_quietly_overwrite_live_state(self):
        self.worked("dur-guard")
        archive = Path(self._store.name) / "guard.tgz"
        self.durability.backup(archive)

        target = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        self.durability.restore(archive, target)
        with self.assertRaises(SystemExit):
            self.durability.restore(archive, target)          # second time: refuses
        self.durability.restore(archive, target, force=True)  # unless told to

    def test_restoring_something_that_is_not_there_fails_clearly(self):
        with self.assertRaises(SystemExit):
            self.durability.restore(Path(self._store.name) / "nope.tgz", Path("."))


if __name__ == "__main__":
    unittest.main()
