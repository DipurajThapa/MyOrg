from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime import skills

ROOT = Path(__file__).resolve().parents[1]


class SkillRegistryTest(unittest.TestCase):
    """What the company can actually do, versus what it says it can do."""

    def setUp(self) -> None:
        self.audit = skills.audit()

    # --- the registry tells the truth ----------------------------------------------

    def test_every_department_declares_at_least_one_skill(self):
        for agent, found in self.audit.items():
            self.assertTrue(found, f"{agent} claims no skills at all")

    def test_no_department_claims_a_skill_nobody_can_resolve(self):
        """The point of SKILL-02: a claim must resolve, locally or as a declared
        dependency. A new unresolvable reference fails here rather than drifting."""
        missing = skills.unresolved(self.audit)
        self.assertEqual(missing, {},
                         f"unresolved skills; build them or declare them in "
                         f"{skills.MANIFEST.name}: {missing}")

    def test_a_local_skill_resolves_to_this_repository(self):
        resolved = skills.resolve("lead-response")
        self.assertEqual(resolved.resolution, skills.LOCAL)
        self.assertEqual(resolved.provider, "in-repo")
        self.assertTrue(resolved.usable)

    def test_a_declared_skill_resolves_to_its_provider(self):
        resolved = skills.resolve("engineering:architecture")
        self.assertEqual(resolved.resolution, skills.DECLARED)
        self.assertTrue(resolved.provider)
        self.assertEqual(resolved.family, "engineering")

    def test_an_invented_skill_stays_unresolved(self):
        resolved = skills.resolve("department-of-magic:levitate")
        self.assertEqual(resolved.resolution, skills.UNRESOLVED)
        self.assertFalse(resolved.usable)

    # --- declared is not the same as proven -----------------------------------------

    def test_nothing_external_is_marked_verified_yet(self):
        manifest = json.loads(skills.MANIFEST.read_text(encoding="utf-8"))
        self.assertTrue(manifest["skills"])
        self.assertTrue(all(entry["verified_here"] is False
                            for entry in manifest["skills"].values()),
                        "a skill may only be marked verified once it has run here")
        self.assertIn("unverified", manifest["note"])

    def test_declared_skills_are_not_counted_as_in_repo(self):
        totals = skills.summary(self.audit)
        self.assertEqual(totals[skills.LOCAL], len(
            {s.reference for f in self.audit.values() for s in f
             if s.resolution == skills.LOCAL}))
        self.assertGreater(totals[skills.DECLARED], totals[skills.LOCAL],
                           "most capability is still external, and should read that way")

    def test_every_manifest_entry_names_a_provider_and_a_family(self):
        manifest = json.loads(skills.MANIFEST.read_text(encoding="utf-8"))
        for reference, entry in manifest["skills"].items():
            self.assertTrue(entry.get("provider"), f"{reference} has no provider")
            self.assertTrue(entry.get("family"), f"{reference} has no family")

    # --- parsing the agent files ----------------------------------------------------

    def test_a_partner_department_is_not_mistaken_for_a_skill(self):
        """Agents name colleagues in the same block; they are people, not capabilities."""
        for agent, found in self.audit.items():
            named = {skill.reference for skill in found}
            self.assertEqual(named & set(skills.departments()), set(),
                             f"{agent} lists a department as a skill")

    def test_skills_are_read_only_from_the_skills_section(self):
        found = skills.declared_by("cto-engineering")
        self.assertIn("engineering:architecture", found)
        # "How you work" follows the skills block and must not leak in.
        self.assertNotIn("file:line", found)

    def test_an_unknown_department_declares_nothing(self):
        self.assertEqual(skills.declared_by("no-such-department"), [])

    # --- grouping, so the gap is a work plan not a list of 124 ----------------------

    def test_missing_capability_is_grouped_into_families(self):
        grouped = skills.unresolved({
            "x": [skills.Skill("engineering:a", skills.UNRESOLVED, "engineering"),
                  skills.Skill("engineering:b", skills.UNRESOLVED, "engineering"),
                  skills.Skill("sales:c", skills.UNRESOLVED, "sales")]})
        self.assertEqual(grouped, {"engineering": {"engineering:a", "engineering:b"},
                                   "sales": {"sales:c"}})

    def test_a_family_is_the_namespace(self):
        self.assertEqual(skills.family_of("finance:close-management"), "finance")
        self.assertEqual(skills.family_of("dataviz"), "ungrouped")

    # --- SKILL-03: does a skill actually run anything? ------------------------------

    def test_a_skill_never_points_at_a_script_that_is_not_there(self):
        broken = skills.broken_tools()
        self.assertEqual(broken, [],
                         f"skills reference missing executables: "
                         f"{[(t.skill, t.path) for t in broken]}")

    def test_a_skill_that_runs_something_is_bound_to_a_real_file(self):
        tools = skills.tools_of("organization-management")
        self.assertTrue(tools, "this skill drives the runtime and should say so")
        self.assertTrue(all(tool.bound for tool in tools))
        self.assertIn("runtime/company_runtime.py", [tool.path for tool in tools])

    def test_a_procedure_only_skill_reports_no_tools(self):
        self.assertEqual(skills.tools_of("deal-desk"), [])

    def test_an_unknown_skill_has_no_tools(self):
        self.assertEqual(skills.tools_of("no-such-skill"), [])

    def test_the_binding_report_separates_executable_from_procedure(self):
        text = skills.render_bindings()
        self.assertIn("in-repo skills run something", text)
        self.assertIn("Procedure only", text)
        self.assertIn("organization-management", text)

    def test_the_report_reads_plainly(self):
        text = skills.render(self.audit)
        self.assertIn("distinct skills across", text)
        self.assertIn("in this repo", text)


if __name__ == "__main__":
    unittest.main()
