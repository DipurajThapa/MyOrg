from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MemoryTest(unittest.TestCase):
    """Shared memory across runs: propose, approve, recall -- and never before approval."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("MYORG_MEMORY_DIR")
        os.environ["MYORG_MEMORY_DIR"] = self._tmp.name
        self.addCleanup(self._restore)

        from runtime import memory
        self.memory = importlib.reload(memory)
        self.addCleanup(lambda: importlib.reload(memory))

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("MYORG_MEMORY_DIR", None)
        else:
            os.environ["MYORG_MEMORY_DIR"] = self._previous

    def propose(self, subject="Legal redlines stall onboarding",
                body="Contract review added 19 days; start redlines at day one.",
                author="coo-operations"):
        return self.memory.propose(subject, body, author)

    # --- nothing is shared until a human says so ----------------------------------

    def test_a_proposal_is_not_visible_to_agents_until_approved(self):
        entry = self.propose()
        self.assertEqual(entry.status, "proposed")
        self.assertFalse(entry.live)
        self.assertEqual(self.memory.recall("onboarding redlines contract"), [])

    def test_an_approved_lesson_reaches_the_next_agent(self):
        entry = self.propose()
        self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        found = self.memory.recall("onboarding contract redlines delay")
        self.assertEqual([e.id for e in found], [entry.id])
        self.assertIn("19 days", found[0].as_prompt_line())

    def test_a_rejected_lesson_never_reaches_anyone(self):
        entry = self.propose()
        self.memory.decide(entry.id, "rejected", "dipuraj")
        self.assertEqual(self.memory.recall("onboarding redlines contract"), [])

    def test_a_retired_lesson_stops_being_told(self):
        entry = self.propose()
        self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        self.assertTrue(self.memory.recall("onboarding redlines contract"))
        self.memory.decide(entry.id, "retired", "dipuraj")
        self.assertEqual(self.memory.recall("onboarding redlines contract"), [])

    def test_a_memory_decision_must_name_a_person(self):
        entry = self.propose()
        with self.assertRaises(SystemExit):
            self.memory.decide(entry.id, self.memory.LIVE, "   ")
        self.assertEqual(self.memory.recall("onboarding redlines contract"), [])

    # --- recall ---------------------------------------------------------------------

    def test_recall_returns_only_what_is_relevant(self):
        for subject, body in [
            ("Legal redlines stall onboarding", "Contract review added nineteen days."),
            ("Invoice disputes need finance early", "Billing errors delay renewals."),
        ]:
            entry = self.memory.propose(subject, body, "coo-operations")
            self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")

        found = self.memory.recall("contract redlines during onboarding")
        self.assertEqual(len(found), 1)
        self.assertIn("redlines", found[0].subject.lower())

    def test_recall_prefers_the_closest_match(self):
        for subject, body in [
            ("Onboarding contract redlines", "Redlines dominate onboarding delay."),
            ("Onboarding generally", "Onboarding involves many teams."),
        ]:
            entry = self.memory.propose(subject, body, "coo-operations")
            self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        found = self.memory.recall("onboarding contract redlines delay")
        self.assertIn("redlines", found[0].subject.lower())

    def test_recall_is_capped_so_prompts_stay_small(self):
        for n in range(10):
            entry = self.memory.propose(f"Onboarding lesson number {n}",
                                        f"Onboarding detail number {n} matters.",
                                        "coo-operations")
            self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        self.assertLessEqual(len(self.memory.recall("onboarding detail")),
                             self.memory.MAX_RECALL)

    def test_noise_words_alone_recall_nothing(self):
        entry = self.propose()
        self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        self.assertEqual(self.memory.recall("this that the work step goal"), [])

    # --- integrity ------------------------------------------------------------------

    def test_two_different_lessons_from_one_department_both_survive(self):
        """Identity was the subject alone, and the only caller in the runtime builds every
        subject as "<owner> on <action>" -- so the company could hold exactly one lesson per
        department-and-action pair, for ever. The first rejection's insight was kept and
        every later one was dropped before a person could see it: `propose` returned None
        and the checker had nothing to show for the review.
        """
        subject = "chief-knowledge-officer on research (market-scan)"
        bodies = ["Cite the vendor's own pricing page, not a comparison blog for prices.",
                  "Every candidate needs one sourced market-size figure, even a skip."]
        first = self.memory.propose(subject, bodies[0], "head-of-data")
        second = self.memory.propose(subject, bodies[1], "head-of-data")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second, "a second lesson from the same pair must not vanish")
        self.assertNotEqual(first.id, second.id, "two lessons, two rows")
        self.assertIsNone(self.memory.propose(subject, bodies[0], "head-of-data"),
                          "the identical lesson is still refused")
        for entry in (first, second):
            self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        recalled = self.memory.recall("pricing market size candidate research")
        self.assertEqual(len(recalled), 2, "both are reusable, not just the first one")

    def test_the_same_lesson_is_not_proposed_twice(self):
        first = self.propose()
        self.assertIsNotNone(first)
        self.assertIsNone(self.propose())

    def test_a_rejected_lesson_may_be_proposed_again_later(self):
        entry = self.propose()
        self.memory.decide(entry.id, "rejected", "dipuraj")
        self.assertIsNotNone(self.propose())

    def test_an_altered_memory_file_is_refused_not_trusted(self):
        entry = self.propose()
        self.memory.decide(entry.id, self.memory.LIVE, "dipuraj")
        path = self.memory.store_path(self.memory.DEFAULT_ORG)
        path.write_text(path.read_text(encoding="utf-8").replace("nineteen", "ninety")
                        .replace("19 days", "90 days"), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.memory.current()

    def test_a_memory_needs_a_subject_and_a_body(self):
        with self.assertRaises(SystemExit):
            self.memory.propose("", "body", "coo-operations")
        with self.assertRaises(SystemExit):
            self.memory.propose("subject", "   ", "coo-operations")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(SystemExit):
            self.memory.propose("s", "b", "a", kind="gossip")

    def test_a_long_body_is_trimmed(self):
        entry = self.memory.propose("Long one", "word " * 500, "coo-operations")
        self.assertLessEqual(len(entry.body), self.memory.MAX_BODY_CHARS)

    # --- one company's memory never leaks into another -----------------------------

    def test_memory_is_scoped_to_its_organization(self):
        entry = self.memory.propose("Acme onboarding redlines", "Acme specific detail.",
                                    "coo-operations", org_id="acme")
        self.memory.decide(entry.id, self.memory.LIVE, "dipuraj", org_id="acme")

        self.assertTrue(self.memory.recall("acme onboarding redlines", org_id="acme"))
        self.assertEqual(self.memory.recall("acme onboarding redlines", org_id="other"), [])
        self.assertEqual(self.memory.recall("acme onboarding redlines"), [])

    def test_an_invalid_org_id_is_refused(self):
        with self.assertRaises(SystemExit):
            self.memory.store_path("../escape")


if __name__ == "__main__":
    unittest.main()
