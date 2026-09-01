from __future__ import annotations

import unittest

from runtime.briefing import (MAX_FINDINGS, MAX_LINE_CHARS, Brief, BriefRequest,
                              parse_brief)

WELL_FORMED = """ASK: Allow the customer response to be sent to ACME.
IF YES: The email goes to their VP of Ops today.
FINDINGS:
- Onboarding took 41 days against a 14-day target.
- Legal redlines caused 19 of those days.
- Fix plan is funded and staffed.
WATCH: We commit to a 14-day target we have not yet hit once.
RECOMMEND: APPROVE - the account is at risk and the plan is credible.
"""


class BriefParsingTest(unittest.TestCase):
    def test_a_well_formed_brief_reads_cleanly(self):
        brief = parse_brief(WELL_FORMED)
        self.assertTrue(brief.usable)
        self.assertTrue(brief.recommends_approval)
        self.assertEqual(len(brief.findings), 3)
        self.assertIn("41 days", brief.findings[0])
        self.assertIn("14-day target", brief.watch)

    def test_a_rejection_is_read_as_a_rejection(self):
        brief = parse_brief(WELL_FORMED.replace("RECOMMEND: APPROVE",
                                                "RECOMMEND: REJECT"))
        self.assertTrue(brief.usable)
        self.assertFalse(brief.recommends_approval)

    def test_findings_are_capped_so_the_card_stays_one_screen(self):
        many = WELL_FORMED.replace(
            "- Fix plan is funded and staffed.",
            "\n".join(f"- Extra finding number {n}." for n in range(10)))
        self.assertEqual(len(parse_brief(many).findings), MAX_FINDINGS)

    def test_a_rambling_line_is_trimmed(self):
        brief = parse_brief(f"ASK: {'word ' * 200}\nRECOMMEND: APPROVE - fine\n")
        self.assertLessEqual(len(brief.ask), MAX_LINE_CHARS)

    def test_prose_around_the_fields_is_ignored(self):
        brief = parse_brief("Certainly! Here is your brief:\n\n" + WELL_FORMED +
                            "\nLet me know if you need anything else.")
        self.assertTrue(brief.usable)
        self.assertNotIn("Certainly", brief.ask)

    def test_a_brief_missing_the_decision_is_unusable_not_half_shown(self):
        self.assertFalse(parse_brief("ASK: something\nIF YES: something\n").usable)
        self.assertFalse(parse_brief("RECOMMEND: APPROVE - ok\n").usable)
        self.assertFalse(parse_brief("I'd rather write an essay.").usable)

    def test_an_empty_brief_is_unusable(self):
        self.assertFalse(Brief().usable)

    def test_round_trips_through_its_own_text_form(self):
        original = parse_brief(WELL_FORMED)
        self.assertEqual(parse_brief(original.as_text()), original)


class BriefRequestTest(unittest.TestCase):
    def request(self) -> BriefRequest:
        return BriefRequest(agent="customer-success", step_id="send-response",
                            action="external_send", risk="yellow",
                            goal="Save the ACME account", brief="b",
                            evidence="the full work product")

    def test_the_prompt_demands_the_short_shape(self):
        prompt = self.request().prompt()
        for field in ("ASK:", "IF YES:", "FINDINGS:", "WATCH:", "RECOMMEND:"):
            self.assertIn(field, prompt)
        self.assertIn("thirty seconds", prompt)
        self.assertIn("cannot read the full work", prompt)

    def test_the_prompt_carries_the_work_being_decided_on(self):
        prompt = self.request().prompt()
        self.assertIn("the full work product", prompt)
        self.assertIn("external_send", prompt)
        self.assertIn("yellow", prompt)

    def test_unresolved_objections_are_routed_to_watch(self):
        self.assertIn("unresolved objection", self.request().prompt())


if __name__ == "__main__":
    unittest.main()
