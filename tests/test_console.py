"""The operator console: one page, served by the runtime API, on the loopback interface.

The Control Center proper is a Next/Cloudflare app that authenticates through OpenAI Sites
headers, so it cannot run on an operator's own machine. This page is the local surface for
the same three human decisions. What matters is that it adds no authority: it is off unless
a human is named for it, it answers nothing but loopback, and its token is the one the admin
CLI already issues.
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


class Console(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
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
        self.temporary.cleanup()

    def get(self, path):
        request = urllib.request.Request(self.base + path)
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, dict(response.headers), response.read()

    def test_the_page_is_served_with_a_nonce_that_matches_its_policy(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        page = body.decode("utf-8")
        self.assertNotIn("__NONCE__", page, "the placeholder must be replaced")
        policy = headers["Content-Security-Policy"]
        nonce = policy.split("script-src 'nonce-", 1)[1].split("'", 1)[0]
        self.assertEqual(page.count(f'nonce="{nonce}"'), 2, "one style tag and one script tag")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)

    def test_each_request_gets_a_fresh_nonce(self):
        first = self.get("/")[1]["Content-Security-Policy"]
        second = self.get("/")[1]["Content-Security-Policy"]
        self.assertNotEqual(first, second)

    def test_the_token_is_the_one_the_cli_issues_and_carries_no_extra_authority(self):
        status, _, body = self.get("/v1/console/token")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        principal = self.server.tokens.verify(payload["token"])
        self.assertEqual((principal.actor_id, principal.org_id, principal.actor_type),
                         ("human-owner", "acme", "human"))
        self.assertEqual(sorted(principal.roles), ["decision-owner"])
        # Short enough that a stolen token dies quickly; long enough that the page can
        # renew at half life without a decision ever meeting an expired one.
        self.assertEqual(payload["expires_in"], 600)

    def test_the_console_is_off_unless_a_human_is_named_for_it(self):
        del os.environ["MYORG_CONSOLE_ACTOR"]
        for path in ("/", "/console", "/v1/console/token"):
            status, _, body = self.get(path)
            self.assertEqual(status, 404, path)
            self.assertEqual(json.loads(body)["error"]["code"], "not_found", path)

    def test_a_suspended_organization_cannot_open_the_console(self):
        """B-03. The refusal is the same `not_found` an unknown actor gets, so the route
        still says nothing about which organizations or people exist."""
        self.store.set_organization_status("acme", "suspended")
        status, _, body = self.get("/v1/console/token")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_an_unknown_or_non_human_actor_gets_no_token(self):
        self.store.upsert_actor("acme", "some-agent", "agent", "Agent", ["maker"])
        for actor, expected in (("nobody-at-all", 404), ("some-agent", 200)):
            os.environ["MYORG_CONSOLE_ACTOR"] = actor
            status, _, body = self.get("/v1/console/token")
            self.assertEqual(status, expected, actor)
        # An agent token is issuable, but the service still refuses every human decision
        # made with it -- the console grants nothing `decide_step` does not already check.
        os.environ["MYORG_CONSOLE_ACTOR"] = "some-agent"
        token = json.loads(self.get("/v1/console/token")[2])["token"]
        request = urllib.request.Request(
            self.base + "/v1/decisions/some-run/some-step", method="POST",
            data=json.dumps({"decision": "approve", "reason": "no"}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "X-Request-Id": "console-test-one"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_a_saved_copy_of_the_page_says_so_instead_of_failing_cryptically(self):
        """Opened from disk there is no origin to resolve `/v1/...` against, and the browser
        reports a URL parser error naming neither the cause nor the cure. A copy of this file
        will be opened from disk -- it was -- so the page has to explain itself."""
        page = (ROOT / "runtime" / "console.html").read_text(encoding="utf-8")
        self.assertIn('["http:", "https:"].includes(location.protocol)', page,
                      "the page must decide up front whether it was served")
        self.assertIn("must be opened from the runtime", page)
        self.assertIn("http://127.0.0.1:8080/", page, "the message must name the real URL")
        self.assertIn('el("start").disabled = true', page,
                      "a button that cannot work must not invite a click")

    def test_no_panel_is_left_saying_loading_when_a_load_fails(self):
        """A placeholder that never clears reads as a hang rather than an error."""
        page = (ROOT / "runtime" / "console.html").read_text(encoding="utf-8")
        placeholders = page.count('<div class="empty">loading…</div>')
        self.assertEqual(placeholders, 4, "four panels start with a placeholder")
        self.assertIn('const PANELS = ["decisions", "memory", "ideas", "runs"]', page)
        # Every failure path replaces them.
        self.assertEqual(page.count("stall("), 4,
                         "one definition and one call on each of the three failure paths")

    def test_an_unreachable_api_is_reported_as_a_stopped_server(self):
        page = (ROOT / "runtime" / "console.html").read_text(encoding="utf-8")
        self.assertIn("Cannot reach the runtime API", page)
        self.assertIn("runtime.api` still running?", page)

    def test_the_page_only_calls_routes_the_api_serves(self):
        page = (ROOT / "runtime" / "console.html").read_text(encoding="utf-8")
        api = (ROOT / "runtime" / "api.py").read_text(encoding="utf-8")
        for route in ("/v1/me", "/v1/decisions", "/v1/memory/proposals", "/v1/runs",
                      "/v1/console/token", "/v1/ideas"):
            self.assertIn(f'"{route}"', page, f"{route} is not called by the page")
            self.assertIn(f'"{route}"', api, f"{route} is not served by the API")
        # The API rejects every URL carrying a query string, so no call may build one.
        for call in re.findall(r'(?:fetch|call)\(\s*(?:"[A-Z]+",\s*)?[`"]([^`"]+)[`"]', page):
            self.assertNotIn("?", call, f"{call} carries a query string")


if __name__ == "__main__":
    unittest.main()
