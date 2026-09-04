"""The work board: the console's other view, laid out as the stages work actually passes through.

The board is the operational surface -- where is everything, what is stuck, what needs me, and
what can I do about it. So what matters here is not that it looks like a board. It is that
every column is a status the runtime really reports, every button maps to a route the API
really serves, and nothing it offers could put the backend into a state the backend would
refuse. A board that invents a control is worse than no board: it teaches an operator to
expect something the company cannot do.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from runtime.api import create_server
from runtime.db import Store

ROOT = Path(__file__).resolve().parents[1]
SECRET = "0123456789abcdef0123456789abcdef"
PAGE = ROOT / "runtime" / "kanban.html"


class BoardPage(unittest.TestCase):
    """What the page promises, checked against the boundary that has to keep the promise."""

    @classmethod
    def setUpClass(cls):
        cls.page = PAGE.read_text(encoding="utf-8")
        cls.api = "".join(path.read_text(encoding="utf-8")
                          for path in sorted((ROOT / "runtime").glob("api*.py")))

    def test_every_route_the_board_calls_is_one_the_api_serves(self):
        """The rule that keeps the board honest. A call the API does not answer is a button
        that lies, and this catches it before an operator does."""
        literal = set(re.findall(r'"(/v1/[a-z/-]+)"', self.page))
        self.assertTrue(literal, "the page must call the API")
        for route in sorted(literal):
            self.assertIn(f'"{route}"', self.api, f"{route} is called but never served")
        # A route with an id in it is matched by the boundary on its segments, not as a
        # literal string, so it has to be checked the way the boundary actually reads it.
        for prefix in sorted(set(re.findall(r'`/v1/([a-z-]+)/\$\{', self.page))):
            self.assertIn(f'"v1", "{prefix}"', self.api,
                          f"/v1/{prefix}/<id> is called but the boundary routes no such path")

    def test_no_call_carries_a_query_string(self):
        """The boundary rejects any URL with one, so a call that builds one always fails."""
        for call in re.findall(r'call\(\s*"[A-Z]+",\s*[`"]([^`"]+)[`"]', self.page):
            self.assertNotIn("?", call, f"{call} carries a query string")

    def test_every_write_the_board_makes_is_a_documented_route(self):
        """Read paths are forgiving; a write that misses is a failed action in someone's
        face. These are listed by hand so adding one is a deliberate act."""
        for route, verb in (("/v1/ideas", "POST"), ("/v1/ideas/${", "POST"),
                            ("/v1/runs/${", "POST"), ("/v1/decisions/${", "POST"),
                            ("/v1/approvals/${", "POST"), ("/v1/memory/${", "POST")):
            self.assertIn(route, self.page, f"{verb} {route} is not wired up")

    def test_status_is_never_carried_by_colour_alone(self):
        """Every state chip pairs a glyph with a word, so the board reads the same to
        somebody who cannot tell the colours apart."""
        tags = re.search(r"const TAG = \{(.*?)\n\};", self.page, re.S)
        self.assertIsNotNone(tags, "the page must define its status vocabulary in one place")
        entries = re.findall(r'\{\s*g:\s*"([^"]+)",\s*word:\s*"([^"]+)",\s*tone:\s*"([^"]+)"',
                             tags.group(1))
        self.assertGreaterEqual(len(entries), 6)
        for glyph, word, tone in entries:
            self.assertTrue(glyph.strip(), "a status needs a glyph, not only a colour")
            self.assertTrue(word.strip(), "a status needs a word, not only a colour")
            self.assertIn(tone, {"bad", "warn", "ok", "live", "idle"})

    def test_a_move_is_only_offered_where_a_real_operation_exists(self):
        """Dragging must never be the decision. Only an action that names a `drop` column
        can be reached by dragging, every one of those needs a stated reason, and the drop
        opens the card armed instead of firing -- so no gesture can commit anything."""
        self.assertIn("function legalMove", self.page)
        self.assertIn("openDrawer(card, action.label)", self.page,
                      "a legal drop must arm the action, never run it")
        for block in re.findall(r"drop: \"(\w+)\"[^}]*", self.page):
            self.assertIn(block, {"working", "stopped"},
                          "a person can only decide, stop or withdraw")
        armed = re.findall(r"\{ label: \"([^\"]+)\"[^}]*?drop: \"", self.page)
        self.assertTrue(armed)
        for label in armed:
            pattern = rf'label: "{re.escape(label)}"[^}}]*?needs: "'
            self.assertRegex(self.page, pattern, f"“{label}” is draggable but takes no reason")

    def test_the_board_says_what_the_backend_cannot_do(self):
        """Where a person would reasonably expect a control and there is no route behind it,
        the card has to say so. Retrying a stopped run and deleting a run are both real
        wants with no operation; a silent absence reads as an oversight."""
        self.assertIn("There is no retry for a stopped run", self.page)
        self.assertIn("Runs are never deleted", self.page)
        self.assertIn("This creates a new request", self.page,
                      "the fix path must not pretend it repaired the original")

    def test_an_action_cannot_be_submitted_twice(self):
        self.assertIn("inFlight", self.page)
        self.assertIn("if (inFlight.has(key)) return;", self.page)

    def test_the_page_explains_itself_when_it_was_not_served_by_the_runtime(self):
        self.assertIn('["http:", "https:"].includes(location.protocol)', self.page)
        self.assertIn("must be opened from the runtime", self.page)


class BoardServing(unittest.TestCase):
    """The board is the console's other view: same actor, same loopback rule, same policy."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name) / "myorg.db")
        self.store.migrate()
        self.store.bootstrap_organization("acme", "Acme")
        self.store.upsert_actor("acme", "human-owner", "human", "Owner", ["decision-owner"])
        self.server = create_server("127.0.0.1", 0, self.store.path, SECRET)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.previous = {key: os.environ.get(key)
                         for key in ("MYORG_CONSOLE_ACTOR", "MYORG_CONSOLE_ORG")}
        os.environ["MYORG_CONSOLE_ACTOR"] = "human-owner"
        os.environ["MYORG_CONSOLE_ORG"] = "acme"

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        request = urllib.request.Request(self.base + path)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, dict(response.headers), response.read()

    def test_the_board_is_served_under_the_same_nonce_and_policy_as_the_console(self):
        for path in ("/kanban", "/board"):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200, path)
            page = body.decode("utf-8")
            self.assertNotIn("__NONCE__", page, "the placeholder must be replaced")
            policy = headers["Content-Security-Policy"]
            nonce = policy.split("script-src 'nonce-", 1)[1].split("'", 1)[0]
            self.assertIn(f'<style nonce="{nonce}">', page)
            self.assertIn(f'<script nonce="{nonce}">', page)
            self.assertIn("default-src 'none'", policy)
            self.assertIn("frame-ancestors 'none'", policy)

    def test_the_board_is_off_unless_a_console_actor_is_named(self):
        """It carries no authority of its own -- the same switch that turns the console off
        turns this off, because it is the same surface."""
        os.environ.pop("MYORG_CONSOLE_ACTOR", None)
        self.assertEqual(self.get("/kanban")[0], 404)


class RunHistory(unittest.TestCase):
    """`/v1/runs/{id}/events` reads the store's operational events, which are empty for a run
    the executor drove: the run's own history lives in the append-only log and nothing
    exposed it. The board needs it to answer "what was tried before", so it is read-only,
    org-scoped the same way the output is, and capped."""

    def setUp(self):
        import argparse
        import importlib
        from contextlib import redirect_stdout
        import io

        self._tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self._tmp.cleanup)
        keys = ("MYORG_RUNS_DIR", "MYORG_AUDIT_LOG")
        self._previous = {key: os.environ.get(key) for key in keys}
        self.addCleanup(self._restore)
        os.environ["MYORG_RUNS_DIR"] = self._tmp.name
        os.environ["MYORG_AUDIT_LOG"] = str(Path(self._tmp.name) / "_audit-log.jsonl")

        from runtime import company_runtime
        self.core = importlib.reload(company_runtime)
        self.store = Store(Path(self._tmp.name) / "myorg.db")
        self.store.migrate()
        for org in ("acme", "other"):
            self.store.bootstrap_organization(org, org)
            self.store.upsert_actor(org, "owner", "human", "Owner", ["decision-owner"])

        from runtime.auth import TokenService
        from runtime.service import MyOrgService
        self.tokens = TokenService(self.store, SECRET)
        self.service = MyOrgService(self.store)

        workflow = {"version": 1, "id": "wf-hist", "goal": "history probe", "max_cycles": 12,
                    "steps": [{"id": "s1", "owner": "cmo-marketing", "action": "publish",
                               "depends_on": [], "max_attempts": 2}]}
        path = Path(self._tmp.name) / "hist.wf.json"
        path.write_text(json.dumps(workflow), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.core.create_run(argparse.Namespace(
                workflow=str(path), run_id="hist-one", actor="chief-of-staff",
                request_id="create-hist", org="acme"))
            self.core.request_step(argparse.Namespace(
                run_id="hist-one", step="s1", actor="cmo-marketing",
                holder="driver-a", request_id="req-hist"))

    def _restore(self):
        import importlib
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from runtime import company_runtime
        importlib.reload(company_runtime)

    def principal(self, org="acme"):
        return self.tokens.verify(self.tokens.issue(org, "owner"))

    def test_the_history_is_the_runs_own_timeline(self):
        history = self.service.run_history(self.principal(), "hist-one")
        self.assertEqual(history["run_id"], "hist-one")
        self.assertFalse(history["truncated"])
        events = [entry["event"] for entry in history["entries"]]
        self.assertEqual(events, ["run.created", "step.requested"])
        parked = history["entries"][-1]
        self.assertEqual(parked["step"], "s1")
        self.assertEqual(parked["step_status"], "awaiting_approval")
        self.assertEqual(parked["changed"], ["s1"], "it names what moved, not the whole run")
        self.assertTrue(parked["at"] and parked["actor"])

    def test_a_decision_leaves_who_and_why_in_the_history(self):
        """The board answers "what was previously approved" from this, so the approver and
        their stated reason have to survive into it."""
        self.service.decide_step(self.principal(), "hist-one", "s1",
                                 {"decision": "approve", "reason": "checked the copy"},
                                 "decide-hist")
        entries = self.service.run_history(self.principal(), "hist-one")["entries"]
        approved = [e for e in entries if e["event"] == "step.approved"]
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["approver"], "Owner")
        self.assertEqual(approved[0]["approval_ref"], "checked the copy")

    def test_another_organizations_history_reads_like_one_that_does_not_exist(self):
        from runtime.service import ServiceError
        with self.assertRaises(ServiceError):
            self.service.run_history(self.principal("other"), "hist-one")

    def test_a_long_run_is_truncated_from_the_front_not_refused(self):
        """The recent end is the operational one, and a run that has been going for a week
        must still open."""
        history = self.service.run_history(self.principal(), "hist-one")
        self.assertLessEqual(len(history["entries"]), self.service.MAX_HISTORY_EVENTS)
        self.assertEqual(history["total_events"], len(history["entries"]))


if __name__ == "__main__":
    unittest.main()
