"""The GitHub operator inbox (NOTIFY-01): the notification-command contract, end to end,
against a stand-in `gh` that records what it was asked and answers like the real one.

No network, no shell. The stand-in is a Python script named by MYORG_GH; it appends every
argv it receives to a log and serves the issue list it has been told to serve.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINK = ROOT / "scripts" / "notify_github.py"

FAKE_GH = r'''
import json, os, sys
from pathlib import Path
here = Path(__file__).parent
(here / "calls.jsonl").open("a", encoding="utf-8").write(json.dumps(sys.argv[1:]) + "\n")
mode = (here / "mode").read_text(encoding="utf-8").strip() if (here / "mode").exists() else "ok"
args = sys.argv[1:]
if mode == "down":
    sys.stderr.write("gh: error connecting to api.github.com\n"); sys.exit(1)
if args[:2] == ["repo", "view"]:
    print("owner/detected"); sys.exit(0)
if args[:2] == ["issue", "list"]:
    print((here / "issues.json").read_text(encoding="utf-8") if (here / "issues.json").exists() else "[]")
    sys.exit(0)
if args[:2] == ["issue", "create"]:
    if mode == "refuse":
        sys.stderr.write("GraphQL: Resource not accessible by integration (createIssue)\n"); sys.exit(1)
    print("https://github.com/owner/repo/issues/42"); sys.exit(0)
if args[:2] in (["issue", "reopen"], ["issue", "comment"]):
    print("ok"); sys.exit(0)
sys.stderr.write("fake gh: unknown command " + " ".join(args) + "\n"); sys.exit(2)
'''


class GitHubSinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.fake = self.dir / "fake_gh.py"
        self.fake.write_text(FAKE_GH, encoding="utf-8")
        self.env = {**os.environ, "MYORG_GH": f'"{sys.executable}" "{self.fake}"',
                    "MYORG_NOTIFY_GITHUB_REPO": "owner/repo"}
        self.env.pop("GH_TOKEN", None)

    def calls(self) -> list[list[str]]:
        path = self.dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def send(self, notice: dict, **env) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SINK), json.dumps(notice)],
                              capture_output=True, text=True, env={**self.env, **env},
                              timeout=60, encoding="utf-8")

    NOTICE = {"id": "needs_approval-run-7-release", "kind": "needs_approval",
              "severity": "blocking", "org_id": "acme",
              "subject": "run-7 is waiting on your decision",
              "detail": "Step release needs a person.\nLine two; with $(dangerous) `text` \"quotes\"",
              "action": "Open the Control Center and approve or reject it.",
              "run_id": "run-7", "step_id": "release", "created_at": "2026-09-03T10:00:00Z"}

    def test_a_new_notice_becomes_one_issue_with_the_id_in_its_body(self):
        result = self.send(self.NOTICE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("created https://github.com/owner/repo/issues/42", result.stdout)
        create = next(c for c in self.calls() if c[:2] == ["issue", "create"])
        self.assertEqual(create[create.index("--repo") + 1], "owner/repo")
        title = create[create.index("--title") + 1]
        body = create[create.index("--body") + 1]
        self.assertEqual(title, "[MyOrg · blocking] run-7 is waiting on your decision")
        # Multiline content and shell-looking text arrive verbatim, as one argument.
        self.assertIn("Line two; with $(dangerous) `text` \"quotes\"", body)
        self.assertIn("`myorg-notice: needs_approval-run-7-release`", body)
        self.assertIn("**Do:** Open the Control Center", body)
        self.assertIn("Run/step: `run-7/release`", body)

    def test_a_retry_of_a_delivered_notice_does_not_make_a_second_issue(self):
        (self.dir / "issues.json").write_text(json.dumps([
            {"number": 42, "state": "OPEN", "url": "https://github.com/owner/repo/issues/42",
             "body": "old text\n\n`myorg-notice: needs_approval-run-7-release`"}]), encoding="utf-8")
        result = self.send(self.NOTICE)
        self.assertEqual(result.returncode, 0, result.stderr)
        kinds = [c[:2] for c in self.calls()]
        self.assertNotIn(["issue", "create"], kinds)
        self.assertIn(["issue", "comment"], kinds)  # the fact came again: say so on the issue
        self.assertNotIn(["issue", "reopen"], kinds)
        self.assertIn("updated https://github.com/owner/repo/issues/42", result.stdout)

    def test_a_closed_issue_is_reopened_when_the_same_notice_comes_back(self):
        (self.dir / "issues.json").write_text(json.dumps([
            {"number": 42, "state": "CLOSED", "url": "u",
             "body": "`myorg-notice: needs_approval-run-7-release`"}]), encoding="utf-8")
        result = self.send(self.NOTICE)
        self.assertEqual(result.returncode, 0, result.stderr)
        kinds = [c[:2] for c in self.calls()]
        self.assertIn(["issue", "reopen"], kinds)
        self.assertIn(["issue", "comment"], kinds)

    def test_a_different_notice_with_a_similar_marker_is_not_mistaken_for_it(self):
        (self.dir / "issues.json").write_text(json.dumps([
            {"number": 1, "state": "OPEN", "url": "u",
             "body": "`myorg-notice: needs_approval-run-7-release-2`"}]), encoding="utf-8")
        self.send(self.NOTICE)
        self.assertIn(["issue", "create"], [c[:2] for c in self.calls()])

    def test_failures_are_non_zero_and_say_why(self):
        (self.dir / "mode").write_text("refuse", encoding="utf-8")
        refused = self.send(self.NOTICE)
        self.assertEqual(refused.returncode, 3)
        self.assertIn("GitHub refused the issue", refused.stderr)
        self.assertIn("Resource not accessible", refused.stderr)

        (self.dir / "mode").write_text("down", encoding="utf-8")
        down = self.send(self.NOTICE)
        self.assertEqual(down.returncode, 3)
        self.assertIn("error connecting", down.stderr)

        (self.dir / "mode").unlink()
        garbage = subprocess.run([sys.executable, str(SINK), "not json"],
                                 capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(garbage.returncode, 2)
        self.assertIn("could not read the notice", garbage.stderr)

    def test_the_repository_is_explicit_in_production_and_detected_only_as_a_fallback(self):
        env = dict(self.env)
        env.pop("MYORG_NOTIFY_GITHUB_REPO")
        result = subprocess.run([sys.executable, str(SINK), json.dumps(self.NOTICE)],
                                capture_output=True, text=True, env=env, timeout=60,
                                encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        create = next(c for c in self.calls() if c[:2] == ["issue", "create"])
        self.assertEqual(create[create.index("--repo") + 1], "owner/detected")

    def test_nothing_in_the_sink_or_its_docs_carries_a_credential(self):
        for path in (SINK, ROOT / "deploy" / "myorg.env.example",
                     ROOT / "docs" / "OPERATIONS-RUNBOOK.md"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"gh[pousr]_[A-Za-z0-9]{20,}", path.name)
            self.assertNotRegex(text, r"github_pat_[A-Za-z0-9_]{20,}", path.name)


if __name__ == "__main__":
    unittest.main()
