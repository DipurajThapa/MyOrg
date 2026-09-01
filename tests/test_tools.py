"""What a department is allowed to touch, and where.

AGENT-06/EXEC-01 in the REV2 audit: every step was dispatched with `--allowedTools ""`, so
agents could only write prose *about* work. Turning tools on without scoping them would
have handed every department the ability to rewrite the runtime, the audit log and the run
state -- the controls the whole product rests on.

Measured first, then designed (see docs/AUTONOMY-AUDIT-2026-09-01-REV2.md, cycle E):
  * a bare `Read` grant reads anything on the machine, including this repository;
  * `Read(./**)` with `--permission-mode dontAsk` denies anything outside the working
    directory and allows what is inside it;
  * so the working directory is only a boundary when the grant is scoped to it.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from runtime import tools

ROOT = Path(__file__).resolve().parents[1]


class GrantTest(unittest.TestCase):
    def test_every_department_has_a_usable_grant(self) -> None:
        departments = sorted(p.stem for p in (ROOT / ".claude" / "agents").glob("*.md"))
        for role in departments:
            grant = tools.grant_for(role)
            self.assertTrue(grant.tools, f"{role} has no tools at all")
            self.assertTrue(grant.allow, f"{role} has no allow rules")

    def test_an_unknown_role_gets_the_default_grant_not_everything(self) -> None:
        grant = tools.grant_for("not-a-department")
        self.assertEqual(grant.tools, tools.grant_for("cto-engineering").tools)
        self.assertNotIn("Bash", grant.tools)

    # --- containment ----------------------------------------------------------------

    def test_every_file_rule_is_scoped_to_the_working_directory(self) -> None:
        """An unscoped rule reads the whole machine. Measured, not assumed."""
        for role in tools.roles():
            for rule in tools.grant_for(role).allow:
                self.assertIn("(", rule, f"{role}: '{rule}' is unscoped")
                self.assertTrue(rule.split("(", 1)[1].startswith("./"),
                                f"{role}: '{rule}' is not relative to the workspace")

    def test_no_department_may_run_shell_commands(self) -> None:
        """Bash is scoped by command, never by path, so a workspace cannot contain it.

        If a role ever needs to run something, it goes through a connector with its own
        admission control -- not through a shell the runtime cannot bound."""
        for role in tools.roles():
            self.assertNotIn("Bash", tools.grant_for(role).tools, f"{role} may run shell")

    def test_no_department_may_reach_the_network_yet(self) -> None:
        for role in tools.roles():
            granted = set(tools.grant_for(role).tools)
            self.assertEqual(granted & {"WebFetch", "WebSearch", "Task", "Agent"}, set(),
                             f"{role} was granted an ungoverned outward or spawning tool")

    def test_a_broken_grant_file_refuses_rather_than_granting_everything(self) -> None:
        with self.assertRaises(SystemExit):
            tools.load({"version": 2, "default": {"tools": [], "allow": []}})
        with self.assertRaises(SystemExit):
            tools.load({"version": 1, "default": {"tools": ["Read"], "allow": ["Read"]}})
        with self.assertRaises(SystemExit):
            tools.load({"version": 1, "default": {"tools": ["Bash"], "allow": ["Bash(./**)"]},
                        "roles": {}})

    def test_the_shipped_grant_file_is_valid(self) -> None:
        data = json.loads((ROOT / "runtime" / "tools.json").read_text(encoding="utf-8"))
        self.assertEqual(tools.load(data).default.tools, tools.grant_for("cfo-finance").tools)


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        # Own the workspace root, so a test never leaves rooms in the real one.
        import importlib
        import os
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("MYORG_WORKSPACES")
        os.environ["MYORG_WORKSPACES"] = self._tmp.name
        self.addCleanup(self._restore)
        from runtime import tools as module
        globals()["tools"] = importlib.reload(module)
        self.addCleanup(lambda: importlib.reload(module))

    def _restore(self) -> None:
        import os
        if self._previous is None:
            os.environ.pop("MYORG_WORKSPACES", None)
        else:
            os.environ["MYORG_WORKSPACES"] = self._previous

    def test_each_step_gets_its_own_workspace(self) -> None:
        one = tools.workspace("run-a", "step-one")
        two = tools.workspace("run-a", "step-two")
        self.assertNotEqual(one, two)
        self.assertTrue(one.is_dir())

    def test_a_workspace_is_never_the_repository(self) -> None:
        made = tools.workspace("run-b", "step-one")
        self.assertNotEqual(made.resolve(), ROOT)
        self.assertNotIn("runtime\\runs", str(made))
        self.assertNotIn("runtime/runs", str(made).replace("\\", "/"))

    def test_an_invalid_id_cannot_escape_the_workspace_root(self) -> None:
        for bad in ("../..", "a/b", "..", "C:/windows"):
            with self.assertRaises(SystemExit):
                tools.workspace(bad, "step")
            with self.assertRaises(SystemExit):
                tools.workspace("run-c", bad)

    def test_what_the_agent_left_behind_is_listed_and_hashed(self) -> None:
        made = tools.workspace("run-d", "step-one")
        (made / "report.md").write_text("the deliverable", encoding="utf-8")
        (made / "notes").mkdir()
        (made / "notes" / "aside.txt").write_text("working note", encoding="utf-8")
        produced = tools.produced_files(made)
        names = {item["path"] for item in produced}
        self.assertEqual(names, {"report.md", "notes/aside.txt"})
        for item in produced:
            self.assertEqual(len(item["sha256"]), 64)
            self.assertGreater(item["bytes"], 0)

    def test_the_harness_own_files_are_not_reported_as_the_departments_work(self) -> None:
        """Measured live: the CLI writes CLAUDE.md into whatever folder it runs in."""
        made = tools.workspace("run-g", "step-one")
        (made / "CLAUDE.md").write_text("harness memory, not a deliverable", encoding="utf-8")
        (made / ".claude").mkdir()
        (made / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (made / "real-deliverable.md").write_text("the actual work", encoding="utf-8")
        self.assertEqual([item["path"] for item in tools.produced_files(made)],
                         ["real-deliverable.md"])

    def test_the_manifest_reads_as_something_a_person_can_check(self) -> None:
        made = tools.workspace("run-e", "step-one")
        (made / "model.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        rendered = tools.manifest(tools.produced_files(made))
        self.assertIn("model.csv", rendered)
        self.assertIn("sha256", rendered.lower())

    def test_no_files_produced_says_so_plainly(self) -> None:
        made = tools.workspace("run-f", "step-one")
        self.assertEqual(tools.produced_files(made), [])
        self.assertIn("No files", tools.manifest([]))


class BackendFlagsTest(unittest.TestCase):
    """The exact flags the containment depends on. Measured against the CLI in cycle E:
    `Read(./**)` + `--permission-mode dontAsk` refuses anything outside the room, and a
    bare `Read` does not. If these stop being sent, the boundary is gone."""

    def build(self, **fields):
        from unittest.mock import patch
        from runtime.backends import ClaudeCliBackend
        from runtime.prompts import StepRequest
        request = StepRequest(run_id="r", step_id="s", agent="cfo-finance", action="draft",
                              goal="g", brief="b", **fields)
        captured = {}

        class Done:
            returncode = 0
            # The shape the CLI actually returns since spend became measurable: JSON with
            # the deliverable in `result` and what it cost alongside. A fake that still
            # spoke plain text would pass while the real parser broke.
            stdout = json.dumps({"result": "a deliverable", "total_cost_usd": 0.12})
            stderr = ""

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["cwd"] = kwargs.get("cwd")
            return Done()

        with patch("runtime.backends.subprocess.run", fake_run):
            ClaudeCliBackend()(request)
        return captured

    def test_a_granted_step_is_run_in_its_room_with_its_rules(self) -> None:
        room = tools.workspace("run-flags", "step-one")
        grant = tools.grant_for("cfo-finance")
        captured = self.build(workspace=room, grant=grant)
        command = captured["command"]
        self.assertEqual(captured["cwd"], room)
        self.assertIn("--permission-mode", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(command[command.index("--tools") + 1], ",".join(grant.tools))
        for rule in grant.allow:
            self.assertIn(rule, command)

    def test_a_step_with_no_grant_gets_no_tools_at_all(self) -> None:
        command = self.build()["command"]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--allowedTools") + 1], "")

    def test_what_a_dispatch_cost_comes_back_with_what_it_said(self) -> None:
        """A-01 depends on this: an answer that arrived without its price would leave the
        ceiling counting zero forever."""
        from unittest.mock import patch
        from runtime.backends import ClaudeCliBackend
        from runtime.prompts import StepRequest
        request = StepRequest(run_id="r", step_id="s", agent="cfo-finance", action="draft",
                              goal="g", brief="b")

        class Done:
            returncode = 0
            stdout = json.dumps({"result": "a deliverable", "total_cost_usd": 0.12})
            stderr = ""

        with patch("runtime.backends.subprocess.run", lambda *a, **k: Done()):
            answer = ClaudeCliBackend()(request)
        self.assertEqual(answer.strip(), "a deliverable")
        self.assertAlmostEqual(answer.cost_usd, 0.12, places=4)

    # --- the dispatch profile (A-09) --------------------------------------------------

    def test_a_dispatch_never_inherits_the_operators_own_connectors(self) -> None:
        """Containment, not economy. This repository ships no `.mcp.json`, so without this
        flag the only MCP servers a dispatch loads are whatever the person running it has
        connected -- their mail, their calendar, their drive. `tools.json` cannot take those
        away, because MCP tools arrive outside the grant it controls."""
        for captured in (self.build(), self.build(workspace=tools.workspace("run-profile", "step-one"),
                                                  grant=tools.grant_for("cfo-finance"))):
            self.assertIn("--strict-mcp-config", captured["command"])

    def test_a_dispatch_does_not_pay_to_load_skills_it_cannot_invoke(self) -> None:
        """No grant includes `Skill`, so a dispatched step cannot run one. Loading them is
        cost with no capability behind it."""
        self.assertNotIn("Skill", tools.grant_for("cfo-finance").tools)
        self.assertIn("--disable-slash-commands", self.build()["command"])

    def test_the_profile_applies_to_ungranted_calls_too(self) -> None:
        """Grading and briefing get no tools, but they are still dispatches and still
        inherit whatever the operator has connected unless this is set."""
        command = self.build()["command"]
        for flag in ("--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, command)

    def setUp(self) -> None:
        import importlib
        import os
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._previous = os.environ.get("MYORG_WORKSPACES")
        os.environ["MYORG_WORKSPACES"] = self._tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("MYORG_WORKSPACES", self._previous)
                        if self._previous else os.environ.pop("MYORG_WORKSPACES", None))
        from runtime import tools as module
        globals()["tools"] = importlib.reload(module)
        self.addCleanup(lambda: importlib.reload(module))


if __name__ == "__main__":
    unittest.main()
