from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "runtime" / "workflows" / "manual-gold-run.json"


class EscalationTest(unittest.TestCase):
    """Nobody had to be told before. Now something says a person is needed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env = {k: os.environ.get(k) for k in
                     ("MYORG_RUNS_DIR", "MYORG_OUTBOX", "MYORG_NOTIFY_COMMAND",
                      "MYORG_MEMORY_DIR")}
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        os.environ["MYORG_OUTBOX"] = str(Path(self._tmp.name) / "_outbox.jsonl")
        os.environ["MYORG_MEMORY_DIR"] = self._tmp.name
        os.environ.pop("MYORG_NOTIFY_COMMAND", None)
        self.addCleanup(self._restore)

        from runtime import (company_runtime, escalation, executor, health, memory,
                             notify)
        self.core = importlib.reload(company_runtime)
        self.executor = importlib.reload(executor)
        importlib.reload(health)
        self.memory = importlib.reload(memory)
        self.notify = importlib.reload(notify)
        self.escalation = importlib.reload(escalation)
        for module in (company_runtime, executor, health, memory, notify, escalation):
            self.addCleanup(lambda m=module: importlib.reload(m))

        self.logs: list[str] = []
        self.addCleanup(self.clear_evidence)

    def clear_evidence(self) -> None:
        for pattern in ("esc-*.evidence", "esc-*.brief"):
            for path in self.executor.EVIDENCE_DIR.glob(pattern):
                path.unlink(missing_ok=True)

    def _restore(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def create(self, run_id: str) -> None:
        self.executor.quietly(self.core.create_run, self.executor.namespace(
            workflow=str(WORKFLOW), run_id=run_id, actor="chief-of-staff",
            request_id=f"create-{run_id}", org="acme"))

    def park(self, run_id: str) -> None:
        self.create(run_id)
        self.executor.advance(run_id, self.executor.StubBackend(), log=self.logs.append)

    # --- somebody gets told ---------------------------------------------------------

    def test_a_run_waiting_on_a_person_raises_a_blocking_notice(self):
        self.park("esc-waiting")
        raised = self.escalation.scan(log=self.logs.append)

        self.assertEqual(len(raised), 1)
        notice = raised[0]
        self.assertEqual(notice.kind, self.notify.NEEDS_APPROVAL)
        self.assertTrue(notice.blocking)
        self.assertEqual((notice.run_id, notice.step_id), ("esc-waiting", "release-output"))
        self.assertIn("Control Center", notice.action)
        self.assertEqual(notice.org_id, "acme")

    def test_a_run_that_ran_out_of_retries_raises_a_blocking_notice(self):
        def failing(request):
            raise self.executor.ExecutorError("down")

        self.create("esc-dead")
        self.executor.advance("esc-dead", failing, log=self.logs.append)
        raised = self.escalation.scan(log=self.logs.append)

        notice = next(n for n in raised if n.run_id == "esc-dead")
        self.assertEqual(notice.kind, self.notify.RUN_FAILED)
        self.assertTrue(notice.blocking)
        self.assertIn("failed every time", notice.detail)

    def test_a_proposed_lesson_is_surfaced_too(self):
        entry = self.memory.propose("Redlines stall onboarding",
                                    "Contract review added nineteen days.",
                                    "coo-operations")
        self.assertIsNotNone(entry)
        raised = self.escalation.scan(log=self.logs.append)
        notice = next(n for n in raised if n.kind == self.notify.LESSON_PROPOSED)
        self.assertFalse(notice.blocking)
        self.assertIn("Redlines stall onboarding", notice.detail)

    def test_a_healthy_run_bothers_nobody(self):
        self.create("esc-fine")
        self.assertEqual(self.escalation.scan(log=self.logs.append), [])
        self.assertEqual(self.notify.outstanding(), [])

    # --- and is not told the same thing twice ---------------------------------------

    def test_the_same_problem_is_reported_once(self):
        self.park("esc-once")
        first = self.escalation.scan(log=self.logs.append)
        second = self.escalation.scan(log=self.logs.append)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(self.notify.outstanding()), 1)

    def test_acknowledging_clears_it(self):
        self.park("esc-ack")
        notice = self.escalation.scan(log=self.logs.append)[0]
        self.notify.mark_delivered(notice.id)
        self.assertEqual(self.notify.outstanding(), [])

    # --- what is most urgent comes first --------------------------------------------

    def test_blocking_notices_are_listed_above_routine_ones(self):
        entry = self.memory.propose("A lesson", "Something worth keeping here.",
                                    "coo-operations")
        self.assertIsNotNone(entry)
        self.park("esc-order")
        self.escalation.scan(log=self.logs.append)

        severities = [n.severity for n in self.notify.outstanding()]
        self.assertEqual(severities[0], "blocking")
        self.assertEqual(severities[-1], "routine")

    def test_the_summary_says_how_many_are_blocking(self):
        self.park("esc-render")
        self.escalation.scan(log=self.logs.append)
        text = self.notify.render(self.notify.outstanding())
        self.assertIn("esc-render/release-output", text)
        self.assertIn("1 blocking", text)

    def test_an_empty_outbox_reads_plainly(self):
        self.assertEqual(self.notify.render([]), "Nothing needs you.")

    # --- the company never reaches outward on its own -------------------------------

    def test_nothing_is_sent_when_no_delivery_is_configured(self):
        self.park("esc-quiet")
        self.escalation.scan(log=self.logs.append)
        waiting = self.notify.deliver(log=self.logs.append)

        # Still outstanding: listed, not sent.
        self.assertTrue(waiting)
        self.assertTrue(self.notify.outstanding())

    def test_a_configured_command_receives_the_notice_and_it_is_marked_sent(self):
        sink = Path(self._tmp.name) / "sent.txt"
        script = Path(self._tmp.name) / "deliver.py"
        script.write_text(
            "import sys, pathlib\n"
            f"pathlib.Path(r'{sink}').write_text(sys.argv[1], encoding='utf-8')\n",
            encoding="utf-8")
        os.environ["MYORG_NOTIFY_COMMAND"] = f"{__import__('sys').executable} {script}"

        self.park("esc-send")
        self.escalation.scan(log=self.logs.append)
        sent = self.notify.deliver(log=self.logs.append)

        self.assertTrue(sent)
        payload = json.loads(sink.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], "esc-send")
        self.assertEqual(self.notify.outstanding(), [])

    def test_a_broken_delivery_command_leaves_the_notice_outstanding(self):
        os.environ["MYORG_NOTIFY_COMMAND"] = "no-such-command-anywhere"
        self.park("esc-broken")
        self.escalation.scan(log=self.logs.append)
        self.notify.deliver(log=self.logs.append)

        self.assertTrue(self.notify.outstanding(),
                        "a failed send must not silently drop the notice")


if __name__ == "__main__":
    unittest.main()
